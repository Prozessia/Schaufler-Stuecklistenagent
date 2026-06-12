from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.job_store import Job
from src.api.main import app
from src.api.routes import jobs as jobs_route


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    from src.core import auth

    monkeypatch.setitem(auth._SETTINGS, "login_enabled", False)
    monkeypatch.setitem(auth._SETTINGS, "api_key_enabled", False)


class _StubStore:
    def __init__(self, job: Job | None) -> None:
        self._job = job

    def get(self, job_id: str) -> Job | None:
        if self._job and self._job.job_id == job_id:
            return self._job
        return None


def test_source_route_streams_uploaded_file(monkeypatch, tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    source_file = upload_dir / "demo.pdf"
    source_file.write_bytes(b"%PDF-1.4 test")

    job = Job(
        job_id="source-ok",
        filename="demo.pdf",
        filepath=source_file,
        status="completed",
    )

    monkeypatch.setattr(jobs_route, "job_store", _StubStore(job))
    monkeypatch.setattr(jobs_route, "_UPLOAD_DIR", upload_dir.resolve())

    client = TestClient(app)
    response = client.get("/jobs/source-ok/source")

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 test"
    assert "application/pdf" in response.headers.get("content-type", "")


def test_source_route_blocks_paths_outside_upload_dir(
    monkeypatch, tmp_path: Path
) -> None:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    external_file = tmp_path / "outside.pdf"
    external_file.write_bytes(b"%PDF-1.4 outside")

    job = Job(
        job_id="source-forbidden",
        filename="outside.pdf",
        filepath=external_file,
        status="completed",
    )

    monkeypatch.setattr(jobs_route, "job_store", _StubStore(job))
    monkeypatch.setattr(jobs_route, "_UPLOAD_DIR", upload_dir.resolve())

    client = TestClient(app)
    response = client.get("/jobs/source-forbidden/source")

    assert response.status_code == 403
