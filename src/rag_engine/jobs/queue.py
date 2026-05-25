"""In-process job queue.

The control surface is intentionally tiny — `put(job_id)` and `get() -> job_id`
— so swapping to Redis/RQ later is a one-class change. Jobs are persisted to
SQL the moment they're created (see JobsRepo.create), so a crash loses only
the in-memory queue position, not the job itself; on startup the worker calls
`JobsRepo.recover_inflight()` and re-enqueues.
"""
from __future__ import annotations

import asyncio


class JobQueue:
    def __init__(self):
        self._q: asyncio.Queue[str] = asyncio.Queue()

    async def put(self, job_id: str) -> None:
        await self._q.put(job_id)

    async def get(self) -> str:
        return await self._q.get()

    def task_done(self) -> None:
        self._q.task_done()

    def qsize(self) -> int:
        return self._q.qsize()
