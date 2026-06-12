"""PERF-001 (2d): Job wall-clock timeout test.

Patches _job_timeout_seconds to 0.1s and makes parse_file hang for 1s so the
pipeline exits with a "Zeitlimit" error message without waiting a full minute.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.api import pipeline_runner
from src.api.job_store import JobStore
import src.llm.azure_openai as azure_openai_module


# ---------------------------------------------------------------------------
# Stubs — mirrors the pattern from test_pipeline_runner.py
# ---------------------------------------------------------------------------


class _StubLLM:
    pass


class _StubCounterCheckService:
    def __init__(self, llm: object) -> None:
        self.llm = llm

    def release_job(self, job_id: str) -> None:
        pass

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_fails_with_timeout_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_pipeline marks the job as failed with a 'Zeitlimit' message when the
    inner pipeline exceeds the configured timeout.

    Approach:
      - Patch _job_timeout_seconds to return 0.1 (seconds).
      - Patch parse_file to sleep for 1 second (well beyond timeout).
      - Patch AzureOpenAILLM so no real Azure credentials are needed.
      - Verify the job ends in status='failed' with the timeout message.
    """
    db_path = tmp_path / "jobs_timeout.db"
    store = JobStore(db_path=db_path)
    source_file = tmp_path / "demo.pdf"
    source_file.write_bytes(b"%PDF-1.4 demo")
    store.create("job-timeout-1", source_file.name, source_file)

    async def _slow_parse_file(
        filepath: Path, llm: object | None = None, progress_callback: object = None
    ) -> None:
        await asyncio.sleep(1.0)
        raise AssertionError("parse_file should not complete within 0.1s timeout")

    monkeypatch.setattr(pipeline_runner, "job_store", store)
    monkeypatch.setattr(azure_openai_module, "AzureOpenAILLM", _StubLLM)
    monkeypatch.setattr(
        pipeline_runner, "VisionCounterCheckService", _StubCounterCheckService
    )
    monkeypatch.setattr(pipeline_runner, "parse_file", _slow_parse_file)
    monkeypatch.setattr(pipeline_runner, "_job_timeout_seconds", lambda: 0.1)

    await pipeline_runner.run_pipeline("job-timeout-1")

    job = store.get("job-timeout-1")
    assert job is not None
    assert job.status == "failed", f"Expected failed, got {job.status!r}"
    assert job.error is not None
    assert "zeitlimit" in job.error.lower(), (
        f"Expected 'Zeitlimit' in error message, got: {job.error!r}"
    )
