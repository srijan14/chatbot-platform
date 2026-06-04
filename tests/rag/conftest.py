"""RAG-specific pytest fixtures: in-memory sqlite for rag.db schema, fake
embedder, tmp Chroma store."""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from rag_engine.embeddings.base import Embedder
from rag_engine.storage.models import Base


@pytest_asyncio.fixture
async def rag_db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def rag_sm(rag_db_engine):
    return async_sessionmaker(rag_db_engine, expire_on_commit=False, class_=AsyncSession)


class FakeEmbedder(Embedder):
    """Deterministic 8-dim embedder: each text becomes a bag-of-words-ish
    fingerprint so semantically-similar tests stay stable across runs.

    Coordinates 0..6 track how often a keyword appears; coordinate 7 is the
    length signal. Vectors are L2-normalized so distance comparisons are
    meaningful.
    """

    KEYS = ["cancel", "refund", "data", "roaming", "kyc", "bill", "plan"]
    model = "fake"
    dimensions = 8

    def _vec(self, text: str) -> list[float]:
        t = text.lower()
        v = [float(t.count(k)) for k in self.KEYS] + [float(len(t)) / 1000.0]
        # Normalize
        n = (sum(x * x for x in v) ** 0.5) or 1.0
        return [x / n for x in v]

    async def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    async def embed_query(self, text):
        return self._vec(text)


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()
