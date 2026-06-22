"""LangGraph checkpointer factory — SQLite (zero-infra) or Postgres (prod/local).

LangGraph persists per-session conversation state (the agent's `messages` list)
through a checkpointer. We pick the backend from env so the *same* code runs on
a laptop with no infra (SQLite file) or against a real Postgres instance:

  * ``CHATBOT_CHECKPOINT_DB_URL`` set to a Postgres DSN  → ``AsyncPostgresSaver``
  * otherwise (or ``CHATBOT_CHECKPOINT_DB`` file path)   → ``AsyncSqliteSaver``

Driver note — the checkpointer talks to Postgres through **psycopg** (libpq), so
its DSN is a plain ``postgresql://user:pass@host:port/db`` (NOT the
``postgresql+asyncpg://`` form the SQLAlchemy stores use). The two layers use
different drivers on purpose; keep their URLs distinct in ``.env``.

Both savers are async context managers, so callers drive them via an
``AsyncExitStack`` exactly as before.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from langgraph.checkpoint.base import BaseCheckpointSaver

log = logging.getLogger("chatbot.checkpointer")

# Back-compat default: the file the chatbot used before Postgres landed.
SQLITE_DEFAULT = "data/chatbot_checkpoints.db"


def _checkpoint_target() -> str:
    """Resolve the checkpoint backend target from env.

    A Postgres DSN in ``CHATBOT_CHECKPOINT_DB_URL`` wins. Otherwise we fall back
    to ``CHATBOT_CHECKPOINT_DB`` (a SQLite file path) for the zero-infra path.
    """
    return (
        os.getenv("CHATBOT_CHECKPOINT_DB_URL")
        or os.getenv("CHATBOT_CHECKPOINT_DB")
        or SQLITE_DEFAULT
    )


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[BaseCheckpointSaver]:
    """Yield a ready-to-use checkpointer, backend chosen from env."""
    target = _checkpoint_target()

    if target.startswith(("postgres://", "postgresql://")):
        # Imported lazily so the SQLite-only path never needs psycopg installed.
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(target) as cp:
            # Idempotent: creates the checkpoint tables on first run, migrates
            # them on later upgrades. Safe to call on every boot.
            await cp.setup()
            log.info("LangGraph checkpointer: Postgres")
            yield cp
        return

    if target.startswith("postgresql+"):
        # A SQLAlchemy-style URL (e.g. postgresql+asyncpg://) was passed by
        # mistake — psycopg can't parse the driver suffix. Fail loud and clear.
        raise ValueError(
            "CHATBOT_CHECKPOINT_DB_URL must be a plain libpq DSN "
            "(postgresql://user:pass@host:port/db), not a SQLAlchemy URL like "
            f"{target!r}. The checkpointer uses psycopg, not asyncpg."
        )

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    # Ensure the SQLite file's directory exists before the driver opens it.
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(target) as cp:
        log.info("LangGraph checkpointer: SQLite (%s)", target)
        yield cp
