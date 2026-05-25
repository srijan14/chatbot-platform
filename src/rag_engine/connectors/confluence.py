"""Confluence Cloud connector.

config:
  base_url:    "https://YOUR-SITE.atlassian.net/wiki"
  space_key:   "TELCO"                 # required
  cql:         "type=page"             # optional override
  user_email_env: "CONFLUENCE_USER_EMAIL"   # default
  token_env:      "CONFLUENCE_API_TOKEN"    # default

Pages are emitted as Documents whose body is the rendered storage HTML
flattened to plain text (no HTML parser dependency — Confluence's storage
format is XHTML and a regex strip is good enough for retrieval).

Auth: HTTP Basic with `email:api_token`, per Atlassian Cloud's REST docs.
"""
from __future__ import annotations

import os
import re
from typing import AsyncIterator

import httpx

from rag_engine.connectors.base import DocRef, SourceConnector
from rag_engine.models import Document, doc_id_for

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+\n")


def _html_to_text(html: str) -> str:
    txt = _TAG_RE.sub("", html)
    txt = _WHITESPACE_RE.sub("\n", txt)
    return txt.strip()


class ConfluenceConnector(SourceConnector):
    connector_name = "confluence"

    def __init__(self, config: dict):
        self.base_url = (config.get("base_url") or "").rstrip("/")
        self.space_key = config.get("space_key")
        if not self.base_url or not self.space_key:
            raise ValueError("confluence connector requires base_url and space_key")
        self.cql = config.get("cql") or f'space="{self.space_key}" AND type=page'
        self.page_limit = int(config.get("page_limit", 50))

        email = os.getenv(config.get("user_email_env", "CONFLUENCE_USER_EMAIL"))
        token = os.getenv(config.get("token_env", "CONFLUENCE_API_TOKEN"))
        if not email or not token:
            raise ValueError(
                "confluence connector requires CONFLUENCE_USER_EMAIL + "
                "CONFLUENCE_API_TOKEN env vars (or the overrides in config)"
            )
        self._auth = (email, token)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, auth=self._auth, timeout=30.0)

    async def list_documents(self) -> AsyncIterator[DocRef]:
        # Paginated CQL search. Cursor-based via `cursor` returned in body.
        async with self._client() as c:
            cursor: str | None = None
            while True:
                params: dict = {"cql": self.cql, "limit": self.page_limit}
                if cursor:
                    params["cursor"] = cursor
                r = await c.get("/rest/api/search", params=params)
                r.raise_for_status()
                body = r.json()
                for result in body.get("results", []):
                    content = result.get("content") or {}
                    page_id = content.get("id") or result.get("id")
                    title = content.get("title") or result.get("title", "untitled")
                    if not page_id:
                        continue
                    uri = f"{self.base_url}/pages/{page_id}"
                    yield DocRef(
                        source_uri=uri,
                        mime_type="text/plain",
                        metadata={"title": title, "page_id": page_id, "space": self.space_key},
                    )
                cursor = (body.get("_links") or {}).get("next")
                if not cursor:
                    break

    async def fetch_document(
        self, ref: DocRef, tenant_id: str, collection: str
    ) -> Document:
        page_id = ref.metadata.get("page_id")
        if not page_id:
            raise ValueError(f"confluence ref missing page_id: {ref}")
        async with self._client() as c:
            r = await c.get(f"/rest/api/content/{page_id}",
                            params={"expand": "body.storage,version"})
            r.raise_for_status()
            data = r.json()
        body = ((data.get("body") or {}).get("storage") or {}).get("value", "")
        text = _html_to_text(body)
        version = (data.get("version") or {}).get("number")
        return Document(
            doc_id=doc_id_for(ref.source_uri),
            source_uri=ref.source_uri,
            content=text,
            mime_type="text/plain",
            tenant_id=tenant_id,
            collection=collection,
            metadata={
                "title": ref.metadata.get("title"),
                "page_id": page_id,
                "version": version,
                "space": self.space_key,
                **{k: v for k, v in ref.metadata.items() if k not in {"title", "page_id"}},
            },
        )
