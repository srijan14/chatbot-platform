"""VectorStore Protocol — the swap boundary for the storage backend.

Default: MilvusVectorStore. Swapping to pgvector / Qdrant / Weaviate is a
one-file change — no caller touches anything but this Protocol. It stays
minimal: anything fancy (HNSW tuning,
hybrid search) goes behind implementation-specific config, not into this API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class UpsertItem:
    id: str
    embedding: list[float]
    document: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryHit:
    id: str
    document: str
    metadata: dict[str, Any]
    distance: float                  # smaller = closer; backend-defined units


@runtime_checkable
class VectorStore(Protocol):
    async def create_collection(
        self, name: str, dimensions: int, metadata: dict[str, Any] | None = None
    ) -> None: ...

    async def list_collections(self) -> list[str]: ...

    async def drop_collection(self, name: str) -> None: ...

    async def upsert(self, collection: str, items: list[UpsertItem]) -> None: ...

    async def query(
        self,
        collection: str,
        query_embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[QueryHit]: ...

    async def delete_by_filter(self, collection: str, where: dict[str, Any]) -> int: ...
