"""Clarification Skill — domain-agnostic 'ask the user for missing info'.

The LLM emits this tool call when it cannot proceed without missing info from
the user. The orchestrator intercepts the call locally (no MCP round-trip) and
short-circuits the loop, surfacing a structured clarification signal on the
chat response. Reusing the `tool_calls` channel means we avoid fragile string
parsing and the parameters get validated provider-side.

The schema (expected-reply enum, description hint, suggested-reply cap) is
driven by per-bot config so a telecom bot can constrain `expected` to
`plan_id|bill_id|...` while a BI or RAG bot picks its own vocabulary — no
domain knowledge lives in this module.
"""
from __future__ import annotations

from src.chatbot.skills.base import Skill, ToolResult, TurnSignal

TOOL_NAME = "ask_clarification"

DEFAULT_DESCRIPTION = (
    "Use ONLY when you cannot proceed without missing information from the "
    "user (a missing identifier, an ambiguous choice, a yes/no confirmation). "
    "Never call together with another tool in the same turn."
)

# Platform-generic reply-type vocabulary. Bots layer domain tokens on top via
# their `clarification.expected_types` (or legacy `expected_values`) YAML key.
DEFAULT_EXPECTED_VALUES = [
    "free_text",
    "yes_no",
    "single_choice",
    "multi_choice",
    "numeric",
]

DEFAULT_MAX_SUGGESTED_REPLIES = 4

# Concatenated into the system prompt by the orchestrator so every bot that
# enables this skill learns the same calling convention without each YAML
# repeating it.
_SYSTEM_PROMPT_RULE = (
    "When the user's request is ambiguous or is missing a critical identifier, "
    "do NOT guess. Call the `ask_clarification` tool with a short concrete "
    "question, an `expected` reply-type hint, and when appropriate up to "
    "a few `suggested_replies` the user can pick from. Never call "
    "`ask_clarification` together with another tool in the same turn."
)


class ClarificationSkill(Skill):
    name = "clarification"

    def __init__(
        self,
        expected_types: list[str] | None = None,
        description: str | None = None,
        max_suggested_replies: int = DEFAULT_MAX_SUGGESTED_REPLIES,
    ):
        self.expected_types = list(expected_types) if expected_types else None
        self.description = description or DEFAULT_DESCRIPTION
        self.max_suggested_replies = max_suggested_replies

    async def prepare_tools(self) -> list[dict]:
        expected_schema: dict = {
            "type": "string",
            "description": "Hint about the expected reply type.",
        }
        # Only constrain to an enum when the bot config supplied one — otherwise
        # leave `expected` as free-form so generic bots aren't boxed in.
        if self.expected_types:
            expected_schema["enum"] = list(self.expected_types)

        return [{
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "required": ["question"],
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Short concrete question for the user.",
                        },
                        "expected": expected_schema,
                        "suggested_replies": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": self.max_suggested_replies,
                            "description": (
                                f"Up to {self.max_suggested_replies} short "
                                f"suggested replies for quick-reply chips."
                            ),
                        },
                    },
                },
            },
        }]

    def owns_tool(self, name: str) -> bool:
        return name == TOOL_NAME

    def system_prompt_addition(self) -> str | None:
        return _SYSTEM_PROMPT_RULE

    async def execute_tool(self, name: str, arguments: dict) -> ToolResult:
        """Emit a terminal 'clarification' signal. The orchestrator dispatches
        every tool uniformly; the skill alone decides the loop pauses here."""
        question = arguments.get("question", "Could you clarify?")
        expected = arguments.get("expected", "free_text")
        suggested_replies = list(arguments.get("suggested_replies") or [])
        return ToolResult(
            # Goes into LLM history so the next-turn replay matches the
            # tool_call_id pairing rule OpenAI enforces.
            text="(awaiting user response)",
            # Overrides the chat response's `text` field because the assistant
            # message had content=None — the question lives only in args.
            user_visible_text=question,
            signal=TurnSignal(
                type="clarification",
                payload={
                    "question": question,
                    "expected": expected,
                    "suggested_replies": suggested_replies,
                },
            ),
            terminal=True,
        )
