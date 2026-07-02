"""BlobStore — where the *original* document artifact (the uploaded file or the
raw text) is persisted so it can be listed and downloaded later.

Separate seam from the vector store: Milvus owns chunk embeddings, the SQL
`documents` table owns dedupe bookkeeping, and the BlobStore owns the bytes the
caller sent us. Provider-neutral like the other seams — `LocalBlobStore` is the
filesystem default; an `AzureBlobStore` can drop in behind the same Protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable


@dataclass
class BlobRef:
    """What `put` returns: the storage key plus the facts the documents table
    records so `list` / `download` work without re-reading the bytes."""
    key: str
    content_type: str
    size_bytes: int


def blob_key_for(tenant_id: str, doc_id: str, source_uri: str = "") -> str:
    """Tenant-scoped, deterministic storage key: ``{tenant_id}/{doc_id}{ext}``.

    `doc_id` is the sha256 of the source uri, so re-upserting the same document
    overwrites its blob in place — same idempotency model as the rest of the
    pipeline. The extension (from `source_uri`) is cosmetic; the authoritative
    content type lives in the documents table.
    """
    ext = PurePosixPath(source_uri).suffix if source_uri else ""
    return f"{tenant_id}/{doc_id}{ext}"


@runtime_checkable
class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> BlobRef: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> bool: ...
    async def exists(self, key: str) -> bool: ...

    async def url(self, key: str, *, expires: int = 3600) -> str | None:
        """A directly-fetchable URL for the object, or None if this backend
        doesn't expose one (callers then fall back to the API download proxy).

        Object stores (S3 / MinIO) return a time-limited **presigned** URL valid
        for `expires` seconds; the filesystem backend returns None.
        """
        ...
