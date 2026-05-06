"""Tool Call Skill — wraps an MCP client and exposes its tools to the LLM.

This skill produces tool definitions in OpenAI's shape (used by Azure OpenAI).
Switching providers means swapping the translator below; the rest of the pipeline
doesn't care.
"""
from __future__ import annotations

from src.chatbot.skills.base import Skill
from src.chatbot.engines.tool_engine.mcp_client import MCPClient
from src.chatbot.engines.tool_engine.tool_translator import mcp_to_openai


class ToolCallSkill(Skill):
    name = "tool_call"

    def __init__(self, mcp_client: MCPClient, tool_allowlist: list[str] | None = None):
        self.mcp_client = mcp_client
        self.tool_allowlist = tool_allowlist or []
        self._tool_names: set[str] = set()

    async def prepare_tools(self) -> list[dict]:
        mcp_tools = await self.mcp_client.list_tools()
        openai_tools = mcp_to_openai(mcp_tools, self.tool_allowlist or None)
        self._tool_names = {t["function"]["name"] for t in openai_tools}
        return openai_tools

    def owns_tool(self, name: str) -> bool:
        return name in self._tool_names

    async def execute_tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        return await self.mcp_client.call_tool(name, arguments)
