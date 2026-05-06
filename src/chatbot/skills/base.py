"""Base Skill class. Every pluggable skill (tool_call, rag, tag, web_scrape) extends this."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Skill(ABC):
    name: str

    @abstractmethod
    async def prepare_tools(self) -> list[dict]:
        """Return the Anthropic-shaped tool definitions this skill contributes."""

    @abstractmethod
    async def execute_tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        """Execute a tool call. Returns (text_result, is_error)."""

    @abstractmethod
    def owns_tool(self, name: str) -> bool:
        """Whether this skill can handle the named tool."""
