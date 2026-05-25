"""Chroma-backed VectorStore.

Chroma's Python client is synchronous; we wrap every call in `asyncio.to_thread`
so the rest of the engine can stay `async`. For a local PersistentClient on
SSD this overhead is tiny — the right move once we swap to a real
async-native backend is to delete the thread hops, not to redesign callers.

Collection metadata note: we record the embedding model + dimensions in
collection.metadata so a later operator can audit "what was this indexed with"
without consulting the SQL table.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import chromadb
from chromadb.api import ClientAPI
from chromadb.config import Settings

from rag_engine.vector_store.base import QueryHit, UpsertItem, VectorStore


class ChromaVectorStore(VectorStore):
    def __init__(self, persist_path: str | None = None):
        path = persist_path or os.getenv("RAG_CHROMA_PATH", "./data/chroma")
        # anonymized_telemetry=False — opt out of phone-home for a privacy-conscious
        # platform, and to keep dev logs quiet.
        self._client: ClientAPI = chromadb.PersistentClient(
            path=path,
            settings=Settings(anonymized_telemetry=False),
        )

    # --- collections ----------------------------------------------------
    async def create_collection(
        self, name: str, dimensions: int, metadata: dict[str, Any] | None = None
    ) -> None:
        md = {"dimensions": dimensions, **(metadata or {})}
        await asyncio.to_thread(
            self._client.get_or_create_collection, name=name, metadata=md
        )

    async def list_collections(self) -> list[str]:
        cols = await asyncio.to_thread(self._client.list_collections)
        # chromadb >= 0.5 returns Collection objects (or names in newer versions).
        return [c.name if hasattr(c, "name") else str(c) for c in cols]

    async def drop_collection(self, name: str) -> None:
        await asyncio.to_thread(self._client.delete_collection, name=name)

    # --- vectors --------------------------------------------------------
    async def upsert(self, collection: str, items: list[UpsertItem]) -> None:
        if not items:
            return
        col = await asyncio.to_thread(self._client.get_collection, name=collection)
        await asyncio.to_thread(
            col.upsert,
            ids=[i.id for i in items],
            embeddings=[i.embedding for i in items],
            documents=[i.document for i in items],
            metadatas=[i.metadata for i in items],
        )

    async def query(
        self,
        collection: str,
        query_embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[QueryHit]:
        col = await asyncio.to_thread(self._client.get_collection, name=collection)
        # Chroma's `where` is a Mongo-ish dict; for simple equality filters the
        # plain {key: value} form Just Works (it's normalized internally).
        res = await asyncio.to_thread(
            col.query,
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        return [
            QueryHit(id=i, document=d, metadata=m or {}, distance=float(x))
            for i, d, m, x in zip(ids, docs, metas, dists)
        ]

    async def delete_by_filter(self, collection: str, where: dict[str, Any]) -> int:
        col = await asyncio.to_thread(self._client.get_collection, name=collection)
        # Chroma's delete returns None; count by querying ids matching the filter first.
        existing = await asyncio.to_thread(col.get, where=where, include=[])
        ids = existing.get("ids") or []
        if ids:
            await asyncio.to_thread(col.delete, ids=ids)
        return len(ids)
