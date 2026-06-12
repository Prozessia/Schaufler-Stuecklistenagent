"""FE-002 — queue position tracking and retry endpoint."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.job_queue import JobQueue


# ---------------------------------------------------------------------------
# Queue-position tests (unit-level, no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_position_returns_1_based_position() -> None:
    """Two jobs submitted without a running worker are at position 1 and 2."""
    # Use a large queue so put() never blocks
    q = JobQueue(AsyncMock(), concurrency=1, maxsize=0)
    # Don't start workers — jobs stay pending
    await q.submit("job-a")
    await q.submit("job-b")

    assert q.position("job-a") == 1
    assert q.position("job-b") == 2


@pytest.mark.asyncio
async def test_queue_position_none_when_not_pending() -> None:
    """Unknown job id returns None."""
    q = JobQueue(AsyncMock(), concurrency=1)
    assert q.position("no-such-job") is None


@pytest.mark.asyncio
async def test_queue_position_removed_when_worker_picks_up() -> None:
    """Once a worker picks up the job it is no longer in _pending."""
    picked = asyncio.Event()
    done = asyncio.Event()

    async def slow_runner(job_id: str) -> None:
        picked.set()
        await done.wait()

    q = JobQueue(slow_runner, concurrency=1)
    q.start()
    try:
        await q.submit("job-x")
        await picked.wait()  # worker has dequeued it
        assert q.position("job-x") is None
    finally:
        done.set()
        await q.stop()


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    from src.core import auth

    monkeypatch.setitem(auth._SETTINGS, "login_enabled", False)
    monkeypatch.setitem(auth._SETTINGS, "api_key_enabled", False)


@pytest.fixture()
def tmp_db(tmp_path):
    """Isolated SQLite database for each test."""
    db = tmp_path / "jobs_test.db"
    with patch.dict(os.environ, {"BOM_MAPPER_JOB_DB_PATH": str(db)}):
        yield db


def _make_client():
    from src.api.main import app

    return TestClient(app, raise_server_exceptions=False)


def _create_failed_job(job_store, tmp_path: Path, job_id: str = "testjob1") -> Path:
    """Insert a job with status=failed into job_store and return the filepath."""
    filepath = tmp_path / f"{job_id}_bom.pdf"
    filepath.write_bytes(b"%PDF-fake")
    job = job_store.create(job_id, "bom.pdf", filepath)
    job_store.update(job_id, status="failed", error="mapping columns failed: boom")
    return filepath


def test_retry_failed_job_returns_200_and_pending(tmp_path) -> None:
    """POST /jobs/{id}/retry on a failed job → 200, status pending."""
    from src.api.job_store import JobStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        store = JobStore(db_path)
        filepath = _create_failed_job(store, tmp_path)

        with (
            patch("src.api.routes.jobs.job_store", store),
            patch("src.api.routes.jobs.job_queue") as mock_q,
        ):
            mock_q.submit = AsyncMock(return_value=None)
            mock_q.position = MagicMock(return_value=1)

            client = _make_client()
            resp = client.post("/jobs/testjob1/retry")

        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["status"] == "pending"
    finally:
        store.close()
        Path(db_path).unlink(missing_ok=True)


def test_retry_completed_job_returns_409(tmp_path) -> None:
    """POST /jobs/{id}/retry on a completed job → 409."""
    from src.api.job_store import JobStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        store = JobStore(db_path)
        filepath = tmp_path / "comp.pdf"
        filepath.write_bytes(b"%PDF-fake")
        store.create("comp1", "comp.pdf", filepath)
        store.update("comp1", status="completed")

        with patch("src.api.routes.jobs.job_store", store):
            client = _make_client()
            resp = client.post("/jobs/comp1/retry")

        assert resp.status_code == 409
    finally:
        store.close()
        Path(db_path).unlink(missing_ok=True)


def test_retry_missing_job_returns_404() -> None:
    """POST /jobs/nonexistent/retry → 404."""
    from src.api.job_store import JobStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        store = JobStore(db_path)
        with patch("src.api.routes.jobs.job_store", store):
            client = _make_client()
            resp = client.post("/jobs/no-such-job/retry")
        assert resp.status_code == 404
    finally:
        store.close()
        Path(db_path).unlink(missing_ok=True)


def test_retry_deleted_source_file_returns_410(tmp_path) -> None:
    """POST /jobs/{id}/retry when source file gone → 410."""
    from src.api.job_store import JobStore

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        store = JobStore(db_path)
        filepath = tmp_path / "gone.pdf"
        filepath.write_bytes(b"%PDF-fake")
        store.create("gone1", "gone.pdf", filepath)
        store.update("gone1", status="failed", error="boom")
        filepath.unlink()  # simulate deleted file

        with patch("src.api.routes.jobs.job_store", store):
            client = _make_client()
            resp = client.post("/jobs/gone1/retry")

        assert resp.status_code == 410
    finally:
        store.close()
        Path(db_path).unlink(missing_ok=True)
