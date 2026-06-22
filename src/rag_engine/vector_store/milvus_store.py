"""Milvus-backed VectorStore.

The platform's production vector backend. Implements the same `VectorStore`
Protocol as every other backend, so callers (Retriever, IngestionPipeline,
RagEngine) are unaware of which store is wired in.

pymilvus' `MilvusClient` is synchronous, so — exactly like the old Chroma
backend — we wrap each call in `asyncio.to_thread` to keep the engine `async`.
That's the pragmatic move; if we later adopt pymilvus' `AsyncMilvusClient` the
thread hops can be deleted without touching callers.

Deployment:
  * Production — point `MILVUS_URI` at a real Milvus cluster
    (e.g. `http://milvus:19530`), with `MILVUS_TOKEN` for auth.
  * Local/dev/tests — the default `MILVUS_URI` is a local file path, which
    pymilvus serves via the embedded **Milvus Lite** engine (no server to run).

Schema (one Milvus collection per bot collection, physical name
`{tenant_id}__{logical}`):
  * `id`        VARCHAR primary key (the chunk id)
  * `vector`    FLOAT_VECTOR (dim pinned at create time)
  * `document`  VARCHAR — the chunk text
  * `metadata`  JSON — arbitrary chunk metadata (tenant_id, doc_id, source_uri…)

Equality filters arrive as a plain `{key: value}` dict and are compiled to a
Milvus boolean expression over the JSON `metadata` field
(`metadata["tenant_id"] == "t1"`). We use the **L2** metric so the distance
units match what `Retriever` expects (smaller = closer; it maps L2 → a
"higher = better" similarity).
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from pymilvus import DataType, MilvusClient

from rag_engine.vector_store.base import QueryHit, UpsertItem, VectorStore

# Milvus' hard VARCHAR ceiling. Chunks are far smaller than this in practice,
# but we size to the max so a long chunk never overflows the column.
_MAX_VARCHAR = 65535
_METRIC = "L2"


def _literal(value: Any) -> str:
    """Render a Python value as a Milvus expression literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # Strings (and anything else) — JSON-encode so quotes/backslashes escape.
    return json.dumps(str(value))


def _build_filter(where: dict[str, Any] | None) -> str:
    """Compile a simple equality `where` dict into a Milvus boolean expression.

    Every key is matched against the JSON `metadata` field, AND-ed together.
    An empty/None filter yields the empty string (Milvus treats that as
    "no filter").
    """
    if not where:
        return ""
    return " and ".join(
        f'metadata["{key}"] == {_literal(val)}' for key, val in where.items()
    )


class MilvusVectorStore(VectorStore):
    def __init__(self, uri: str | None = None, token: str | None = None):
        # Default to a local file → pymilvus runs Milvus Lite embedded, mirroring
        # the old Chroma PersistentClient ergonomics for dev/tests. Production
        # overrides MILVUS_URI with a real cluster endpoint.
        uri = uri or os.getenv("MILVUS_URI", "./data/milvus.db")
        token = token if token is not None else os.getenv("MILVUS_TOKEN", "")
        self._client = MilvusClient(uri=uri, token=token or "")

    # --- collections ----------------------------------------------------
    async def create_collection(
        self, name: str, dimensions: int, metadata: dict[str, Any] | None = None
    ) -> None:
        await asyncio.to_thread(self._create_collection_sync, name, dimensions, metadata)

    def _create_collection_sync(
        self, name: str, dimensions: int, metadata: dict[str, Any] | None
    ) -> None:
        # Idempotent (get-or-create): the engine calls this on every startup.
        if self._client.has_collection(name):
            # Ensure it's queryable after a process restart.
            self._client.load_collection(name)
            return

        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=512)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dimensions)
        schema.add_field("document", DataType.VARCHAR, max_length=_MAX_VARCHAR)
        schema.add_field("metadata", DataType.JSON)

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="vector", index_type="AUTOINDEX", metric_type=_METRIC
        )

        # Record the embedding model + dims on the collection description so an
        # operator can audit "what was this indexed with" without the SQL table.
        desc = json.dumps({"dimensions": dimensions, **(metadata or {})})
        self._client.create_collection(
            collection_name=name,
            schema=schema,
            index_params=index_params,
            description=desc[:255],
        )
        # create_collection auto-loads, but be explicit so search works at once.
        self._client.load_collection(name)

    async def list_collections(self) -> list[str]:
        return await asyncio.to_thread(self._client.list_collections)

    async def drop_collection(self, name: str) -> None:
        await asyncio.to_thread(self._client.drop_collection, name)

    # --- vectors --------------------------------------------------------
    async def upsert(self, collection: str, items: list[UpsertItem]) -> None:
        if not items:
            return
        rows = [
            {
                "id": i.id,
                "vector": i.embedding,
                "document": i.document,
                "metadata": i.metadata or {},
            }
            for i in items
        ]
        await asyncio.to_thread(
            self._client.upsert, collection_name=collection, data=rows
        )

    async def query(
        self,
        collection: str,
        query_embedding: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[QueryHit]:
        res = await asyncio.to_thread(
            self._client.search,
            collection_name=collection,
            data=[query_embedding],
            anns_field="vector",
            limit=top_k,
            filter=_build_filter(where),
            output_fields=["document", "metadata"],
            search_params={"metric_type": _METRIC},
        )
        # MilvusClient.search returns one result list per query vector.
        hits = res[0] if res else []
        out: list[QueryHit] = []
        for h in hits:
            entity = h.get("entity", {}) or {}
            out.append(
                QueryHit(
                    id=str(h.get("id")),
                    document=entity.get("document", "") or "",
                    metadata=entity.get("metadata") or {},
                    distance=float(h.get("distance", 0.0)),
                )
            )
        return out

    async def delete_by_filter(self, collection: str, where: dict[str, Any]) -> int:
        expr = _build_filter(where)
        if not expr:
            return 0
        # Count first (Milvus delete doesn't reliably return a count across
        # backends), then delete by primary key — mirrors the Chroma backend.
        existing = await asyncio.to_thread(
            self._client.query,
            collection_name=collection,
            filter=expr,
            output_fields=["id"],
        )
        ids = [row["id"] for row in existing]
        if ids:
            await asyncio.to_thread(
                self._client.delete, collection_name=collection, ids=ids
            )
        return len(ids)
