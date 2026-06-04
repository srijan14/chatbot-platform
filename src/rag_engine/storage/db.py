"""Async SQLAlchemy engine + session factory for the RAG control plane.

Mirrors `src/chatbot/persistence/db.py` so that anyone fluent in one is fluent
in the other. The RAG service owns its own DB (`data/rag.db` by default) — it
is independent of the chatbot's session/turn-log store.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from rag_engine.storage.models import Base

DEFAULT_DB_URL = "sqlite+aiosqlite:///data/rag.db"


def _ensure_sqlite_dir(url: str) -> None:
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return
    path = url[len(prefix):]
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def create_engine_and_sessionmaker(
    url: str | None = None,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    db_url = url or os.getenv("RAG_DB_URL", DEFAULT_DB_URL)
    _ensure_sqlite_dir(db_url)
    engine = create_async_engine(db_url, echo=False, future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, sm


async def init_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
