"""Authentication endpoints: login, logout, refresh, me."""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter

from src.auth import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    AuthError,
    authenticate,
    clear_auth_cookies,
    consume_refresh_jti,
    create_token,
    decode_token,
    get_current_user,
    issue_token_pair,
    rotate_refresh_token,
    set_auth_cookies,
)
from fastapi import Depends
from src.request_utils import assert_same_origin, client_ip

limiter = Limiter(key_func=client_ip)

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
@limiter.limit("5/minute")
async def login(req: LoginRequest, request: Request):
    auth_cfg = request.app.state.auth
    authenticate(req.username, req.password, auth_cfg)
    access, refresh = issue_token_pair(req.username, auth_cfg)
    response = JSONResponse(content={"username": req.username})
    set_auth_cookies(response, access_token=access, refresh_token=refresh, auth_cfg=auth_cfg)
    return response


@router.post("/auth/logout")
async def logout(
    request: Request,
    current_user: str = Depends(get_current_user),
):
    # Origin check blocks cross-site force-logout via fetch with credentials.
    assert_same_origin(request)
    # Drop the server-side refresh jti so a stolen cookie cannot be used.
    from src.auth import invalidate_refresh
    invalidate_refresh(current_user)
    auth_cfg = request.app.state.auth
    response = JSONResponse(content={"ok": True})
    clear_auth_cookies(response, auth_cfg)
    return response


@router.post("/auth/refresh")
@limiter.limit("30/minute")
async def refresh(
    request: Request,
    refresh_cookie: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
):
    # Reject cross-site refresh attempts; see H-2 in 2026-04-20 review.
    assert_same_origin(request)
    auth_cfg = request.app.state.auth
    if not refresh_cookie:
        raise AuthError("Refresh token required")
    payload = decode_token(refresh_cookie, auth_cfg.jwt_secret, expected_type="refresh")
    username = str(payload["sub"])
    old_jti = str(payload.get("jti") or "")

    # Rotate: the old jti must be the currently-valid one, and using it
    # immediately invalidates it so a stolen copy can only be used once.
    if not consume_refresh_jti(username, old_jti):
        raise AuthError("Refresh token was revoked or already used")

    access, new_refresh = rotate_refresh_token(username, auth_cfg)
    response = JSONResponse(content={"username": username})
    set_auth_cookies(response, access_token=access, refresh_token=new_refresh, auth_cfg=auth_cfg)
    return response


@router.get("/auth/me")
async def me(user: str = Depends(get_current_user)):
    return {"username": user}
