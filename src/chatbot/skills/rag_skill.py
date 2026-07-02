"""RAG Skill — exposes in-process knowledge-base search to the LLM.

The platform owns RAG directly: this skill calls the `rag_engine` library's
`RagEngine` in-process (no MCP/REST hop). Each bot is scoped to its own
collection via `tenant_id` (= bot_id) + logical `collection`; tenant isolation
is enforced inside the engine's retriever. Architecturally this now mirrors
`TagSkill` — an inline tool schema plus a direct engine call — rather than the
old MCP-client wrapper.
"""
from __future__ import annotations

import logging
from typing import Any

from rag_engine import RagEngine
from rag_engine.models import SearchResult

from src.chatbot.skills.base import Skill, ToolResult

log = logging.getLogger("chatbot.rag")

SEARCH_TOOL = "search_knowledge_base"
LIST_TOOL = "list_collections"

_DEFAULT_INSTRUCTIONS = (
    "When a user asks about policies, FAQs, documentation, eligibility, "
    "fair-usage rules, refund/cancellation windows, or any 'how does X work' "
    "question, call `search_knowledge_base` BEFORE answering and ground your "
    "reply in the passages it returns. Cite sources inline using the `[N]` "
    "markers from the returned text. When a passage includes a link in angle "
    "brackets `<...>`, include that link with its citation so the user can open "
    "the source document. Prefer the knowledge base over guessing; prefer domain "
    "action tools (account, billing, etc.) when the question is about a specific "
    "customer record."
)


def _format_results(results: list[SearchResult]) -> str:
    """Render passages as `[N] (source_uri[heading]) <url>\\n<chunk>` so the model
    can cite sources (and their download link) without parsing JSON. `<url>` is a
    presigned link to the original document when the blob backend provides one.
    """
    if not results:
        return "No relevant passages found."
    lines: list[str] = []
    for i, r in enumerate(results, start=1):
        heading = (r.metadata or {}).get("heading")
        head = f" [{heading}]" if heading else ""
        url = (r.metadata or {}).get("source_url")
        link = f" <{url}>" if url else ""
        lines.append(f"[{i}] ({r.source_uri}{head}){link}\n{r.text}")
    return "\n\n".join(lines)


def _sources_from(results: list[SearchResult]) -> list[dict]:
    """Structured source references for the chat response: one per retrieved
    passage, carrying the source document id, a display title, and a link
    (presigned URL when the blob backend provides one)."""
    sources: list[dict] = []
    for r in results:
        md = r.metadata or {}
        sources.append({
            "document_id": r.source_uri,
            "title": md.get("heading") or md.get("filename"),
            "url": md.get("source_url"),
        })
    return sources


class RagSkill(Skill):
    name = "rag"

    def __init__(
        self,
        engine: RagEngine,
        tenant_id: str,
        collection: str,
        top_k: int = 5,
        search_instructions: str | None = None,
    ):
        self.engine = engine
        self.tenant_id = tenant_id
        self.collection = collection
        self.top_k = top_k
        self._search_instructions = search_instructions or _DEFAULT_INSTRUCTIONS

    async def prepare_tools(self) -> list[dict]:
        # Inline schema (mirrors TagSkill). `collection`/`tenant` are NOT model
        # inputs — the skill injects them so the prompt stays terse and a bot
        # can never search another bot's collection.
        return [
            {
                "type": "function",
                "function": {
                    "name": SEARCH_TOOL,
                    "description": (
                        "Search this bot's knowledge base for passages relevant "
                        "to a natural-language query. Use for policy, FAQ, "
                        "documentation, or 'how does X work' questions. Returns "
                        "passages each tagged with a [N] citation marker and its "
                        "source; cite them inline in your reply."
                    ),
                    "parameters": {
                        "type": "object",
                        "required": ["query"],
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Natural-language question to search for.",
                            },
                            "top_k": {
                                "type": "integer",
                                "description": "Max passages to return (default 5).",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": LIST_TOOL,
                    "description": (
                        "List the knowledge-base collections available to this "
                        "bot. Use for discovery when unsure what can be searched."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    def owns_tool(self, name: str) -> bool:
        return name in (SEARCH_TOOL, LIST_TOOL)

    async def execute_tool(self, name: str, arguments: dict) -> ToolResult:
        if name == SEARCH_TOOL:
            query = (arguments.get("query") or "").strip()
            if not query:
                return ToolResult(text="No query provided.", is_error=True)
            top_k = int(arguments.get("top_k") or self.top_k)
            filters: dict[str, Any] | None = arguments.get("filters")
            try:
                results = await self.engine.search(
                    query=query,
                    collection=self.collection,
                    tenant_id=self.tenant_id,
                    top_k=top_k,
                    filters=filters,
                )
            except Exception as exc:
                # Never let a retrieval failure (missing collection, embedding
                # endpoint down, vector-store error, …) propagate out of the
                # tool: a raised exception can crash the agent turn and leave an
                # orphaned tool_call in the checkpoint, which then 400s every
                # later turn. Always return a ToolResult so the model gets a
                # clean error message and can apologise instead.
                log.warning(
                    "[rag] search failed collection=%s tenant=%s: %s: %s",
                    self.collection, self.tenant_id, type(exc).__name__, exc,
                )
                return ToolResult(
                    text=(
                        f"Knowledge base search failed "
                        f"({type(exc).__name__}: {exc})."
                    ),
                    is_error=True,
                )
            log.info(
                "[rag] search collection=%s tenant=%s top_k=%d hits=%d",
                self.collection, self.tenant_id, top_k, len(results),
            )
            return ToolResult(
                text=_format_results(results),
                sources=_sources_from(results),
            )

        if name == LIST_TOOL:
            try:
                specs = await self.engine.list_collections(self.tenant_id)
            except Exception as exc:
                log.warning("[rag] list_collections failed: %s", exc)
                return ToolResult(
                    text=f"Could not list collections ({type(exc).__name__}: {exc}).",
                    is_error=True,
                )
            if not specs:
                return ToolResult(text="No collections available.")
            lines = [f"- {s.name}: {s.description or ''}".rstrip() for s in specs]
            return ToolResult(text="\n".join(lines))

        return ToolResult(text=f"Unknown tool: {name}", is_error=True)

    def system_prompt_addition(self) -> str | None:
        return self._search_instructions
