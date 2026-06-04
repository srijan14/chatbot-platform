"""ConnectorRunsRepo — bookkeeping for scheduler fires."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_engine.storage.models import ConnectorRunRow


class ConnectorRunsRepo:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]):
        self._sm = sessionmaker

    async def record(
        self,
        *,
        source_name: str,
        tenant_id: str,
        collection: str,
        status: str,
        started_at: datetime,
        finished_at: datetime | None,
        error: str | None,
        docs_seen: int = 0,
        docs_changed: int = 0,
        docs_skipped: int = 0,
    ) -> None:
        async with self._sm() as s:
            s.add(
                ConnectorRunRow(
                    source_name=source_name,
                    tenant_id=tenant_id,
                    collection=collection,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    error=error,
                    docs_seen=docs_seen,
                    docs_changed=docs_changed,
                    docs_skipped=docs_skipped,
                )
            )
            await s.commit()
