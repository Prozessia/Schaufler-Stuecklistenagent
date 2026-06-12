"""Bounded in-process job queue with a global concurrency limit.

Replaces fire-and-forget ``asyncio.create_task`` in the upload route. A fixed
pool of worker tasks pulls job ids from a queue, so the number of pipelines
running at once — and therefore the concurrent Azure load — is capped globally.
That global cap is the lever the system spec called out as missing for 429 /
latency control. The worker tasks are owned by the queue for the application's
lifetime, so a job can never be garbage-collected mid-run (AW-1).

Deliberately NOT a resume mechanism: a job interrupted by a process exit is
marked ``failed`` by the job store (see ``JobStore._recover_orphaned_jobs``) and
the user re-uploads. Re-running an interrupted job would risk partial Azure spend
and half-written exports, so fail-fast + re-upload is the chosen contract.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

JobRunner = Callable[[str], Awaitable[None]]


class JobQueue:
    """An asyncio queue drained by a fixed pool of worker tasks."""

    def __init__(
        self,
        runner: JobRunner,
        *,
        concurrency: int = 1,
        maxsize: int = 0,
    ) -> None:
        self._runner = runner
        self._concurrency = max(1, concurrency)
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self._workers: list[asyncio.Task] = []
        # Ordered list of job ids waiting in the queue (not yet picked up by a worker).
        self._pending: list[str] = []

    def start(self) -> None:
        """Spawn the worker pool. Idempotent; must run inside the event loop."""
        if self._workers:
            return
        for i in range(self._concurrency):
            self._workers.append(asyncio.create_task(self._worker(i)))
        logger.info("JobQueue started with %d worker(s)", self._concurrency)

    async def submit(self, job_id: str) -> None:
        """Enqueue a job id for processing (awaits if the queue is bounded+full)."""
        self._pending.append(job_id)
        await self._queue.put(job_id)

    def position(self, job_id: str) -> int | None:
        """1-based position of job_id in the pending list; None if not waiting."""
        try:
            return self._pending.index(job_id) + 1
        except ValueError:
            return None

    async def _worker(self, worker_id: int) -> None:
        while True:
            job_id = await self._queue.get()
            # Remove from the ordered pending list as the worker picks it up.
            try:
                self._pending.remove(job_id)
            except ValueError:
                pass  # already removed (e.g. by a concurrent retry submission)
            try:
                await self._runner(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad job must not kill the worker
                logger.exception(
                    "Worker %d: job %s crashed unexpectedly", worker_id, job_id
                )
            finally:
                self._queue.task_done()

    async def join(self) -> None:
        """Block until every enqueued job has been processed (test helper)."""
        await self._queue.join()

    async def stop(self) -> None:
        """Cancel the worker pool and wait for them to unwind."""
        for w in self._workers:
            w.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def running(self) -> bool:
        return bool(self._workers)


def _build_default_queue() -> JobQueue:
    # Imported lazily to avoid importing the heavy pipeline at module import of
    # lightweight consumers; pipeline_runner has no dependency on this module.
    from src.api.pipeline_runner import run_pipeline

    concurrency = max(1, int(os.environ.get("JOB_CONCURRENCY", "1")))
    return JobQueue(run_pipeline, concurrency=concurrency)


# Singleton used by the upload route and the FastAPI lifespan.
job_queue = _build_default_queue()
