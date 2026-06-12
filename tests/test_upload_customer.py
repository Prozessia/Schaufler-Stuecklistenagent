"""DATA-001 — customer field forwarded through upload → job_store."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    """Disable auth exactly as in test_upload_security.py."""
    from src.core import auth

    monkeypatch.setitem(auth._SETTINGS, "login_enabled", False)
    monkeypatch.setitem(auth._SETTINGS, "api_key_enabled", False)


# Minimal valid PDF magic bytes (SEC-005 check)
_MINIMAL_PDF = b"%PDF-1.4 fake"


def _make_client():
    return TestClient(app, raise_server_exceptions=False)


def test_upload_with_customer_sets_job_customer() -> None:
    """customer form field is stored in the job_store entry."""
    with patch("src.api.routes.upload.job_queue") as mock_queue, patch(
        "src.api.routes.upload.job_store"
    ) as mock_store:
        mock_queue.submit = AsyncMock(return_value=None)

        # Capture what create() was called with
        created_jobs: list[dict] = []

        def fake_create(job_id, filename, filepath, *, customer=""):
            created_jobs.append({"job_id": job_id, "customer": customer})
            # Return a minimal Job-like object
            from unittest.mock import MagicMock

            job = MagicMock()
            job.job_id = job_id
            job.filename = filename
            return job

        mock_store.create.side_effect = fake_create

        client = _make_client()
        response = client.post(
            "/upload",
            data={"customer": "ACME GmbH"},
            files={"file": ("bom.pdf", _MINIMAL_PDF, "application/pdf")},
        )

    # Accept 200 or any non-400 (the mock store returns a MagicMock, not a proper
    # UploadResponse, which may cause a 500 from response validation — that's fine
    # for this test since we only care that create() was called correctly).
    assert response.status_code != 400, response.json()
    assert len(created_jobs) == 1
    assert created_jobs[0]["customer"] == "ACME GmbH"


def test_upload_customer_too_long_returns_400() -> None:
    """customer field longer than 100 characters → 400."""
    client = _make_client()
    long_name = "A" * 101
    response = client.post(
        "/upload",
        data={"customer": long_name},
        files={"file": ("bom.pdf", _MINIMAL_PDF, "application/pdf")},
    )
    assert response.status_code == 400
    detail = response.json().get("detail", "")
    assert "100" in detail or "Zeichen" in detail


def test_upload_without_customer_defaults_empty() -> None:
    """Omitting the customer field stores empty string (no error)."""
    with patch("src.api.routes.upload.job_queue") as mock_queue, patch(
        "src.api.routes.upload.job_store"
    ) as mock_store:
        mock_queue.submit = AsyncMock(return_value=None)

        created_jobs: list[dict] = []

        def fake_create(job_id, filename, filepath, *, customer=""):
            created_jobs.append({"customer": customer})
            from unittest.mock import MagicMock

            job = MagicMock()
            job.job_id = job_id
            job.filename = filename
            return job

        mock_store.create.side_effect = fake_create

        client = _make_client()
        response = client.post(
            "/upload",
            files={"file": ("bom.pdf", _MINIMAL_PDF, "application/pdf")},
        )

    assert response.status_code != 400, response.json()
    assert len(created_jobs) == 1
    assert created_jobs[0]["customer"] == ""
