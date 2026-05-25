"""Thin httpx client over rag_api.

Always sends the `X-Tenant-Id` header sourced from `RAG_TENANT_ID`. One MCP
server == one tenant; multi-tenant fan-out is achieved by running multiple
rag_mcp processes with different `RAG_TENANT_ID` values.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

RAG_API_URL = os.getenv("RAG_API_URL", "http://localhost:8002")
TENANT_ID = os.getenv("RAG_TENANT_ID", "default")

_headers = {"X-Tenant-Id": TENANT_ID}
_client = httpx.Client(base_url=RAG_API_URL, headers=_headers, timeout=30.0)


def _check(resp: httpx.Response) -> Any:
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise RuntimeError(f"rag_api {resp.status_code}: {detail}")
    return resp.json()


def search(query: str, collection: str, top_k: int = 5,
           filters: Optional[dict] = None) -> dict:
    return _check(_client.post("/search", json={
        "query": query,
        "collection": collection,
        "top_k": top_k,
        "filters": filters,
    }))


def list_collections() -> list[dict]:
    return _check(_client.get("/collections"))


def tenant_id() -> str:
    return TENANT_ID
