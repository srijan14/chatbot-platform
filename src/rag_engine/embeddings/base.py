"""Embedder Protocol — the swap boundary for the embedding model.

Two methods on purpose: documents (batchable, often during ingest) and query
(typically a single string, latency-sensitive). Some providers offer different
endpoints / prompts for the two; the split lets implementations specialize.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    model: str
    dimensions: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...
