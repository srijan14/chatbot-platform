"""SourceConnector Protocol — adapts an external doc source to the pipeline.

Two-phase design: `list_documents()` is cheap (an iterator of refs the
scheduler can diff against `documents.content_hash`), `fetch_document()` is
the expensive pull. This separation lets the scheduler skip unchanged docs
without paying to fetch their full bodies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, ClassVar, Protocol, runtime_checkable

from rag_engine.models import Document


@dataclass
class DocRef:
    """Cheap pointer to a document the connector can fetch on demand."""
    source_uri: str                  # stable url/path used for doc_id
    mime_type: str = "text/plain"
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SourceConnector(Protocol):
    connector_name: ClassVar[str]

    def __init__(self, config: dict[str, Any]) -> None: ...

    def list_documents(self) -> AsyncIterator[DocRef]: ...

    async def fetch_document(
        self, ref: DocRef, tenant_id: str, collection: str
    ) -> Document: ...
