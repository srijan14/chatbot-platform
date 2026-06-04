"""Async worker that consumes a JobQueue and runs the ingestion pipeline.

One task per process. Multi-worker fan-out is future work — when it arrives,
queue identity moves out of process (Redis) and the worker becomes N replicas
of this same loop.
"""
from __future__ import annotations

import asyncio
import logging

from rag_engine.connectors.registry import ConnectorRegistry
from rag_engine.ingestion.pipeline import IngestionPipeline
from rag_engine.jobs.queue import JobQueue
from rag_engine.jobs.store import JobsRepo
from rag_engine.models import CollectionSpec, JobStatus

log = logging.getLogger("rag_engine.jobs")


class JobRunner:
    def __init__(
        self,
        queue: JobQueue,
        jobs_repo: JobsRepo,
        pipeline: IngestionPipeline,
        registry: ConnectorRegistry,
        collection_resolver,        # async (tenant_id, name) -> CollectionSpec
    ):
        self.queue = queue
        self.jobs = jobs_repo
        self.pipeline = pipeline
        self.registry = registry
        self.resolve = collection_resolver
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="rag-job-runner")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def recover(self) -> int:
        """Re-enqueue any jobs left QUEUED/RUNNING by a previous process."""
        leftovers = await self.jobs.recover_inflight()
        for j in leftovers:
            await self.queue.put(j.job_id)
        if leftovers:
            log.info("recovered %d in-flight jobs", len(leftovers))
        return len(leftovers)

    async def _loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await self._process(job_id)
            except Exception:
                log.exception("job %s crashed", job_id)
            finally:
                self.queue.task_done()

    async def _process(self, job_id: str) -> None:
        job = await self.jobs.get(job_id)
        if job is None:
            log.warning("job %s not found", job_id)
            return

        await self.jobs.mark_running(job_id)
        log.info("job %s started (%s -> %s/%s)",
                 job_id, job.source_name, job.tenant_id, job.collection)

        try:
            connector_cls = self.registry.get(job.source_name)
            connector = connector_cls(job.source_config)
            spec = await self.resolve(job.tenant_id, job.collection)
            counts = await self.pipeline.run(connector, spec)
        except Exception as e:
            log.exception("job %s failed", job_id)
            await self.jobs.finalize(
                job_id,
                JobStatus.FAILED,
                counts={"documents": 0, "chunks": 0, "embedded": 0, "upserted": 0, "skipped": 0},
                errors=[f"{type(e).__name__}: {e}"],
            )
            return

        status = JobStatus.SUCCEEDED if counts.errors == 0 else JobStatus.FAILED
        await self.jobs.finalize(
            job_id, status, counts=counts.asdict(), errors=counts.error_messages
        )
        log.info("job %s finished status=%s counts=%s", job_id, status.value, counts.asdict())
