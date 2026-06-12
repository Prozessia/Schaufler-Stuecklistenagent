"""SEC-003 — CSRF double-submit cookie tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core import auth


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _enable_login(monkeypatch):
    """Enable login auth with known credentials; disable API-key auth."""
    monkeypatch.setitem(auth._SETTINGS, "login_enabled", True)
    monkeypatch.setitem(auth._SETTINGS, "login_admin_user", "admin")
    monkeypatch.setitem(auth._SETTINGS, "login_admin_password", "testpass")
    monkeypatch.setitem(auth._SETTINGS, "allow_default_admin", False)
    monkeypatch.setitem(auth._SETTINGS, "api_key_enabled", False)
    monkeypatch.setitem(auth._SETTINGS, "session_cookie_name", "bom_session")
    monkeypatch.setitem(auth._SETTINGS, "session_ttl_seconds", 28800)
    monkeypatch.setitem(auth._SETTINGS, "session_cookie_secure", False)


def _do_login(client: TestClient) -> tuple[str, str]:
    """Login and return (session_token, csrf_token)."""
    resp = client.post("/auth/login", json={"username": "admin", "password": "testpass"})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    session = resp.cookies.get("bom_session", "")
    csrf = resp.cookies.get("csrf_token", "")
    return session, csrf


def test_login_sets_csrf_cookie(client):
    """POST /auth/login must set the csrf_token cookie."""
    _, csrf = _do_login(client)
    assert csrf, "csrf_token cookie must be set after login"


def test_mutating_request_without_csrf_header_returns_403(client):
    """A session-authenticated DELETE without X-CSRF-Token must return 403."""
    session, csrf = _do_login(client)
    assert csrf

    resp = client.delete(
        "/jobs/nonexistent",
        cookies={"bom_session": session, "csrf_token": csrf},
        # No X-CSRF-Token header
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"


def test_mutating_request_with_correct_csrf_header_is_not_403(client):
    """A session-authenticated DELETE with matching X-CSRF-Token must NOT return 403.

    The actual status (404, 422, etc.) depends on the endpoint — we only verify
    that CSRF validation itself is not the reason for rejection.
    """
    session, csrf = _do_login(client)
    assert csrf

    resp = client.delete(
        "/jobs/nonexistent",
        cookies={"bom_session": session, "csrf_token": csrf},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code != 403, (
        f"Got 403 — CSRF check blocked a valid token: {resp.text}"
    )


def test_get_without_csrf_header_is_allowed(client):
    """GET requests must pass CSRF validation without any X-CSRF-Token header."""
    session, csrf = _do_login(client)

    resp = client.get(
        "/jobs/nonexistent",
        cookies={"bom_session": session, "csrf_token": csrf},
    )
    # Not 403 (CSRF); 404 is expected for a nonexistent job
    assert resp.status_code != 403


def test_logout_clears_csrf_cookie(client):
    """POST /auth/logout must delete the csrf_token cookie."""
    session, csrf = _do_login(client)
    assert csrf

    resp = client.post(
        "/auth/logout",
        cookies={"bom_session": session, "csrf_token": csrf},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    # Cookie should be cleared (empty value or absent)
    cleared = resp.cookies.get("csrf_token", None)
    assert cleared in (None, ""), f"csrf_token not cleared: {cleared!r}"
