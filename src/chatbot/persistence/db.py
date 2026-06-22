"""Async SQLAlchemy engine + session factory + schema bootstrap.

We deliberately do not add alembic yet — the schema will churn while these
features land, and `Base.metadata.create_all` on lifespan startup is the right
trade-off. Models all hang off a single `Base` so a later `alembic init` only
needs `target_metadata = Base.metadata`.
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

from src.chatbot.persistence.models import Base

DEFAULT_DB_URL = "sqlite+aiosqlite:///data/chatbot.db"


def _ensure_sqlite_dir(url: str) -> None:
    """For sqlite URLs, make sure the parent directory exists."""
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return
    path = url[len(prefix):]
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def create_engine_and_sessionmaker(
    url: str | None = None,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    db_url = url or os.getenv("CHATBOT_DB_URL", DEFAULT_DB_URL)
    _ensure_sqlite_dir(db_url)
    # pool_pre_ping recycles connections dropped by Postgres/idle-timeouts; it's
    # a cheap SELECT 1 and harmless on SQLite, so we keep one code path.
    engine = create_async_engine(db_url, echo=False, future=True, pool_pre_ping=True)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, sm


async def init_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# Module-level lazy default engine — used by scripts that don't have an
# application lifespan to manage one. The chatbot service builds its own
# inside the FastAPI lifespan and stores it on app.state.
_default_engine: AsyncEngine | None = None
_default_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def AsyncSessionLocal() -> AsyncSession:
    global _default_engine, _default_sessionmaker
    if _default_sessionmaker is None:
        _default_engine, _default_sessionmaker = create_engine_and_sessionmaker()
    return _default_sessionmaker()
