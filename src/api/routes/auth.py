"""Authentication routes for login/session management."""

from __future__ import annotations

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

router = APIRouter(prefix="/auth")


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUserResponse(BaseModel):
    username: str


class LogoutResponse(BaseModel):
    ok: bool = True


@router.post("/login", response_model=AuthUserResponse)
async def login(payload: LoginRequest, response: Response):
    """Login with local admin credentials and issue a session cookie."""
    if not login_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login authentication is disabled",
        )

    username = payload.username.strip()
    if not authenticate_local_user(username, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

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
    return LogoutResponse(ok=True)
