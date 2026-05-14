"""Clarification Skill — domain-agnostic 'ask the user for missing info'.

Exposes a synthetic `ask_clarification` tool that the LLM calls when it
cannot proceed. The orchestrator intercepts that call locally (no MCP round
trip) and surfaces a structured signal on the chat response.

Generic by design: the `expected` enum defaults to a small UI taxonomy
(free_text, yes_no, single_choice, multi_choice, numeric). Bots can supply
domain-specific tokens via `clarification.expected_values` in their YAML —
e.g. a telecom bot might add `plan_id`, `bill_id`, `addon_id`. Platform
core never names telecom things.

The platform-level instruction "call ask_clarification when ambiguous"
is contributed by `system_prompt_addition()` so every bot that enables
the skill gets it automatically; bot YAMLs only carry domain hints.
"""
from __future__ import annotations

from src.chatbot.skills.base import Skill, ToolResult, TurnSignal

TOOL_NAME = "ask_clarification"

# Generic UI taxonomy. Bots can extend / replace via config.
DEFAULT_EXPECTED_VALUES: list[str] = [
    "free_text",
    "yes_no",
    "single_choice",
    "multi_choice",
    "numeric",
]

_SYSTEM_PROMPT_RULE = (
    "Clarification policy: if the user's request is ambiguous or missing "
    "required information, call the `ask_clarification` tool with a short, "
    "concrete question. When useful, include up to 4 `suggested_replies` "
    "(short concrete options the user can pick). Never call "
    "`ask_clarification` together with another tool in the same turn."
)


class ClarificationSkill(Skill):
    name = "clarification"

    def __init__(self, expected_values: list[str] | None = None):
        # `None` or empty → use the generic platform default.
        self.expected_values = list(expected_values) if expected_values else list(DEFAULT_EXPECTED_VALUES)

    def _tool_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": (
                    "Use ONLY when you cannot proceed without missing info "
                    "from the user. Provide a short question and optional "
                    "suggested replies."
                ),
                "parameters": {
                    "type": "object",
                    "required": ["question"],
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Short concrete question for the user.",
                        },
                        "expected": {
                            "type": "string",
                            "enum": self.expected_values,
                            "description": "Hint about the kind of reply expected.",
                        },
                        "suggested_replies": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 4,
                            "description": "Up to 4 short suggested replies for quick-reply chips.",
                        },
                    },
                },
            },
        }

    async def prepare_tools(self) -> list[dict]:
        return [self._tool_schema()]

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
