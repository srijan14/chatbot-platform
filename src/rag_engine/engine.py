"""RagEngine — the facade callers (REST routes, MCP tools, other apps) hit.

Everything below is wiring. The interesting logic lives in `retrieval/`,
`ingestion/`, `jobs/`. Construct one of these once at service startup and
keep it on app.state.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from rag_engine.chunking.base import Chunker
from rag_engine.connectors.registry import ConnectorRegistry, default_registry
from rag_engine.embeddings.base import Embedder
from rag_engine.ingestion.pipeline import IngestionPipeline
from rag_engine.jobs.queue import JobQueue
from rag_engine.jobs.runner import JobRunner
from rag_engine.jobs.store import DocumentsRepo, JobsRepo
from rag_engine.models import CollectionSpec, IngestionJob, SearchResult
from rag_engine.retrieval.reranker import Reranker
from rag_engine.retrieval.retriever import Retriever
from rag_engine.scheduler.runs import ConnectorRunsRepo
from rag_engine.scheduler.scheduler import RagScheduler, SourceSpec
from rag_engine.storage.models import CollectionRow
from rag_engine.tenancy.resolver import physical_collection_name
from rag_engine.vector_store.base import VectorStore

log = logging.getLogger("rag_engine")


class CollectionsRepo:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]):
        self._sm = sessionmaker

    async def upsert(self, spec: CollectionSpec) -> None:
        async with self._sm() as s:
            row = (
                await s.execute(
                    select(CollectionRow).where(CollectionRow.name == spec.physical_name())
                )
            ).scalar_one_or_none()
            if row:
                row.embedding_model = spec.embedding_model
                row.dimensions = spec.dimensions
                row.description = spec.description
            else:
                s.add(
                    CollectionRow(
                        name=spec.physical_name(),
                        logical_name=spec.name,
                        tenant_id=spec.tenant_id,
                        embedding_model=spec.embedding_model,
                        dimensions=spec.dimensions,
                        description=spec.description,
                        created_at=datetime.utcnow(),
                    )
                )
            await s.commit()

    async def get(self, tenant_id: str, logical_name: str) -> CollectionSpec | None:
        physical = physical_collection_name(tenant_id, logical_name)
        async with self._sm() as s:
            row = (
                await s.execute(select(CollectionRow).where(CollectionRow.name == physical))
            ).scalar_one_or_none()
            if not row:
                return None
            return CollectionSpec(
                name=row.logical_name,
                tenant_id=row.tenant_id,
                embedding_model=row.embedding_model,
                dimensions=row.dimensions,
                description=row.description,
            )

    async def list_for_tenant(self, tenant_id: str) -> list[CollectionSpec]:
        async with self._sm() as s:
            rows = (
                await s.execute(
                    select(CollectionRow).where(CollectionRow.tenant_id == tenant_id)
                )
            ).scalars().all()
            return [
                CollectionSpec(
                    name=r.logical_name,
                    tenant_id=r.tenant_id,
                    embedding_model=r.embedding_model,
                    dimensions=r.dimensions,
                    description=r.description,
                )
                for r in rows
            ]

    async def delete(self, tenant_id: str, logical_name: str) -> bool:
        physical = physical_collection_name(tenant_id, logical_name)
        async with self._sm() as s:
            row = (
                await s.execute(select(CollectionRow).where(CollectionRow.name == physical))
            ).scalar_one_or_none()
            if not row:
                return False
            await s.delete(row)
            await s.commit()
            return True


class RagEngine:
    def __init__(
        self,
        vector_store: VectorStore,
        embedder: Embedder,
        chunker: Chunker,
        sessionmaker: async_sessionmaker[AsyncSession],
        reranker: Reranker | None = None,
        connector_registry: ConnectorRegistry | None = None,
    ):
        self.vstore = vector_store
        self.embedder = embedder
        self.chunker = chunker
        self.registry = connector_registry or default_registry

        self.collections = CollectionsRepo(sessionmaker)
        self.jobs = JobsRepo(sessionmaker)
        self._documents = DocumentsRepo(sessionmaker)
        self._connector_runs = ConnectorRunsRepo(sessionmaker)

        self.retriever = Retriever(vector_store, embedder, reranker)
        self.pipeline = IngestionPipeline(vector_store, embedder, chunker, self._documents)
        self.queue = JobQueue()
        self.runner = JobRunner(
            self.queue, self.jobs, self.pipeline, self.registry,
            collection_resolver=self._resolve_or_raise,
        )
        self._scheduler: RagScheduler | None = None

    # --- lifecycle ------------------------------------------------------
    async def start(self) -> None:
        self.runner.start()
        await self.runner.recover()

    async def stop(self) -> None:
        if self._scheduler:
            await self._scheduler.stop()
        await self.runner.stop()

    async def bootstrap_collections(self, specs: list[CollectionSpec]) -> None:
        """Idempotently ensure each declared collection exists.

        Safe to call on every startup — existing collections are no-ops.
        """
        for spec in specs:
            await self.ensure_collection(spec)

    def attach_scheduler(self, sources: list[SourceSpec]) -> RagScheduler:
        """Configure scheduled connector syncs.

        Each fire enqueues a job through the same queue the REST API uses
        — no duplicate ingestion path.
        """
        self._scheduler = RagScheduler(
            sources=sources,
            enqueue=self._enqueue_from_source,
            runs_repo=self._connector_runs,
        )
        self._scheduler.start()
        return self._scheduler

    async def _enqueue_from_source(self, src: SourceSpec) -> str:
        return await self.ingest(
            source_name=src.connector,
            collection=src.collection,
            tenant_id=src.tenant,
            source_config=src.config,
            metadata={"scheduled_source": src.name, **src.metadata},
        )

    # --- collections ----------------------------------------------------
    async def ensure_collection(self, spec: CollectionSpec) -> CollectionSpec:
        await self.vstore.create_collection(
            spec.physical_name(),
            dimensions=spec.dimensions,
            metadata={"tenant_id": spec.tenant_id,
                      "logical_name": spec.name,
                      "embedding_model": spec.embedding_model},
        )
        await self.collections.upsert(spec)
        return spec

    async def list_collections(self, tenant_id: str) -> list[CollectionSpec]:
        return await self.collections.list_for_tenant(tenant_id)

    async def drop_collection(self, tenant_id: str, name: str) -> bool:
        physical = physical_collection_name(tenant_id, name)
        try:
            await self.vstore.drop_collection(physical)
        except Exception as e:
            log.warning("vstore.drop_collection(%s) failed: %s", physical, e)
        return await self.collections.delete(tenant_id, name)

    # --- ingestion ------------------------------------------------------
    async def ingest(
        self,
        source_name: str,
        collection: str,
        tenant_id: str,
        source_config: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        # Validate up-front: missing collection / unknown connector should 4xx
        # before we enqueue a doomed job.
        if await self.collections.get(tenant_id, collection) is None:
            raise KeyError(f"collection {collection!r} not found for tenant {tenant_id!r}")
        self.registry.get(source_name)  # raises KeyError if unknown

        job = await self.jobs.create(
            tenant_id=tenant_id,
            collection=collection,
            source_name=source_name,
            source_config=source_config,
            metadata=metadata,
        )
        await self.queue.put(job.job_id)
        return job.job_id

    async def job_status(self, job_id: str) -> IngestionJob | None:
        return await self.jobs.get(job_id)

    # --- retrieval ------------------------------------------------------
    async def search(
        self,
        query: str,
        collection: str,
        tenant_id: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        spec = await self._resolve_or_raise(tenant_id, collection)
        return await self.retriever.search(query, spec, top_k=top_k, filters=filters)

    # --- internals ------------------------------------------------------
    async def _resolve_or_raise(self, tenant_id: str, collection: str) -> CollectionSpec:
        spec = await self.collections.get(tenant_id, collection)
        if spec is None:
            raise KeyError(
                f"collection {collection!r} not found for tenant {tenant_id!r}"
            )
        return spec
