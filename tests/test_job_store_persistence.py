"""Persistence tests for the SQLite-backed JobStore.

Verifies:
  1. Jobs created in one store instance are readable from a fresh instance
     pointing at the same DB file (i.e. survive a "restart").
  2. Jobs left in `pending` or `processing` are auto-marked `failed` when
     a fresh store opens the DB — a server restart must not leave ghosts.
"""

from __future__ import annotations

from pathlib import Path

from src.api.job_store import JobStore


def test_jobs_survive_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"

    store = JobStore(db_path=db_path)
    store.create("job-completed", "ok.pdf", tmp_path / "ok.pdf")
    store.update("job-completed", status="completed", progress=1.0, customer="ACME")
    store.close()

    # Simulate a process restart by opening a new store on the same DB.
    fresh = JobStore(db_path=db_path)
    job = fresh.get("job-completed")

    assert job is not None
    assert job.status == "completed"
    assert job.progress == 1.0
    assert job.customer == "ACME"
    assert job.filename == "ok.pdf"


def test_orphaned_in_flight_jobs_become_failed_on_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"

    store = JobStore(db_path=db_path)
    store.create("job-pending", "a.pdf", tmp_path / "a.pdf")
    store.create("job-running", "b.pdf", tmp_path / "b.pdf")
    store.update("job-running", status="processing", progress=0.5)
    store.create("job-done", "c.pdf", tmp_path / "c.pdf")
    store.update("job-done", status="completed", progress=1.0)
    store.close()

    # Crash + restart.
    fresh = JobStore(db_path=db_path)

    pending = fresh.get("job-pending")
    running = fresh.get("job-running")
    done = fresh.get("job-done")

    assert pending is not None and pending.status == "failed"
    assert pending.error and "restart" in pending.error.lower()

    assert running is not None and running.status == "failed"
    assert running.error and "restart" in running.error.lower()

    # Completed jobs are left alone.
    assert done is not None and done.status == "completed"
    assert done.error is None
