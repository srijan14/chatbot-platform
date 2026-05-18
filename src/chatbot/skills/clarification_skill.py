"""Clarification Skill — exposes a synthetic `ask_clarification` tool.

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

from src.chatbot.skills.base import Skill

TOOL_NAME = "ask_clarification"

DEFAULT_DESCRIPTION = (
    "Use ONLY when you cannot proceed without missing information from the "
    "user (a missing identifier, an ambiguous choice, a yes/no confirmation). "
    "Never call together with another tool in the same turn."
)

DEFAULT_MAX_SUGGESTED_REPLIES = 4


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

    async def execute_tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        # The orchestrator short-circuits ask_clarification before reaching here.
        raise RuntimeError(
            f"ClarificationSkill.execute_tool should not be reached (got {name})"
        )
