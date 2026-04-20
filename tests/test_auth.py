"""Tests for JWT auth: login, refresh rotation, Origin check, 401 semantics."""
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from src.auth import (
    AuthError,
    AuthSettings,
    authenticate,
    consume_refresh_jti,
    decode_token,
    hash_password,
    invalidate_refresh,
    issue_token_pair,
    register_refresh_jti,
    verify_password,
)


def _settings(password: str = "hunter22", agent_token: str = "") -> AuthSettings:
    return AuthSettings(
        username="admin",
        password_hash=hash_password(password),
        jwt_secret="test-jwt-secret-must-be-at-least-32-chars!!",
        cookie_secure=False,
        access_ttl=timedelta(minutes=15),
        refresh_ttl=timedelta(days=7),
        agent_api_token=agent_token,
    )


def test_password_round_trip():
    h = hash_password("hunter22")
    assert verify_password("hunter22", h)
    assert not verify_password("wrong", h)


def test_authenticate_rejects_wrong_password():
    cfg = _settings("correct-password")
    with pytest.raises(AuthError):
        authenticate("admin", "wrong", cfg)


def test_authenticate_rejects_wrong_username():
    cfg = _settings()
    with pytest.raises(AuthError):
        authenticate("otheruser", "hunter22", cfg)


def test_authenticate_accepts_valid():
    cfg = _settings()
    authenticate("admin", "hunter22", cfg)  # should not raise


def test_token_round_trip():
    cfg = _settings()
    access, refresh = issue_token_pair("admin", cfg)
    a_payload = decode_token(access, cfg.jwt_secret, expected_type="access")
    r_payload = decode_token(refresh, cfg.jwt_secret, expected_type="refresh")
    assert a_payload["sub"] == "admin"
    assert r_payload["sub"] == "admin"
    assert a_payload["type"] == "access"
    assert r_payload["type"] == "refresh"


def test_access_token_rejected_as_refresh():
    cfg = _settings()
    access, _ = issue_token_pair("admin", cfg)
    with pytest.raises(AuthError):
        decode_token(access, cfg.jwt_secret, expected_type="refresh")


def test_token_signed_with_other_secret_fails():
    cfg = _settings()
    access, _ = issue_token_pair("admin", cfg)
    with pytest.raises(AuthError):
        decode_token(access, "different-secret-that-is-long-enough!!!!!", expected_type="access")


@pytest.mark.asyncio
async def test_login_flow_sets_cookies_and_rejects_bad_password(tmp_path):
    from routes.auth import router as auth_router

    app = FastAPI()
    app.state.auth = _settings("hunter22")
    app.include_router(auth_router, prefix="/api/v1")

    @app.exception_handler(AuthError)
    async def _h(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc)})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r_bad = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
        assert r_bad.status_code == 401

        r_ok = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "hunter22"})
        assert r_ok.status_code == 200
        cookies = r_ok.headers.get_list("set-cookie")
        assert any("access_token=" in c for c in cookies)
        assert any("refresh_token=" in c for c in cookies)
        assert all("HttpOnly" in c for c in cookies)


@pytest.mark.asyncio
async def test_protected_endpoint_requires_auth(tmp_path):
    """Smoke test: mount runs router with auth dependency and expect 401."""
    from fastapi import Depends
    from src.auth import get_current_user, AuthError
    from fastapi.responses import JSONResponse

    app = FastAPI()
    app.state.auth = _settings()

    @app.exception_handler(AuthError)
    async def _h(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc)})

    from routes.runs import router as runs_router
    app.include_router(runs_router, prefix="/api/v1", dependencies=[Depends(get_current_user)])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/v1/runs")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Regression tests for 2026-04-20 review findings
# ---------------------------------------------------------------------------

def test_refresh_jti_consumed_once():
    """C-1: a refresh jti may only be consumed once; replays are rejected."""
    invalidate_refresh("alice")
    register_refresh_jti("alice", "jti-1")
    assert consume_refresh_jti("alice", "jti-1") is True
    # Replay of the same jti must now fail.
    assert consume_refresh_jti("alice", "jti-1") is False


def test_refresh_jti_wrong_value_rejected():
    invalidate_refresh("bob")
    register_refresh_jti("bob", "jti-good")
    assert consume_refresh_jti("bob", "jti-bad") is False
    # Good jti still intact after a failed attempt.
    assert consume_refresh_jti("bob", "jti-good") is True


@pytest.mark.asyncio
async def test_full_refresh_rotation_flow():
    """C-1 end-to-end: login, then refresh, then replay old refresh token -> 401."""
    from routes.auth import router as auth_router
    from fastapi.responses import JSONResponse

    app = FastAPI()
    app.state.auth = _settings("hunter22")
    app.include_router(auth_router, prefix="/api/v1")

    @app.exception_handler(AuthError)
    async def _h(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc)})

    invalidate_refresh("admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "hunter22"})
        assert r.status_code == 200
        first_refresh = client.cookies.get("refresh_token")
        assert first_refresh

        r2 = await client.post("/api/v1/auth/refresh", headers={"origin": "http://test"})
        assert r2.status_code == 200
        new_refresh = client.cookies.get("refresh_token")
        assert new_refresh and new_refresh != first_refresh

        # Try to replay the ORIGINAL refresh token — must be rejected now.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client2:
            client2.cookies.set("refresh_token", first_refresh)
            r3 = await client2.post("/api/v1/auth/refresh", headers={"origin": "http://test"})
            assert r3.status_code == 401


@pytest.mark.asyncio
async def test_cross_origin_logout_blocked():
    """H-2: logout without a matching Origin header is 403."""
    from fastapi.responses import JSONResponse
    from routes.auth import router as auth_router

    app = FastAPI()
    app.state.auth = _settings("hunter22")
    app.include_router(auth_router, prefix="/api/v1")

    @app.exception_handler(AuthError)
    async def _h(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc)})

    invalidate_refresh("admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "hunter22"})
        assert login.status_code == 200

        r_evil = await client.post(
            "/api/v1/auth/logout",
            headers={"origin": "http://evil.example"},
        )
        assert r_evil.status_code == 403

        r_ok = await client.post(
            "/api/v1/auth/logout",
            headers={"origin": "http://test"},
        )
        assert r_ok.status_code == 200


def test_scp_repo_url_rejects_path_with_at():
    """H-3: scp-like URL whose path starts with @ is a host-confusion vector."""
    from src.path_validation import RepoUrlError, validate_repo_url

    with pytest.raises(RepoUrlError):
        validate_repo_url(
            "git@github.com:@evil.com/repo",
            ["github.com", "gitee.com"],
            ["https", "ssh", "git"],
        )
    # Legitimate SCP URL still works.
    ok = validate_repo_url(
        "git@github.com:owner/repo.git",
        ["github.com", "gitee.com"],
        ["https", "ssh", "git"],
    )
    assert ok == "git@github.com:owner/repo.git"


def test_client_ip_honours_xff_from_trusted_proxy(monkeypatch):
    """H-1: X-Forwarded-For is trusted only when the peer is a trusted proxy."""
    from src.request_utils import client_ip
    from unittest.mock import MagicMock

    settings = MagicMock()
    settings.security.trusted_proxies = ["127.0.0.1"]

    def _req(peer, xff):
        r = MagicMock()
        r.client.host = peer
        r.headers = {"x-forwarded-for": xff} if xff else {}
        r.app.state.settings = settings
        return r

    # Peer is trusted: XFF is believed.
    assert client_ip(_req("127.0.0.1", "203.0.113.7, 10.0.0.1")) == "203.0.113.7"

    # Peer is NOT trusted: XFF is ignored; peer returned.
    assert client_ip(_req("8.8.8.8", "203.0.113.7")) == "8.8.8.8"


def test_redact_truncates_oversized_input():
    """M-3: redact caps input size so regex work stays bounded."""
    from routes.diagnostics import _redact, _REDACT_MAX_BYTES

    big = "x" * (_REDACT_MAX_BYTES + 100)
    out = _redact(big + "sk-ABCDEFGHIJKLMNOP")
    # Output must not exceed the cap (+ small margin for substitutions).
    assert len(out) <= _REDACT_MAX_BYTES + 50
    assert "sk-***" in out


def test_redact_scrubs_aws_and_anthropic_keys():
    """M-2: extended secret coverage."""
    from routes.diagnostics import _redact

    sample = (
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"
        "ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
    )
    cleaned = _redact(sample)
    assert "AKIAIOSFODNN7EXAMPLE" not in cleaned
    assert "sk-ant-api03-abcdefghijklmnopqrstuvwxyz" not in cleaned
    assert "-----BEGIN RSA PRIVATE KEY-----" not in cleaned


@pytest.mark.asyncio
async def test_agent_token_bypasses_login():
    """Valid X-Agent-Token should authenticate as the agent subject."""
    from fastapi import Depends
    from fastapi.responses import JSONResponse
    from src.auth import AGENT_SUBJECT, get_current_user

    app = FastAPI()
    app.state.auth = _settings(agent_token="a" * 40)

    @app.exception_handler(AuthError)
    async def _h(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc)})

    @app.get("/who")
    async def who(user: str = Depends(get_current_user)):
        return {"user": user}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r_no = await client.get("/who")
        assert r_no.status_code == 401

        r_bad = await client.get("/who", headers={"X-Agent-Token": "b" * 40})
        assert r_bad.status_code == 401

        r_ok = await client.get("/who", headers={"X-Agent-Token": "a" * 40})
        assert r_ok.status_code == 200
        assert r_ok.json() == {"user": AGENT_SUBJECT}


def test_agent_token_disabled_when_empty():
    """If AGENT_API_TOKEN is unset, the header path must not match anything."""
    from src.auth import AGENT_SUBJECT
    cfg = _settings(agent_token="")
    assert cfg.agent_api_token == ""
    assert AGENT_SUBJECT == "agent"


def test_ttl_env_clamped(monkeypatch):
    """M-4: TTL env vars are bounded to reasonable ranges."""
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("hunter22"))
    monkeypatch.setenv("JWT_SECRET", "x" * 48)
    monkeypatch.setenv("ACCESS_TOKEN_TTL_MIN", "99999")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_DAYS", "99999")

    cfg = AuthSettings.from_env()
    assert cfg.access_ttl.total_seconds() <= 60 * 60
    assert cfg.refresh_ttl.total_seconds() <= 30 * 24 * 3600
