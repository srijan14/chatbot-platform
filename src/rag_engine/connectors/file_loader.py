"""Local-filesystem connector.

config:
  path: str        # file or directory
  glob: str        # if `path` is a directory; default "**/*"

Yields one DocRef per matching file. `fetch_document()` reads bytes and runs
the appropriate loader from `rag_engine.ingestion.loaders`.
"""
from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from rag_engine.connectors.base import DocRef, SourceConnector
from rag_engine.ingestion.loaders import bytes_to_text, mime_for_path
from rag_engine.models import Document, doc_id_for


class FileLoaderConnector(SourceConnector):
    connector_name = "file_loader"

    def __init__(self, config: dict):
        path = config.get("path")
        if not path:
            raise ValueError("file_loader requires `path`")
        self.path = Path(path)
        self.glob = config.get("glob", "**/*")

    async def list_documents(self) -> AsyncIterator[DocRef]:
        if self.path.is_file():
            yield DocRef(
                source_uri=str(self.path.resolve()),
                mime_type=mime_for_path(str(self.path)),
            )
            return
        if not self.path.exists():
            return
        for p in sorted(self.path.glob(self.glob)):
            if p.is_file():
                yield DocRef(
                    source_uri=str(p.resolve()),
                    mime_type=mime_for_path(str(p)),
                )

    async def fetch_document(
        self, ref: DocRef, tenant_id: str, collection: str
    ) -> Document:
        data = Path(ref.source_uri).read_bytes()
        text = bytes_to_text(data, ref.mime_type)
        return Document(
            doc_id=doc_id_for(ref.source_uri),
            source_uri=ref.source_uri,
            content=text,
            mime_type=ref.mime_type,
            tenant_id=tenant_id,
            collection=collection,
            metadata={"filename": Path(ref.source_uri).name, **ref.metadata},
        )
