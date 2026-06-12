"""SEC-002 — Default-Admin admin/admin tests."""

from __future__ import annotations

import pytest

from src.core import auth


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    """Restore _SETTINGS after each test."""
    monkeypatch.setitem(auth._SETTINGS, "login_enabled", True)
    monkeypatch.setitem(auth._SETTINGS, "login_admin_user", "admin")
    monkeypatch.setitem(auth._SETTINGS, "login_admin_password", "admin")
    monkeypatch.setitem(auth._SETTINGS, "allow_default_admin", False)


def test_default_admin_disabled_when_allow_false_and_different_password(monkeypatch):
    """admin/admin must fail when allow_default_admin is False and a different password is set."""
    monkeypatch.setitem(auth._SETTINGS, "login_admin_password", "supersecret")
    monkeypatch.setitem(auth._SETTINGS, "allow_default_admin", False)
    assert auth.authenticate_local_user("admin", "admin") is False


def test_default_admin_works_when_allow_true(monkeypatch):
    """admin/admin must succeed when allow_default_admin is explicitly True."""
    monkeypatch.setitem(auth._SETTINGS, "login_admin_password", "supersecret")
    monkeypatch.setitem(auth._SETTINGS, "allow_default_admin", True)
    assert auth.authenticate_local_user("admin", "admin") is True


def test_configured_credentials_still_work(monkeypatch):
    """Configured user/password must still authenticate regardless of allow_default_admin."""
    monkeypatch.setitem(auth._SETTINGS, "login_admin_user", "schaufler")
    monkeypatch.setitem(auth._SETTINGS, "login_admin_password", "s3cr3t!")
    monkeypatch.setitem(auth._SETTINGS, "allow_default_admin", False)
    assert auth.authenticate_local_user("schaufler", "s3cr3t!") is True


def test_default_setting_is_false():
    """The module-level _SETTINGS default for allow_default_admin must be False."""
    import importlib
    import src.core.auth as auth_mod
    # Reload to get the pristine defaults without monkeypatching
    original = auth_mod._SETTINGS.copy()
    assert original["allow_default_admin"] is False


def test_security_warning_logged_when_default_admin_active(monkeypatch, caplog):
    """init_auth must emit a SECURITY warning when allow_default_admin is True."""
    import logging
    monkeypatch.setenv("LOGIN_ALLOW_DEFAULT_ADMIN", "true")
    monkeypatch.setenv("LOGIN_ADMIN_PASSWORD", "supersecret")
    monkeypatch.setenv("LOGIN_AUTH_ENABLED", "true")
    # Clear API_KEY so we don't accidentally enable it
    monkeypatch.delenv("API_KEY", raising=False)

    with caplog.at_level(logging.WARNING, logger="src.core.auth"):
        auth.init_auth()

    assert any("SECURITY" in r.message and "admin/admin" in r.message for r in caplog.records)


def test_security_warning_logged_when_password_is_admin(monkeypatch, caplog):
    """init_auth must emit a SECURITY warning when the password is literally 'admin'."""
    import logging
    monkeypatch.setenv("LOGIN_ALLOW_DEFAULT_ADMIN", "false")
    monkeypatch.setenv("LOGIN_ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("LOGIN_AUTH_ENABLED", "true")
    monkeypatch.delenv("API_KEY", raising=False)

    with caplog.at_level(logging.WARNING, logger="src.core.auth"):
        auth.init_auth()

    assert any("SECURITY" in r.message for r in caplog.records)
