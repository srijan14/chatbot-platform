"""Pydantic request/response shapes for the RAG control plane.

These cross both REST (`rag_api`) and MCP (`rag_mcp`) — the MCP server
re-serializes them when bridging to tool I/O. Keep them serializable, no
non-JSON types.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


SourceLiteral = Literal["upload", "file_path", "confluence", "notion"]


class CollectionCreate(BaseModel):
    name: str = Field(..., description="Logical collection name (per tenant).")
    embedding_model: str = "text-embedding-3-small"
    dimensions: int = 1536
    description: Optional[str] = None


class CollectionOut(BaseModel):
    name: str
    tenant_id: str
    embedding_model: str
    dimensions: int
    description: Optional[str] = None


class IngestRequest(BaseModel):
    collection: str
    source: SourceLiteral
    source_config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    job_id: str
    status: Literal["queued"] = "queued"


class JobOut(BaseModel):
    job_id: str
    tenant_id: str
    collection: str
    source_name: str
    status: str
    counts: dict[str, int]
    errors: list[str]
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class SearchRequest(BaseModel):
    query: str
    collection: str
    top_k: int = 5
    filters: Optional[dict[str, Any]] = None


class SearchHit(BaseModel):
    chunk_id: str
    doc_id: str
    text: str
    score: float
    source_uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    collection: str
    results: list[SearchHit]


class HealthResponse(BaseModel):
    ok: bool
    service: str = "rag_api"
