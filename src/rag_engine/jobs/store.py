"""Async repo for ingestion_jobs and documents tables."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_engine.models import IngestionJob, JobStatus, content_hash
from rag_engine.storage.models import DocumentRow, IngestionJobRow


def _row_to_job(row: IngestionJobRow) -> IngestionJob:
    return IngestionJob(
        job_id=row.job_id,
        tenant_id=row.tenant_id,
        collection=row.collection,
        source_name=row.source_name,
        status=JobStatus(row.status),
        counts=json.loads(row.counts_json or "{}"),
        errors=json.loads(row.errors_json or "[]"),
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        source_config=json.loads(row.source_config_json or "{}"),
        metadata=json.loads(row.metadata_json or "{}"),
    )


class JobsRepo:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]):
        self._sm = sessionmaker

    async def create(
        self,
        tenant_id: str,
        collection: str,
        source_name: str,
        source_config: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestionJob:
        async with self._sm() as s:
            row = IngestionJobRow(
                job_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                collection=collection,
                source_name=source_name,
                status=JobStatus.QUEUED.value,
                source_config_json=json.dumps(source_config or {}),
                metadata_json=json.dumps(metadata or {}),
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _row_to_job(row)

    async def get(self, job_id: str) -> IngestionJob | None:
        async with self._sm() as s:
            row = (
                await s.execute(select(IngestionJobRow).where(IngestionJobRow.job_id == job_id))
            ).scalar_one_or_none()
            return _row_to_job(row) if row else None

    async def mark_running(self, job_id: str) -> None:
        async with self._sm() as s:
            await s.execute(
                update(IngestionJobRow)
                .where(IngestionJobRow.job_id == job_id)
                .values(status=JobStatus.RUNNING.value, started_at=datetime.utcnow())
            )
            await s.commit()

    async def finalize(
        self,
        job_id: str,
        status: JobStatus,
        counts: dict[str, int],
        errors: list[str],
    ) -> None:
        async with self._sm() as s:
            await s.execute(
                update(IngestionJobRow)
                .where(IngestionJobRow.job_id == job_id)
                .values(
                    status=status.value,
                    counts_json=json.dumps(counts),
                    errors_json=json.dumps(errors),
                    finished_at=datetime.utcnow(),
                )
            )
            await s.commit()

    async def recover_inflight(self) -> list[IngestionJob]:
        """At service startup, find rows the previous run left RUNNING/QUEUED.

        Returns the list so the worker can re-enqueue them. Caller is expected
        to retain idempotency (same `doc_id` keys mean re-runs overwrite, not
        duplicate).
        """
        async with self._sm() as s:
            rows = (
                await s.execute(
                    select(IngestionJobRow).where(
                        IngestionJobRow.status.in_(
                            [JobStatus.QUEUED.value, JobStatus.RUNNING.value]
                        )
                    )
                )
            ).scalars().all()
            return [_row_to_job(r) for r in rows]


class DocumentsRepo:
    """Bookkeeping for dedupe. Vectors live in Chroma; this is just hashes
    and counts so we know what was ingested and whether it has changed."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]):
        self._sm = sessionmaker

    async def upsert(
        self,
        *,
        doc_id: str,
        tenant_id: str,
        collection: str,
        source_uri: str,
        text: str,
        chunk_count: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        async with self._sm() as s:
            existing = (
                await s.execute(select(DocumentRow).where(DocumentRow.doc_id == doc_id))
            ).scalar_one_or_none()
            h = content_hash(text)
            if existing:
                existing.content_hash = h
                existing.chunk_count = chunk_count
                existing.metadata_json = json.dumps(metadata or {})
                existing.ingested_at = datetime.utcnow()
            else:
                s.add(
                    DocumentRow(
                        doc_id=doc_id,
                        tenant_id=tenant_id,
                        collection=collection,
                        source_uri=source_uri,
                        content_hash=h,
                        chunk_count=chunk_count,
                        metadata_json=json.dumps(metadata or {}),
                    )
                )
            await s.commit()

    async def delete(self, doc_id: str) -> bool:
        """Remove the bookkeeping row for a document. Returns False if absent.

        The vector chunks are deleted separately (Chroma owns them); this only
        clears the dedupe/list row so the document no longer appears as ingested.
        """
        async with self._sm() as s:
            row = await s.get(DocumentRow, doc_id)
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True

    async def list_for(
        self, tenant_id: str, collection: str
    ) -> list[dict[str, Any]]:
        """List ingested documents for a tenant's collection (newest first)."""
        async with self._sm() as s:
            rows = (
                await s.execute(
                    select(DocumentRow)
                    .where(
                        DocumentRow.tenant_id == tenant_id,
                        DocumentRow.collection == collection,
                    )
                    .order_by(DocumentRow.ingested_at.desc())
                )
            ).scalars().all()
            return [
                {
                    "doc_id": r.doc_id,
                    "source_uri": r.source_uri,
                    "chunk_count": r.chunk_count,
                    "ingested_at": r.ingested_at,
                    "metadata": json.loads(r.metadata_json or "{}"),
                }
                for r in rows
            ]

    async def session(self) -> AsyncSession:
        """Escape hatch for callers that need to run a transaction across
        repos (e.g., dedupe.decide + upsert)."""
        return self._sm()
