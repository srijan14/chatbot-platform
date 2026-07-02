"""RagEngine — the facade callers (REST routes, MCP tools, other apps) hit.

Everything below is wiring. The interesting logic lives in `retrieval/`,
`ingestion/`, `jobs/`. Construct one of these once at service startup and
keep it on app.state.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from rag_engine.chunking.base import Chunker
from rag_engine.connectors.registry import ConnectorRegistry, default_registry
from rag_engine.embeddings.base import Embedder
from rag_engine.ingestion.loaders import mime_for_path
from rag_engine.ingestion.pipeline import IngestionPipeline
from rag_engine.storage.blobs import BlobStore, LocalBlobStore, blob_key_for
from rag_engine.jobs.queue import JobQueue
from rag_engine.jobs.runner import JobRunner
from rag_engine.jobs.store import DocumentsRepo, JobsRepo
from rag_engine.models import (
    CollectionSpec,
    Document,
    IngestionJob,
    SearchResult,
    doc_id_for,
)
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
        blob_store: BlobStore | None = None,
    ):
        self.vstore = vector_store
        self.embedder = embedder
        self.chunker = chunker
        # Stores the original artifact (uploaded file / raw text) for download.
        # Defaults to the filesystem store so callers that don't wire one still
        # get working list-with-link + download behaviour.
        self.blob_store = blob_store or LocalBlobStore()
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

    # --- single-document CRUD (synchronous; for API-driven management) ------
    async def upsert_document(
        self,
        tenant_id: str,
        collection: str,
        source_uri: str,
        content: str,
        mime_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        raw_bytes: bytes | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Add or update one document inline (no connector, no job queue).

        `source_uri` is the caller's stable identifier — re-upserting the same
        `source_uri` updates the document in place (old chunks dropped first).

        The original artifact is persisted to the BlobStore so it can be listed
        with a download link and fetched back: `raw_bytes` for a binary upload
        (PDF, etc.), or the encoded `content` for a plain-text upsert. Returns a
        status (`created` / `updated` / `unchanged`) plus counts and the stored
        blob's facts (content_type / size_bytes / filename).
        """
        spec = await self._resolve_or_raise(tenant_id, collection)
        resolved_mime = mime_type or mime_for_path(source_uri)
        doc = Document(
            doc_id=doc_id_for(source_uri),
            source_uri=source_uri,
            content=content,
            mime_type=resolved_mime,
            tenant_id=tenant_id,
            collection=collection,
            metadata=metadata or {},
        )

        # Persist the original artifact first so the download is available even
        # if a transient indexing error makes the caller retry. Idempotent: the
        # key is derived from doc_id, so a retry overwrites in place.
        data = raw_bytes if raw_bytes is not None else content.encode("utf-8")
        display_name = filename or PurePosixPath(source_uri).name or source_uri
        blob_key = blob_key_for(tenant_id, doc.doc_id, source_uri)
        ref = await self.blob_store.put(blob_key, data, resolved_mime)

        counts, decision = await self.pipeline.ingest_one(doc, spec)

        # Record the blob pointer on the bookkeeping row (no-op if ingest errored
        # before the row was written — nothing to attach a download to then).
        await self._documents.set_blob(
            tenant_id,
            doc.doc_id,
            blob_key=blob_key,
            content_type=resolved_mime,
            size_bytes=ref.size_bytes,
            filename=display_name,
        )

        if decision is None:
            status = "error"
        elif decision.is_new:
            status = "created"
        elif decision.is_changed:
            status = "updated"
        else:
            status = "unchanged"
        return {
            "doc_id": doc.doc_id,
            "source_uri": source_uri,
            "status": status,
            "counts": counts.asdict(),
            "errors": counts.error_messages,
            "content_type": resolved_mime,
            "size_bytes": ref.size_bytes,
            "filename": display_name,
            # Presigned object-store link when the backend exposes one (S3/MinIO);
            # None for the filesystem store — the API then returns its /content proxy.
            "download_url": await self.blob_store.url(blob_key),
        }

    async def get_document_blob(
        self,
        tenant_id: str,
        collection: str,
        *,
        source_uri: str | None = None,
        doc_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch a document's original artifact for download.

        Returns ``{data, content_type, filename}`` or None if the document /
        blob is absent. Cross-tenant access is refused: the row's tenant +
        collection must match the caller's scope.
        """
        if not source_uri and not doc_id:
            raise ValueError("get_document_blob requires source_uri or doc_id")
        resolved_doc_id = doc_id or doc_id_for(source_uri)  # type: ignore[arg-type]
        row = await self._documents.get(tenant_id, resolved_doc_id)
        if (
            row is None
            or row.get("collection") != collection
            or not row.get("blob_key")
        ):
            return None
        try:
            data = await self.blob_store.get(row["blob_key"])
        except FileNotFoundError:
            return None
        return {
            "data": data,
            "content_type": row.get("content_type") or "application/octet-stream",
            "filename": row.get("filename") or PurePosixPath(row["source_uri"]).name,
        }

    async def delete_document(
        self,
        tenant_id: str,
        collection: str,
        *,
        source_uri: str | None = None,
        doc_id: str | None = None,
    ) -> dict[str, Any]:
        """Remove one document: its chunks from the vector store + its
        bookkeeping row. Identify it by `source_uri` (preferred) or `doc_id`.
        """
        if not source_uri and not doc_id:
            raise ValueError("delete_document requires source_uri or doc_id")
        resolved_doc_id = doc_id or doc_id_for(source_uri)  # type: ignore[arg-type]
        spec = await self._resolve_or_raise(tenant_id, collection)
        # Read the blob pointer before we drop the bookkeeping row.
        row = await self._documents.get(tenant_id, resolved_doc_id)
        chunks_removed = await self.vstore.delete_by_filter(
            spec.physical_name(), where={"doc_id": resolved_doc_id}
        )
        existed = await self._documents.delete(tenant_id, resolved_doc_id)
        blob_deleted = False
        if row and row.get("blob_key"):
            blob_deleted = await self.blob_store.delete(row["blob_key"])
        return {
            "doc_id": resolved_doc_id,
            "source_uri": source_uri,
            "deleted": existed or chunks_removed > 0,
            "chunks_removed": chunks_removed,
            "blob_deleted": blob_deleted,
        }

    async def list_documents(
        self, tenant_id: str, collection: str
    ) -> list[dict[str, Any]]:
        """List the documents ingested into a tenant's collection.

        Each row carries a `download_url`: a presigned object-store link when the
        blob backend exposes one, else None (the API falls back to its proxy).
        """
        rows = await self._documents.list_for(tenant_id, collection)
        for r in rows:
            blob_key = r.get("blob_key")
            r["download_url"] = await self.blob_store.url(blob_key) if blob_key else None
        return rows

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
        results = await self.retriever.search(query, spec, top_k=top_k, filters=filters)
        await self._attach_source_urls(tenant_id, results)
        return results

    async def _attach_source_urls(
        self, tenant_id: str, results: list[SearchResult]
    ) -> None:
        """Best-effort: stamp each hit's `metadata["source_url"]` with a presigned
        link to its original document, so chat citations can point back to the
        source file. Never fails search — a link problem must not break retrieval.
        """
        try:
            doc_ids = {r.doc_id for r in results if r.doc_id}
            if not doc_ids:
                return
            blob_keys = await self._documents.blob_keys_for(tenant_id, doc_ids)
            for r in results:
                blob_key = blob_keys.get(r.doc_id)
                if not blob_key:
                    continue
                url = await self.blob_store.url(blob_key)
                if url:
                    r.metadata = {**(r.metadata or {}), "source_url": url}
        except Exception:  # pragma: no cover - defensive
            log.warning("source_url enrichment failed", exc_info=True)

    # --- internals ------------------------------------------------------
    async def _resolve_or_raise(self, tenant_id: str, collection: str) -> CollectionSpec:
        spec = await self.collections.get(tenant_id, collection)
        if spec is None:
            raise KeyError(
                f"collection {collection!r} not found for tenant {tenant_id!r}"
            )
        return spec
