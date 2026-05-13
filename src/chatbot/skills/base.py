"""Base Skill class. Every pluggable skill (tool_call, clarification, rag, ...) extends this."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Skill(ABC):
    name: str

    @abstractmethod
    async def prepare_tools(self) -> list[dict]:
        """Return the OpenAI-shaped tool definitions this skill contributes."""

    @abstractmethod
    async def execute_tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        """Execute a tool call. Returns (text_result, is_error)."""

    @abstractmethod
    def owns_tool(self, name: str) -> bool:
        """Whether this skill can handle the named tool."""

    def system_prompt_addition(self) -> str | None:
        """Optional: text the skill contributes to the system prompt at runtime.

        The orchestrator concatenates non-None additions from every enabled
        skill into the system prompt before each LLM call. This is the
        platform's way to let a skill teach the model how to invoke it,
        without each bot's YAML having to repeat the same boilerplate.
        """
        return None
