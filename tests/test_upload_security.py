"""Sprint 1 — SEC-2 (filename sanitisation).

AW-1 (background-task reference) is now covered by the job queue owning its
worker tasks for the app lifetime; see test_job_queue.py.
"""

from __future__ import annotations

import pytest

from src.api.routes import upload


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    """Keep these endpoint tests independent of the auth module's global state
    (same pattern as test_job_source_route.py)."""
    from src.core import auth

    monkeypatch.setitem(auth._SETTINGS, "login_enabled", False)
    monkeypatch.setitem(auth._SETTINGS, "api_key_enabled", False)


# --- SEC-2: filename sanitisation -----------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("normal.pdf", "normal.pdf"),
        ("../../etc/passwd", "passwd"),
        (r"..\..\windows\system32\evil.xlsx", "evil.xlsx"),
        ("sub/dir/file.csv", "file.csv"),
        (r"C:\abs\path\bom.pdf", "bom.pdf"),
    ],
)
def test_safe_filename_strips_path_components(raw: str, expected: str) -> None:
    assert upload._safe_filename(raw) == expected


def test_safe_filename_pure_traversal_is_empty() -> None:
    # A name that is only separators / dots collapses to "" → caller rejects it.
    assert upload._safe_filename("../../") == ""


# --- BUG-019: .xls blocked with actionable message --------------------------


def test_xls_upload_returns_400_with_helpful_message() -> None:
    """.xls uploads are rejected at the extension check with HTTP 400."""
    from fastapi.testclient import TestClient
    from src.api.main import app

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/upload",
        files={"file": ("legacy.xls", b"\xd0\xcf\x11\xe0", "application/octet-stream")},
    )
    assert response.status_code == 400
    detail = response.json().get("detail", "")
    assert ".xls" in detail
    assert ".xlsx" in detail


# --- SEC-005: magic-byte validation ------------------------------------------


def test_non_pdf_content_named_pdf_returns_400() -> None:
    """A file with .pdf extension but MZ (PE) magic bytes must be rejected with 400."""
    from fastapi.testclient import TestClient
    from src.api.main import app

    client = TestClient(app, raise_server_exceptions=False)
    # MZ header (Windows PE) masquerading as PDF
    fake_pdf = b"MZ" + b"\x00" * 100
    response = client.post(
        "/upload",
        files={"file": ("malicious.pdf", fake_pdf, "application/pdf")},
    )
    assert response.status_code == 400
    assert ".pdf" in response.json().get("detail", "")


def test_valid_pdf_magic_passes_magic_check() -> None:
    """A file starting with %PDF- must not be rejected on magic-byte grounds.

    The job-creation step may still fail (no real PDF content), but the
    status code must not be 400 with the magic-byte error message.
    """
    from unittest.mock import AsyncMock, patch
    from fastapi.testclient import TestClient
    from src.api.main import app

    # Patch job_queue.submit so we don't need a running worker
    with patch("src.api.routes.upload.job_queue") as mock_queue:
        mock_queue.submit = AsyncMock(return_value=None)
        client = TestClient(app, raise_server_exceptions=False)
        minimal_pdf = b"%PDF-1.4 fake content"
        response = client.post(
            "/upload",
            files={"file": ("bom.pdf", minimal_pdf, "application/pdf")},
        )

    # Must not be 400 with the magic-byte rejection message
    if response.status_code == 400:
        assert "entspricht nicht" not in response.json().get("detail", "")


def test_non_xlsx_content_named_xlsx_returns_400() -> None:
    """A file with .xlsx extension but no PK magic bytes must be rejected."""
    from fastapi.testclient import TestClient
    from src.api.main import app

    client = TestClient(app, raise_server_exceptions=False)
    fake_xlsx = b"This is not a zip file at all"
    response = client.post(
        "/upload",
        files={"file": ("bom.xlsx", fake_xlsx, "application/octet-stream")},
    )
    assert response.status_code == 400
    assert ".xlsx" in response.json().get("detail", "")
