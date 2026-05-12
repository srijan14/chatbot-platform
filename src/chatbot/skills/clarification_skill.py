"""Clarification Skill — exposes a synthetic `ask_clarification` tool.

The LLM emits this tool call when it cannot proceed without missing info from
the user. The orchestrator intercepts the call locally (no MCP round-trip) and
short-circuits the loop, surfacing a structured clarification signal on the
chat response. Reusing the `tool_calls` channel means we avoid fragile string
parsing and the parameters get validated provider-side.
"""
from __future__ import annotations

from src.chatbot.skills.base import Skill

TOOL_NAME = "ask_clarification"

_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Use ONLY when you cannot proceed without a missing identifier or "
            "choice from the user (e.g., which plan, which bill, which SIM). "
            "Never call together with another tool in the same turn."
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
                    "enum": [
                        "plan_id",
                        "phone_number",
                        "customer_id",
                        "bill_id",
                        "addon_id",
                        "yes_no",
                        "free_text",
                    ],
                    "description": "Hint about the expected reply type.",
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


class ClarificationSkill(Skill):
    name = "clarification"

    async def prepare_tools(self) -> list[dict]:
        return [_TOOL_SCHEMA]

    def owns_tool(self, name: str) -> bool:
        return name == TOOL_NAME

    async def execute_tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        # The orchestrator short-circuits ask_clarification before reaching here.
        raise RuntimeError(
            f"ClarificationSkill.execute_tool should not be reached (got {name})"
        )
