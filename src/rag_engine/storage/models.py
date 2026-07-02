"""SQLAlchemy tables for the RAG control plane.

Note: chunks are *not* mirrored here — Milvus owns them. We keep only what
SQL is better at: collection metadata, job tracking, document-level dedupe,
and connector run history.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CollectionRow(Base):
    __tablename__ = "collections"

    # Physical Milvus name = "{tenant_id}__{logical_name}". Always the PK.
    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    logical_name: Mapped[str] = mapped_column(String(255), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    embedding_model: Mapped[str] = mapped_column(String(128))
    dimensions: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IngestionJobRow(Base):
    __tablename__ = "ingestion_jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    collection: Mapped[str] = mapped_column(String(255), index=True)
    source_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    source_config_json: Mapped[str] = mapped_column(Text, default="{}")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DocumentRow(Base):
    """Lightweight bookkeeping for dedupe / re-ingestion. One row per ingested
    document (not chunk). `content_hash` drives the "skip if unchanged" branch
    in the ingestion pipeline.

    The `blob_*` columns point at the original artifact in the BlobStore so the
    document can be listed with a download link and fetched back verbatim. They
    are nullable: documents ingested before blob storage existed (or via a
    connector that doesn't retain the raw bytes) simply have no downloadable
    file."""
    __tablename__ = "documents"

    # Composite PK (doc_id, tenant_id). `doc_id` is derived from the source uri
    # only, so the SAME id used by two different bots produces the same doc_id —
    # tenant_id in the key keeps each bot's row (and its blob pointer) separate,
    # so one bot can never read/overwrite another's document bookkeeping.
    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    collection: Mapped[str] = mapped_column(String(255), index=True)
    source_uri: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Original artifact in the BlobStore (nullable — see class docstring).
    blob_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)


class ConnectorRunRow(Base):
    __tablename__ = "connector_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_name: Mapped[str] = mapped_column(String(128), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    collection: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))
    docs_seen: Mapped[int] = mapped_column(Integer, default=0)
    docs_changed: Mapped[int] = mapped_column(Integer, default=0)
    docs_skipped: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
