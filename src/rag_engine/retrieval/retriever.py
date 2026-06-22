"""Retriever — vector kNN with mandatory tenant filtering.

Tenant isolation is enforced twice:
  1. Physical: queries go to `{tenant_id}__{logical}`, the only collection
     name that can ever contain this tenant's vectors.
  2. Metadata: `where={"tenant_id": tenant_id, ...}` is appended to every
     query regardless of what `filters` the caller passed. Defense in depth
     against an operator misconfiguring collection names.
"""
from __future__ import annotations

from typing import Any

from rag_engine.embeddings.base import Embedder
from rag_engine.models import CollectionSpec, SearchResult
from rag_engine.retrieval.reranker import NoOpReranker, Reranker
from rag_engine.vector_store.base import VectorStore


class Retriever:
    def __init__(
        self,
        vector_store: VectorStore,
        embedder: Embedder,
        reranker: Reranker | None = None,
    ):
        self.vstore = vector_store
        self.embedder = embedder
        self.reranker = reranker or NoOpReranker()

    async def search(
        self,
        query: str,
        spec: CollectionSpec,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        qvec = await self.embedder.embed_query(query)
        where: dict[str, Any] = {"tenant_id": spec.tenant_id}
        if filters:
            # Caller-supplied filters merge on top, but `tenant_id` can never be
            # overridden — see test_tenancy.py.
            for k, v in filters.items():
                if k == "tenant_id":
                    continue
                where[k] = v

        hits = await self.vstore.query(
            collection=spec.physical_name(),
            query_embedding=qvec,
            top_k=top_k,
            where=where,
        )
        results = [
            SearchResult(
                chunk_id=h.id,
                doc_id=str(h.metadata.get("doc_id", "")),
                text=h.document,
                # Convert the vector store's L2 distance into a "higher = better" similarity.
                # Exact units don't matter — only the ordering — but humans expect
                # bigger numbers to mean "more relevant".
                score=1.0 / (1.0 + h.distance),
                source_uri=str(h.metadata.get("source_uri", "")),
                metadata=h.metadata,
            )
            for h in hits
        ]
        return await self.reranker.rerank(query, results)
