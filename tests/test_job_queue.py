"""Sprint 3 — item 12: bounded global job queue + concurrency limit (and AW-1:
worker tasks are owned by the queue, so a job is never GC'd mid-run)."""

from __future__ import annotations

import asyncio

import pytest

from src.api.job_queue import JobQueue


@pytest.mark.asyncio
async def test_queue_runs_submitted_jobs() -> None:
    processed: list[str] = []

    async def runner(job_id: str) -> None:
        processed.append(job_id)

    q = JobQueue(runner, concurrency=2)
    q.start()
    try:
        for jid in ["a", "b", "c"]:
            await q.submit(jid)
        await q.join()
    finally:
        await q.stop()

    assert sorted(processed) == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_worker_survives_a_crashing_job() -> None:
    processed: list[str] = []

    async def runner(job_id: str) -> None:
        if job_id == "boom":
            raise RuntimeError("kaboom")
        processed.append(job_id)

    q = JobQueue(runner, concurrency=1)
    q.start()
    try:
        await q.submit("boom")  # must not kill the worker
        await q.submit("ok")
        await q.join()
    finally:
        await q.stop()

    assert processed == ["ok"]


@pytest.mark.asyncio
async def test_concurrency_is_capped() -> None:
    active = 0
    peak = 0
    release = asyncio.Event()

    async def runner(job_id: str) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await release.wait()
        active -= 1

    q = JobQueue(runner, concurrency=2)
    q.start()
    try:
        for jid in ["1", "2", "3", "4"]:
            await q.submit(jid)
        # Let the two workers pick up two jobs and block on the event.
        await asyncio.sleep(0.05)
        assert peak <= 2  # never more than the configured concurrency
        release.set()
        await q.join()
    finally:
        release.set()
        await q.stop()

    assert peak == 2


@pytest.mark.asyncio
async def test_workers_are_referenced_while_running() -> None:
    async def runner(job_id: str) -> None:
        await asyncio.sleep(0)

    q = JobQueue(runner, concurrency=3)
    q.start()
    try:
        assert q.running is True
        assert len(q._workers) == 3  # held for the queue's lifetime (AW-1)
    finally:
        await q.stop()
    assert q.running is False
