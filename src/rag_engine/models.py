"""Core dataclasses passed between RAG engine components.

These are platform-neutral — no Milvus, FastAPI, or Azure types leak in. Each
seam (vector store, embedder, connector, chunker, reranker) consumes and
produces these shapes so implementations are swappable.
"""
from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def doc_id_for(source_uri: str) -> str:
    """Deterministic id from a stable source identifier.

    Re-ingesting the same uri must produce the same id so dedupe and
    delete-by-filter stay idempotent across runs.
    """
    return hashlib.sha256(source_uri.encode("utf-8")).hexdigest()[:32]


def content_hash(text: str) -> str:
    """SHA-256 over the post-load text. Drives "changed since last ingest?" checks."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Document:
    doc_id: str
    source_uri: str
    content: str
    mime_type: str
    tenant_id: str
    collection: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    """A token-budget-sized slice of a Document plus the metadata Milvus stores.

    `metadata` always carries `tenant_id`, `source_uri`, `doc_id`, and `ordinal`
    — these are non-negotiable for retrieval-time filtering and citation.
    """
    chunk_id: str
    doc_id: str
    text: str
    ordinal: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    chunk_id: str
    doc_id: str
    text: str
    score: float
    source_uri: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectionSpec:
    """User-facing collection definition. The physical Milvus collection name is
    `{tenant_id}__{name}` — never assemble it by hand, use `physical_name()`."""
    name: str                       # logical name e.g. "telecom_policies"
    tenant_id: str
    embedding_model: str
    dimensions: int
    description: str | None = None

    def physical_name(self) -> str:
        return f"{self.tenant_id}__{self.name}"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class IngestionJob:
    job_id: str
    tenant_id: str
    collection: str
    source_name: str             # connector type ("file_loader", "confluence", …)
    status: JobStatus
    counts: dict[str, int] = field(default_factory=dict)   # documents/chunks/embedded/upserted/skipped
    errors: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    source_config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
