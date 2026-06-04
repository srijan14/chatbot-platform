"""RAG Skill — exposes the rag_mcp tools (`search_knowledge_base`,
`list_collections`) to the LLM.

Architectural parity with `tool_call_skill.py`: this is a thin wrapper around
an MCP client; the actual retrieval logic lives in the rag_engine library
behind the rag_api + rag_mcp services. Swap the MCP endpoint and you've
swapped knowledge bases — no code change here.
"""
from __future__ import annotations

import logging

from src.chatbot.engines.tool_engine.mcp_client import MCPClient
from src.chatbot.engines.tool_engine.tool_translator import mcp_to_openai
from src.chatbot.skills.base import Skill, ToolResult

log = logging.getLogger("chatbot.rag")

_ALLOWLIST = ["search_knowledge_base", "list_collections"]
_DEFAULT_INSTRUCTIONS = (
    "When a user asks about policies, FAQs, documentation, eligibility, "
    "fair-usage rules, refund/cancellation windows, or any 'how does X work' "
    "question, call `search_knowledge_base` BEFORE answering and ground your "
    "reply in the passages it returns. Cite sources inline using the `[N]` "
    "markers from the returned `formatted` field. Prefer the knowledge base "
    "over guessing; prefer domain action tools (account, billing, etc.) when "
    "the question is about a specific customer record."
)


class RagSkill(Skill):
    name = "rag"

    def __init__(
        self,
        mcp_client: MCPClient,
        default_collection: str,
        top_k: int = 5,
        search_instructions: str | None = None,
    ):
        self.mcp = mcp_client
        self.default_collection = default_collection
        self.top_k = top_k
        self._search_instructions = search_instructions or _DEFAULT_INSTRUCTIONS
        self._tool_names: set[str] = set()

    async def prepare_tools(self) -> list[dict]:
        mcp_tools = await self.mcp.list_tools()
        openai_tools = mcp_to_openai(mcp_tools, _ALLOWLIST)
        self._tool_names = {t["function"]["name"] for t in openai_tools}
        log.info("[rag] tools prepared: %s", sorted(self._tool_names))
        return openai_tools

    def owns_tool(self, name: str) -> bool:
        return name in self._tool_names

    async def execute_tool(self, name: str, arguments: dict) -> ToolResult:
        # Inject defaults so the prompt stays terse — the model rarely needs
        # to know which collection to search.
        if name == "search_knowledge_base":
            arguments.setdefault("collection", self.default_collection)
            arguments.setdefault("top_k", self.top_k)
        text, is_error = await self.mcp.call_tool(name, arguments)
        return ToolResult(text=text, is_error=is_error)

    def system_prompt_addition(self) -> str | None:
        return self._search_instructions
