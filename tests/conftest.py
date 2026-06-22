"""Shared pytest fixtures for the RAG engine tests.

`tests/rag/test_document_crud.py` exercises the real Milvus Lite vector store but
needs two collaborators it doesn't construct itself:

  * ``fake_embedder`` — a deterministic, network-free Embedder so tests never
    call Azure. Embedding *quality* is irrelevant here (each collection holds a
    single document), but determinism and correct dimensionality are required by
    the Milvus collection schema.
  * ``rag_sm`` — an async SQLAlchemy sessionmaker for the RAG control-plane
    tables (jobs, documents, dedupe), backed by an in-memory SQLite so each test
    run is isolated and fast.
"""
from __future__ import annotations

import hashlib
import struct

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from rag_engine.storage.models import Base

_DIMENSIONS = 8


class _FakeEmbedder:
    """Deterministic embedder: hashes text into a fixed-dim float vector."""

    model = "fake"
    dimensions = _DIMENSIONS

    def _vec(self, text: str) -> list[float]:
        out: list[float] = []
        i = 0
        while len(out) < self.dimensions:
            digest = hashlib.sha256(f"{i}:{text}".encode()).digest()
            (val,) = struct.unpack("<Q", digest[:8])
            out.append((val % 10_000) / 10_000.0)
            i += 1
        return out[: self.dimensions]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


@pytest.fixture
def fake_embedder() -> _FakeEmbedder:
    return _FakeEmbedder()


@pytest_asyncio.fixture
async def rag_sm():
    # In-memory SQLite kept alive via StaticPool so the schema created here is
    # visible to every session built from the sessionmaker.
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()
