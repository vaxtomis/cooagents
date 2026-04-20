"""JWT authentication for the cooagents API.

Single-user model: credentials come from environment variables loaded at
startup. Tokens are JWTs stored in httpOnly cookies (Secure + SameSite=Lax)
so the frontend never touches the raw token and XSS cannot exfiltrate it.

Env contract (fail-fast on startup if any missing):
    ADMIN_USERNAME          - login username
    ADMIN_PASSWORD_HASH     - argon2 hash of the password
    JWT_SECRET              - HMAC signing key (min 32 chars)

Optional:
    AGENT_API_TOKEN         - service-account token for agent-to-api calls
                              (min 32 chars). Presented via X-Agent-Token
                              header. Bypasses the user login flow so
                              background agents (OpenClaw, schedulers, etc.)
                              can call the API without interactive auth.
    COOAGENTS_ALLOW_INSECURE_COOKIES - "1" disables Secure flag for local http dev
    ACCESS_TOKEN_TTL_MIN    - default 15, clamped to [1, 60]
    REFRESH_TOKEN_TTL_DAYS  - default 7,  clamped to [1, 30]

Refresh rotation: refresh tokens carry a jti. The latest valid jti per user is
held in an in-memory map; using a refresh token both consumes the old jti and
issues a new one. Stolen refresh cookies are invalidated the moment the
legitimate owner refreshes next. (Trade-off: sessions are single-node; a
multi-instance deployment needs a shared store.)
"""
from __future__ import annotations

import os
import secrets
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Cookie, Depends, Header, Request
from fastapi.responses import Response


ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
AGENT_TOKEN_HEADER = "x-agent-token"
AGENT_SUBJECT = "agent"  # pseudo-username recorded for agent-initiated actions
JWT_ALG = "HS256"

_hasher = PasswordHasher()

# Precomputed dummy hash used when the submitted username does not exist, so
# the unknown-user path spends ~the same time as the known-user wrong-password
# path. Verified once here at import time.
_DUMMY_PASSWORD_HASH = _hasher.hash("__cooagents_dummy_hash_not_a_real_password__")

# Clamp bounds for TTL env overrides.
_ACCESS_MIN_MINUTES = 1
_ACCESS_MAX_MINUTES = 60
_REFRESH_MIN_DAYS = 1
_REFRESH_MAX_DAYS = 30


class AuthConfigError(RuntimeError):
    """Raised when required auth env vars are missing or invalid."""


class AuthError(Exception):
    """Raised for authentication failures (bad credentials, expired token)."""

    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class AuthSettings:
    """Resolved auth configuration. Never hold plaintext credentials."""
    username: str
    password_hash: str
    jwt_secret: str
    cookie_secure: bool
    access_ttl: timedelta
    refresh_ttl: timedelta
    # Optional static token used by local agents to authenticate without going
    # through the interactive login flow. Empty string disables the path.
    agent_api_token: str = ""

    @classmethod
    def from_env(cls) -> "AuthSettings":
        username = os.environ.get("ADMIN_USERNAME", "").strip()
        pwd_hash = os.environ.get("ADMIN_PASSWORD_HASH", "").strip()
        jwt_secret = os.environ.get("JWT_SECRET", "").strip()
        missing = [
            name for name, val in [
                ("ADMIN_USERNAME", username),
                ("ADMIN_PASSWORD_HASH", pwd_hash),
                ("JWT_SECRET", jwt_secret),
            ] if not val
        ]
        if missing:
            raise AuthConfigError(
                "Missing required auth env vars: " + ", ".join(missing)
                + ". Generate a password hash with `python scripts/generate_password_hash.py`."
            )
        if len(jwt_secret) < 32:
            raise AuthConfigError(
                "JWT_SECRET must be at least 32 characters. "
                "Generate one with `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
            )
        if not pwd_hash.startswith("$argon2"):
            raise AuthConfigError(
                "ADMIN_PASSWORD_HASH is not an argon2 hash. "
                "Regenerate via `python scripts/generate_password_hash.py`."
            )
        # Default secure=True. Only `COOAGENTS_ALLOW_INSECURE_COOKIES=1`
        # (explicit opt-in) downgrades, and we print a stderr warning on
        # startup so nobody flips this by accident in production.
        insecure_flag = os.environ.get("COOAGENTS_ALLOW_INSECURE_COOKIES", "0") == "1"
        cookie_secure = not insecure_flag
        if insecure_flag:
            print(
                "[cooagents] WARNING: COOAGENTS_ALLOW_INSECURE_COOKIES=1 set. "
                "Auth cookies will be sent over unencrypted HTTP. "
                "NEVER enable this in production.",
                file=sys.stderr,
                flush=True,
            )
        try:
            access_min = int(os.environ.get("ACCESS_TOKEN_TTL_MIN", "15"))
        except ValueError:
            access_min = 15
        try:
            refresh_days = int(os.environ.get("REFRESH_TOKEN_TTL_DAYS", "7"))
        except ValueError:
            refresh_days = 7
        access_min = max(_ACCESS_MIN_MINUTES, min(access_min, _ACCESS_MAX_MINUTES))
        refresh_days = max(_REFRESH_MIN_DAYS, min(refresh_days, _REFRESH_MAX_DAYS))
        agent_token = os.environ.get("AGENT_API_TOKEN", "").strip()
        if agent_token and len(agent_token) < 32:
            raise AuthConfigError(
                "AGENT_API_TOKEN must be at least 32 characters. "
                "Generate with `python -c \"import secrets; print(secrets.token_urlsafe(32))\"`."
            )
        return cls(
            username=username,
            password_hash=pwd_hash,
            jwt_secret=jwt_secret,
            cookie_secure=cookie_secure,
            access_ttl=timedelta(minutes=access_min),
            refresh_ttl=timedelta(days=refresh_days),
            agent_api_token=agent_token,
        )


def hash_password(plain: str) -> str:
    """Hash a plaintext password with argon2 (use from CLI helper only)."""
    if not plain:
        raise ValueError("Password cannot be empty")
    return _hasher.hash(plain)


def verify_password(plain: str, expected_hash: str) -> bool:
    try:
        _hasher.verify(expected_hash, plain)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_token(
    *,
    subject: str,
    secret: str,
    ttl: timedelta,
    token_type: Literal["access", "refresh"],
    jti: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        # 128-bit jti so the revocation/rotation space can't collide.
        "jti": jti or secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALG)


# ---------------------------------------------------------------------------
# Refresh-token rotation store
#
# Tracks the currently valid refresh jti per subject. The store is a simple
# thread-safe dict; single-node only. On process restart all sessions are
# invalidated, which is the desired behaviour for a single-operator deployment.
# ---------------------------------------------------------------------------

_refresh_lock = threading.Lock()
_refresh_jtis: dict[str, str] = {}


def register_refresh_jti(subject: str, jti: str) -> None:
    with _refresh_lock:
        _refresh_jtis[subject] = jti


def consume_refresh_jti(subject: str, jti: str) -> bool:
    """Return True if *jti* matches the currently valid one for *subject*.

    Atomically removes the mapping so the caller must immediately issue a new
    jti if the refresh is to succeed. Why: prevents a race where two clients
    (legit + attacker) both hold the same stolen refresh cookie and race.
    """
    with _refresh_lock:
        current = _refresh_jtis.get(subject)
        if current and secrets.compare_digest(current, jti):
            del _refresh_jtis[subject]
            return True
        return False


def invalidate_refresh(subject: str) -> None:
    with _refresh_lock:
        _refresh_jtis.pop(subject, None)


def decode_token(token: str, secret: str, *, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, secret, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise AuthError("Token expired")
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"Invalid token: {exc}")
    if payload.get("type") != expected_type:
        raise AuthError(f"Expected {expected_type} token")
    return payload


def _common_cookie_kwargs(auth_cfg: AuthSettings) -> dict:
    return {
        "httponly": True,
        "secure": auth_cfg.cookie_secure,
        "samesite": "lax",
        "path": "/",
    }


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    auth_cfg: AuthSettings,
) -> None:
    common = _common_cookie_kwargs(auth_cfg)
    response.set_cookie(
        ACCESS_COOKIE, access_token,
        max_age=int(auth_cfg.access_ttl.total_seconds()),
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE, refresh_token,
        max_age=int(auth_cfg.refresh_ttl.total_seconds()),
        **common,
    )


def set_access_cookie(response: Response, access_token: str, auth_cfg: AuthSettings) -> None:
    common = _common_cookie_kwargs(auth_cfg)
    response.set_cookie(
        ACCESS_COOKIE, access_token,
        max_age=int(auth_cfg.access_ttl.total_seconds()),
        **common,
    )


def clear_auth_cookies(response: Response, auth_cfg: AuthSettings | None = None) -> None:
    # Some browsers ignore a deletion Set-Cookie that does not carry the
    # same attributes that set the cookie; carry Secure/SameSite through.
    secure = auth_cfg.cookie_secure if auth_cfg else True
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=secure, samesite="lax", httponly=True)
    response.delete_cookie(REFRESH_COOKIE, path="/", secure=secure, samesite="lax", httponly=True)


def authenticate(username: str, password: str, auth_cfg: AuthSettings) -> None:
    """Raise AuthError if credentials don't match the configured admin."""
    # Constant-time username comparison; password check via argon2 is already
    # constant-time internally.
    if not secrets.compare_digest(username, auth_cfg.username):
        # Verify against a precomputed dummy hash so unknown-user and
        # known-user paths both spend a full argon2 verify cycle.
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise AuthError("Invalid credentials")
    if not verify_password(password, auth_cfg.password_hash):
        raise AuthError("Invalid credentials")


def issue_token_pair(username: str, auth_cfg: AuthSettings) -> tuple[str, str]:
    refresh_jti = secrets.token_urlsafe(16)
    access = create_token(
        subject=username,
        secret=auth_cfg.jwt_secret,
        ttl=auth_cfg.access_ttl,
        token_type="access",
    )
    refresh = create_token(
        subject=username,
        secret=auth_cfg.jwt_secret,
        ttl=auth_cfg.refresh_ttl,
        token_type="refresh",
        jti=refresh_jti,
    )
    register_refresh_jti(username, refresh_jti)
    return access, refresh


def rotate_refresh_token(username: str, auth_cfg: AuthSettings) -> tuple[str, str]:
    """Issue a fresh access + refresh pair and replace the registered jti.

    Caller must have already validated the incoming refresh token via
    `consume_refresh_jti`; this function is unsafe to call otherwise.
    """
    new_refresh_jti = secrets.token_urlsafe(16)
    access = create_token(
        subject=username,
        secret=auth_cfg.jwt_secret,
        ttl=auth_cfg.access_ttl,
        token_type="access",
    )
    refresh = create_token(
        subject=username,
        secret=auth_cfg.jwt_secret,
        ttl=auth_cfg.refresh_ttl,
        token_type="refresh",
        jti=new_refresh_jti,
    )
    register_refresh_jti(username, new_refresh_jti)
    return access, refresh


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

def _extract_access_token(
    access_cookie: str | None,
    authorization: str | None,
) -> str | None:
    if access_cookie:
        return access_cookie
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            return value.strip()
    return None


async def get_current_user(
    request: Request,
    access_cookie: str | None = Cookie(default=None, alias=ACCESS_COOKIE),
    authorization: str | None = Header(default=None),
    agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
) -> str:
    """Validate the incoming request and return the authenticated principal.

    Order:
      1. X-Agent-Token (service-account path for local agents)
      2. access_token cookie (browser flow)
      3. Authorization: Bearer ... (API clients)
    """
    auth_cfg = getattr(request.app.state, "auth", None)
    if auth_cfg is None:
        # Auth not configured — refuse requests rather than accept them silently.
        raise AuthError("Authentication is not configured on this server", 503)

    if agent_token and auth_cfg.agent_api_token:
        # Constant-time compare so a probing attacker cannot time-derive the token.
        if secrets.compare_digest(agent_token.strip(), auth_cfg.agent_api_token):
            return AGENT_SUBJECT
        raise AuthError("Invalid agent token")

    token = _extract_access_token(access_cookie, authorization)
    if not token:
        raise AuthError("Authentication required")
    payload = decode_token(token, auth_cfg.jwt_secret, expected_type="access")
    return str(payload["sub"])
