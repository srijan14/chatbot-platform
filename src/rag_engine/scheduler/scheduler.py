"""APScheduler integration.

Reads a list of declarative sources (typically loaded from
`configs/rag/sources.yaml`) and schedules each one to enqueue an ingestion
job through the same JobQueue the REST API uses. Connectors don't run
inline here — they run inside the JobRunner like any other job.

Each scheduled execution writes a ConnectorRunRow so operators can answer
"when did this source last sync, and did it work?" without grepping logs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from rag_engine.scheduler.runs import ConnectorRunsRepo

log = logging.getLogger("rag_engine.scheduler")


@dataclass
class SourceSpec:
    name: str                          # human label, e.g. "telecom_policies_local"
    tenant: str
    collection: str
    connector: str                     # connector_name in the registry
    config: dict[str, Any] = field(default_factory=dict)
    cron: str | None = None            # 5-field cron, e.g. "*/15 * * * *"
    metadata: dict[str, Any] = field(default_factory=dict)


class RagScheduler:
    def __init__(
        self,
        sources: list[SourceSpec],
        enqueue: Callable[[SourceSpec], Awaitable[str]],
        runs_repo: ConnectorRunsRepo,
    ):
        self._sources = sources
        self._enqueue = enqueue
        self._runs = runs_repo
        self._sched = AsyncIOScheduler()

    def start(self) -> None:
        for src in self._sources:
            if not src.cron:
                log.info("source %s has no cron; skipping schedule", src.name)
                continue
            trigger = CronTrigger.from_crontab(src.cron)
            self._sched.add_job(
                self._fire,
                trigger=trigger,
                kwargs={"source": src},
                id=f"src:{src.name}",
                replace_existing=True,
                misfire_grace_time=300,
                coalesce=True,
            )
            log.info("scheduled source %s (%s)", src.name, src.cron)
        self._sched.start()

    async def stop(self) -> None:
        try:
            self._sched.shutdown(wait=False)
        except Exception:
            pass

    async def _fire(self, source: SourceSpec) -> None:
        started = datetime.utcnow()
        try:
            job_id = await self._enqueue(source)
            await self._runs.record(
                source_name=source.name,
                tenant_id=source.tenant,
                collection=source.collection,
                status="enqueued",
                started_at=started,
                finished_at=datetime.utcnow(),
                error=None,
            )
            log.info("source %s enqueued job %s", source.name, job_id)
        except Exception as e:
            await self._runs.record(
                source_name=source.name,
                tenant_id=source.tenant,
                collection=source.collection,
                status="failed",
                started_at=started,
                finished_at=datetime.utcnow(),
                error=f"{type(e).__name__}: {e}",
            )
            log.exception("source %s failed to enqueue", source.name)
