"""MCP tool surface for the RAG sub-platform.

We keep this surface deliberately small — every extra tool spends prompt
budget on every turn. Two tools cover the common cases:

  - `search_knowledge_base` — the workhorse; vector search with citations
  - `list_collections`      — discovery helper for multi-collection bots

Returned `text` carries chunks in a `[N] (source_uri) <chunk>` format so the
LLM has the source uri visible without having to parse JSON.
"""
from __future__ import annotations

from typing import Optional

from rag_mcp import rag_client


def _format_results(results: list[dict]) -> str:
    if not results:
        return "No relevant passages found."
    lines: list[str] = []
    for i, r in enumerate(results, start=1):
        heading = r.get("metadata", {}).get("heading")
        head = f" [{heading}]" if heading else ""
        lines.append(f"[{i}] ({r['source_uri']}{head})\n{r['text']}")
    return "\n\n".join(lines)


def register(mcp) -> None:
    @mcp.tool()
    def search_knowledge_base(
        query: str,
        collection: str,
        top_k: int = 5,
        filters: Optional[dict] = None,
    ) -> dict:
        """Search the knowledge base for passages relevant to `query`.

        Use this for policy, FAQ, documentation, "how does X work" or "what
        is the rule for Y" questions. Returns up to `top_k` passages, each
        with its `source_uri` so you can cite it as `[1]`, `[2]`, etc. in
        your reply.

        Args:
          query: Natural-language question.
          collection: Which knowledge base to search (e.g. "telecom_policies").
          top_k: Max passages to return (default 5).
          filters: Optional metadata filter, e.g. {"mime_type":"text/markdown"}.
        """
        resp = rag_client.search(query, collection, top_k=top_k, filters=filters)
        return {
            "collection": resp.get("collection", collection),
            "results": resp.get("results", []),
            "formatted": _format_results(resp.get("results", [])),
        }

    @mcp.tool()
    def list_collections() -> list[dict]:
        """List knowledge-base collections available to this bot's tenant."""
        return rag_client.list_collections()
