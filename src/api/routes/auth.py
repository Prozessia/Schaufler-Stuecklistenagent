"""Authentication routes for login/session management."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from src.core.auth import (
    authenticate_local_user,
    create_session,
    get_session_user,
    invalidate_session,
    login_enabled,
    session_cookie_name,
    session_cookie_secure,
    session_ttl_seconds,
)
from src.core.rate_limit import LoginLockout, RateLimiter

router = APIRouter(prefix="/auth")

# SEC-004: module-level singletons (one per process, reset at restart)
_login_rate_limiter = RateLimiter(max_attempts=10, window_seconds=60)
_login_lockout = LoginLockout(max_failures=8, lockout_seconds=900)


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUserResponse(BaseModel):
    username: str


class LogoutResponse(BaseModel):
    ok: bool = True


@router.post("/login", response_model=AuthUserResponse)
async def login(payload: LoginRequest, request: Request, response: Response):
    """Login with local admin credentials and issue a session cookie."""
    if not login_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login authentication is disabled",
        )

    client_ip = (request.client.host if request.client else None) or "unknown"
    username = payload.username.strip()
    lockout_key = f"{client_ip}:{username}"

    # SEC-004: per-IP rate limit (10 attempts / 60 s)
    if not _login_rate_limiter.allow(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Zu viele Login-Versuche — bitte warten.",
        )

    # SEC-004: per-(IP, user) lockout after 8 failures
    if _login_lockout.is_locked(lockout_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Konto vorübergehend gesperrt — bitte später erneut versuchen.",
        )

    if not authenticate_local_user(username, payload.password):
        _login_lockout.register_failure(lockout_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    _login_lockout.reset(lockout_key)

    token = create_session(username)
    response.set_cookie(
        key=session_cookie_name(),
        value=token,
        httponly=True,
        secure=session_cookie_secure(),
        samesite="lax",
        max_age=session_ttl_seconds(),
        path="/",
    )
    # SEC-003: CSRF double-submit cookie (httponly=False so JS can read it)
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        secure=session_cookie_secure(),
        samesite="lax",
        max_age=session_ttl_seconds(),
        path="/",
    )
    return AuthUserResponse(username=username)


@router.get("/me", response_model=AuthUserResponse)
async def me(request: Request):
    """Return current authenticated user from session cookie."""
    token = request.cookies.get(session_cookie_name(), "")
    user = get_session_user(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )
    return AuthUserResponse(username=user)


@router.post("/logout", response_model=LogoutResponse)
async def logout(request: Request, response: Response):
    """Invalidate the current session and clear cookie."""
    token = request.cookies.get(session_cookie_name(), "")
    if token:
        invalidate_session(token)

    response.delete_cookie(
        key=session_cookie_name(),
        path="/",
        secure=session_cookie_secure(),
        samesite="lax",
    )
    # SEC-003: clear CSRF cookie on logout
    response.delete_cookie(
        key="csrf_token",
        path="/",
        secure=session_cookie_secure(),
        samesite="lax",
    )
    return LogoutResponse(ok=True)
