"""RAG sub-platform — ingestion, retrieval, and multi-tenant knowledge bases.

This package is the *library* underneath `services/rag_api` and `services/rag_mcp`.
Other applications can import `RagEngine` directly and skip the HTTP/MCP hop.
"""
from rag_engine.engine import RagEngine
from rag_engine.models import (
    Chunk,
    CollectionSpec,
    Document,
    IngestionJob,
    JobStatus,
    SearchResult,
)

__all__ = [
    "RagEngine",
    "Chunk",
    "CollectionSpec",
    "Document",
    "IngestionJob",
    "JobStatus",
    "SearchResult",
]
