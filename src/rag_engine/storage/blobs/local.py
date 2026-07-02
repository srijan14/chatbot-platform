"""Filesystem-backed BlobStore — the zero-infra default (dev / local runs).

Writes each blob to ``{root}/{key}``. File I/O is pushed to a thread so a large
PDF doesn't block the event loop. The authoritative content type is stored in
the documents table, so we don't keep sidecar metadata on disk here.

Swap in an object-store implementation (e.g. AzureBlobStore) behind the same
`BlobStore` Protocol for production durability + presigned URLs.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from rag_engine.storage.blobs.base import BlobRef


class LocalBlobStore:
    def __init__(self, root: str | Path = "data/blobs"):
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        """Resolve a key under the root, refusing anything that escapes it."""
        root = self._root.resolve()
        p = (root / key).resolve()
        if root != p and root not in p.parents:
            raise ValueError(f"blob key {key!r} escapes storage root")
        return p

    async def put(self, key: str, data: bytes, content_type: str) -> BlobRef:
        p = self._path(key)

        def _write() -> None:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

        await asyncio.to_thread(_write)
        return BlobRef(key=key, content_type=content_type, size_bytes=len(data))

    async def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise FileNotFoundError(key)
        return await asyncio.to_thread(p.read_bytes)

    async def delete(self, key: str) -> bool:
        p = self._path(key)
        if not p.exists():
            return False
        await asyncio.to_thread(p.unlink)
        return True

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()

    async def url(self, key: str, *, expires: int = 3600) -> str | None:
        # No public URL for local files — callers fall back to the API's
        # /content download proxy.
        return None
