"""Notion connector.

config:
  database_id:  optional — if set, lists all pages in that database
  page_id:      optional — single-page ingestion
  token_env:    "NOTION_API_TOKEN" (default)
  api_version:  "2022-06-28" (default — Notion requires this header)

If both `database_id` and `page_id` are set, `database_id` wins. Page content
is fetched as block children and flattened to plain text (concatenated rich
text segments per block). No SDK dependency — Notion's REST API is small
enough to call directly with httpx.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

import httpx

from rag_engine.connectors.base import DocRef, SourceConnector
from rag_engine.models import Document, doc_id_for

NOTION_API = "https://api.notion.com/v1"


def _rich_text_to_str(rich: list) -> str:
    return "".join(part.get("plain_text", "") for part in (rich or []))


def _block_to_text(block: dict) -> str:
    t = block.get("type")
    if not t:
        return ""
    payload = block.get(t) or {}
    rich = payload.get("rich_text") or []
    line = _rich_text_to_str(rich)
    # Bullet/numbered prefix for readability
    if t == "bulleted_list_item":
        return f"- {line}" if line else ""
    if t == "numbered_list_item":
        return f"1. {line}" if line else ""
    if t == "heading_1":
        return f"# {line}" if line else ""
    if t == "heading_2":
        return f"## {line}" if line else ""
    if t == "heading_3":
        return f"### {line}" if line else ""
    if t == "to_do":
        checked = "x" if payload.get("checked") else " "
        return f"[{checked}] {line}" if line else ""
    return line


class NotionConnector(SourceConnector):
    connector_name = "notion"

    def __init__(self, config: dict):
        self.database_id = config.get("database_id")
        self.page_id = config.get("page_id")
        if not self.database_id and not self.page_id:
            raise ValueError("notion connector requires `database_id` or `page_id`")
        token = os.getenv(config.get("token_env", "NOTION_API_TOKEN"))
        if not token:
            raise ValueError("NOTION_API_TOKEN env var is required")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": config.get("api_version", "2022-06-28"),
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=NOTION_API, headers=self._headers, timeout=30.0)

    async def list_documents(self) -> AsyncIterator[DocRef]:
        if self.database_id:
            async with self._client() as c:
                cursor: str | None = None
                while True:
                    body: dict = {"page_size": 50}
                    if cursor:
                        body["start_cursor"] = cursor
                    r = await c.post(f"/databases/{self.database_id}/query", json=body)
                    r.raise_for_status()
                    data = r.json()
                    for page in data.get("results", []):
                        page_id = page["id"]
                        title = self._page_title(page) or page_id
                        yield DocRef(
                            source_uri=page.get("url", f"notion://page/{page_id}"),
                            mime_type="text/plain",
                            metadata={"title": title, "page_id": page_id,
                                      "database_id": self.database_id},
                        )
                    if not data.get("has_more"):
                        break
                    cursor = data.get("next_cursor")
        else:
            # single page
            yield DocRef(
                source_uri=f"notion://page/{self.page_id}",
                mime_type="text/plain",
                metadata={"page_id": self.page_id},
            )

    async def fetch_document(
        self, ref: DocRef, tenant_id: str, collection: str
    ) -> Document:
        page_id = ref.metadata.get("page_id")
        if not page_id:
            raise ValueError("notion ref missing page_id")
        async with self._client() as c:
            blocks_text: list[str] = []
            cursor: str | None = None
            while True:
                params: dict = {"page_size": 100}
                if cursor:
                    params["start_cursor"] = cursor
                r = await c.get(f"/blocks/{page_id}/children", params=params)
                r.raise_for_status()
                data = r.json()
                for block in data.get("results", []):
                    line = _block_to_text(block)
                    if line:
                        blocks_text.append(line)
                if not data.get("has_more"):
                    break
                cursor = data.get("next_cursor")
        return Document(
            doc_id=doc_id_for(ref.source_uri),
            source_uri=ref.source_uri,
            content="\n\n".join(blocks_text),
            mime_type="text/plain",
            tenant_id=tenant_id,
            collection=collection,
            metadata={k: v for k, v in ref.metadata.items() if v is not None},
        )

    @staticmethod
    def _page_title(page: dict) -> str:
        # Title properties live under "properties" with type "title" — the
        # property NAME varies per database, so scan for the first title-typed
        # property.
        for prop in (page.get("properties") or {}).values():
            if prop.get("type") == "title":
                return _rich_text_to_str(prop.get("title") or [])
        return ""
