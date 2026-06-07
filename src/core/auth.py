"""Authentication helpers for API key and cookie-session based login."""

from __future__ import annotations

import logging
import os
import secrets
import time
from threading import Lock

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

_SETTINGS: dict[str, str | bool | int | None] = {
    "api_key": None,
    "api_key_enabled": False,
    "login_enabled": True,
    "login_admin_user": "admin",
    "login_admin_password": "admin",
    "allow_default_admin": True,
    "session_cookie_name": "bom_session",
    "session_ttl_seconds": 8 * 60 * 60,
    "session_cookie_secure": False,
}

_SESSIONS: dict[str, tuple[str, float]] = {}
_SESSIONS_LOCK = Lock()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _session_cleanup(now_ts: float) -> None:
    expired = [token for token, (_, exp) in _SESSIONS.items() if exp <= now_ts]
    for token in expired:
        _SESSIONS.pop(token, None)


def init_auth() -> None:
    """Read auth configuration from environment and log active auth modes.

    Call once at application startup (before requests are served).
    """
    raw = os.environ.get("API_KEY", "").strip()
    if raw:
        _SETTINGS["api_key"] = raw
        _SETTINGS["api_key_enabled"] = True
        logger.info("API key authentication: ENABLED")
    else:
        _SETTINGS["api_key"] = None
        _SETTINGS["api_key_enabled"] = False
        logger.warning("API key authentication: DISABLED (dev mode)")

    _SETTINGS["login_enabled"] = _env_bool("LOGIN_AUTH_ENABLED", True)
    _SETTINGS["login_admin_user"] = (
        os.environ.get("LOGIN_ADMIN_USER", "admin").strip() or "admin"
    )
    _SETTINGS["login_admin_password"] = os.environ.get("LOGIN_ADMIN_PASSWORD", "admin")
    _SETTINGS["allow_default_admin"] = _env_bool("LOGIN_ALLOW_DEFAULT_ADMIN", True)
    _SETTINGS["session_cookie_name"] = (
        os.environ.get("SESSION_COOKIE_NAME", "bom_session").strip() or "bom_session"
    )
    _SETTINGS["session_ttl_seconds"] = max(
        60,
        int(os.environ.get("SESSION_TTL_SECONDS", str(8 * 60 * 60))),
    )
    _SETTINGS["session_cookie_secure"] = _env_bool("SESSION_COOKIE_SECURE", False)

    if bool(_SETTINGS["login_enabled"]):
        logger.info(
            "Login authentication: ENABLED (user=%s, cookie=%s, ttl=%ss)",
            _SETTINGS["login_admin_user"],
            _SETTINGS["session_cookie_name"],
            _SETTINGS["session_ttl_seconds"],
        )
    else:
        logger.warning("Login authentication: DISABLED")


def login_enabled() -> bool:
    return bool(_SETTINGS["login_enabled"])


def session_cookie_name() -> str:
    return str(_SETTINGS["session_cookie_name"])


def session_cookie_secure() -> bool:
    return bool(_SETTINGS["session_cookie_secure"])


def session_ttl_seconds() -> int:
    return int(_SETTINGS["session_ttl_seconds"])


def authenticate_local_user(username: str, password: str) -> bool:
    if not bool(_SETTINGS["login_enabled"]):
        return False
    configured_match = secrets.compare_digest(
        username.strip(),
        str(_SETTINGS["login_admin_user"]),
    ) and secrets.compare_digest(
        password,
        str(_SETTINGS["login_admin_password"]),
    )
    default_admin_match = (
        bool(_SETTINGS["allow_default_admin"])
        and secrets.compare_digest(username.strip(), "admin")
        and secrets.compare_digest(password, "admin")
    )
    return configured_match or default_admin_match


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + int(_SETTINGS["session_ttl_seconds"])
    with _SESSIONS_LOCK:
        _session_cleanup(time.time())
        _SESSIONS[token] = (username, expires_at)
    return token


def get_session_user(token: str) -> str | None:
    if not token:
        return None
    now_ts = time.time()
    with _SESSIONS_LOCK:
        _session_cleanup(now_ts)
        item = _SESSIONS.get(token)
        if not item:
            return None
        user, _ = item
        return user


def invalidate_session(token: str) -> None:
    if not token:
        return
    with _SESSIONS_LOCK:
        _SESSIONS.pop(token, None)


def _is_public_path(path: str) -> bool:
    return path in {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/auth/login",
        "/auth/logout",
    }


async def verify_api_key(request: Request) -> None:
    """FastAPI dependency — allow login session or API key; else reject.

    Public paths remain exempt. With LOGIN_AUTH_ENABLED (default), a valid
    session cookie is required for protected endpoints unless a valid API key
    is provided.
    """
    path = request.url.path
    if _is_public_path(path):
        return

    if bool(_SETTINGS["login_enabled"]):
        token = request.cookies.get(str(_SETTINGS["session_cookie_name"]), "")
        if token and get_session_user(token):
            return

    if bool(_SETTINGS["api_key_enabled"]):
        provided = request.headers.get("X-API-Key", "")
        if provided == _SETTINGS["api_key"]:
            return

    if bool(_SETTINGS["login_enabled"]) or bool(_SETTINGS["api_key_enabled"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
