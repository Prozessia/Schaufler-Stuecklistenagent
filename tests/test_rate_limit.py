"""SEC-004 — RateLimiter and LoginLockout unit tests + login integration."""

from __future__ import annotations

import time

import pytest

from src.core.rate_limit import LoginLockout, RateLimiter


# ---------------------------------------------------------------------------
# RateLimiter unit tests
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_up_to_max(monkeypatch):
    limiter = RateLimiter(max_attempts=3, window_seconds=60)
    # Patch time so all calls land in the same window
    monkeypatch.setattr("src.core.rate_limit.time.monotonic", lambda: 1000.0)
    assert limiter.allow("ip1") is True
    assert limiter.allow("ip1") is True
    assert limiter.allow("ip1") is True
    # 4th call exceeds the limit
    assert limiter.allow("ip1") is False


def test_rate_limiter_window_expiry(monkeypatch):
    limiter = RateLimiter(max_attempts=2, window_seconds=10)
    t = [1000.0]
    monkeypatch.setattr("src.core.rate_limit.time.monotonic", lambda: t[0])

    limiter.allow("ip1")
    limiter.allow("ip1")
    assert limiter.allow("ip1") is False  # over limit

    # Advance time past the window
    t[0] = 1011.0
    assert limiter.allow("ip1") is True  # window reset


def test_rate_limiter_different_keys_are_independent(monkeypatch):
    limiter = RateLimiter(max_attempts=1, window_seconds=60)
    monkeypatch.setattr("src.core.rate_limit.time.monotonic", lambda: 1000.0)
    assert limiter.allow("ip1") is True
    assert limiter.allow("ip1") is False
    # ip2 has its own bucket — must still be allowed
    assert limiter.allow("ip2") is True


# ---------------------------------------------------------------------------
# LoginLockout unit tests
# ---------------------------------------------------------------------------


def test_lockout_not_triggered_before_max_failures(monkeypatch):
    lockout = LoginLockout(max_failures=3, lockout_seconds=60)
    monkeypatch.setattr("src.core.rate_limit.time.monotonic", lambda: 1000.0)
    lockout.register_failure("k")
    lockout.register_failure("k")
    assert lockout.is_locked("k") is False


def test_lockout_triggered_at_max_failures(monkeypatch):
    lockout = LoginLockout(max_failures=3, lockout_seconds=60)
    monkeypatch.setattr("src.core.rate_limit.time.monotonic", lambda: 1000.0)
    lockout.register_failure("k")
    lockout.register_failure("k")
    lockout.register_failure("k")
    assert lockout.is_locked("k") is True


def test_lockout_expires_after_lockout_seconds(monkeypatch):
    lockout = LoginLockout(max_failures=2, lockout_seconds=30)
    t = [1000.0]
    monkeypatch.setattr("src.core.rate_limit.time.monotonic", lambda: t[0])
    lockout.register_failure("k")
    lockout.register_failure("k")
    assert lockout.is_locked("k") is True

    t[0] = 1031.0  # past lockout
    assert lockout.is_locked("k") is False


def test_lockout_reset_clears_failures(monkeypatch):
    lockout = LoginLockout(max_failures=2, lockout_seconds=60)
    monkeypatch.setattr("src.core.rate_limit.time.monotonic", lambda: 1000.0)
    lockout.register_failure("k")
    lockout.register_failure("k")
    assert lockout.is_locked("k") is True
    lockout.reset("k")
    assert lockout.is_locked("k") is False


# ---------------------------------------------------------------------------
# Integration: 11th login attempt → 429
# ---------------------------------------------------------------------------


def test_eleventh_login_attempt_returns_429(monkeypatch):
    """After 10 login attempts in one window, the 11th must return 429."""
    from fastapi.testclient import TestClient
    from src.api.main import app
    from src.core import auth
    import src.api.routes.auth as auth_route

    # Use fresh limiter instances so this test doesn't share state with others
    fresh_limiter = RateLimiter(max_attempts=10, window_seconds=60)
    fresh_lockout = LoginLockout(max_failures=100, lockout_seconds=900)  # high — don't trigger
    monkeypatch.setattr(auth_route, "_login_rate_limiter", fresh_limiter)
    monkeypatch.setattr(auth_route, "_login_lockout", fresh_lockout)

    # Enable login auth with a different password so all attempts fail on credentials
    monkeypatch.setitem(auth._SETTINGS, "login_enabled", True)
    monkeypatch.setitem(auth._SETTINGS, "login_admin_user", "admin")
    monkeypatch.setitem(auth._SETTINGS, "login_admin_password", "correct-horse")
    monkeypatch.setitem(auth._SETTINGS, "allow_default_admin", False)
    monkeypatch.setitem(auth._SETTINGS, "api_key_enabled", False)
    monkeypatch.setitem(auth._SETTINGS, "session_cookie_name", "bom_session")
    monkeypatch.setitem(auth._SETTINGS, "session_ttl_seconds", 28800)
    monkeypatch.setitem(auth._SETTINGS, "session_cookie_secure", False)

    client = TestClient(app, raise_server_exceptions=False)

    statuses = []
    for _ in range(11):
        resp = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
        statuses.append(resp.status_code)

    # First 10 should be 401 (bad credentials), 11th should be 429
    assert statuses[-1] == 429, f"Expected 429 on 11th attempt, got {statuses[-1]}; all: {statuses}"
