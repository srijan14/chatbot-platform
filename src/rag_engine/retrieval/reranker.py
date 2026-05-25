"""Reranker Protocol + a NoOp default.

The pluggable seam for cross-encoders (Cohere / bge-reranker / Voyage) that
re-score the top-k after vector kNN. The default does nothing so v1 stays
vector-only — but the engine always calls `.rerank(...)`, so an operator can
swap in a real reranker by config without code changes elsewhere.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from rag_engine.models import SearchResult


@runtime_checkable
class Reranker(Protocol):
    async def rerank(
        self, query: str, results: list[SearchResult]
    ) -> list[SearchResult]: ...


class NoOpReranker(Reranker):
    async def rerank(
        self, query: str, results: list[SearchResult]
    ) -> list[SearchResult]:
        return results
