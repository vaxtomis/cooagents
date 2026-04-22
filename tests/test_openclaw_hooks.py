"""Phase 5 OpenClaw outbound contract tests.

Scope per user decision: OpenClaw is outbound-only. The OpenClaw repo must
not need any code change. These tests freeze the invariants that make
that possible:

* slug='openclaw' builtin subscription auto-registered from config
* Authorization: Bearer <token> header
* Body keeps the legacy 7-field envelope (message/name/deliver/channel/to/
  wakeMode/idempotencyKey) — OpenClaw side parses these verbatim
* message text is simplified — zero feishu_doc / action-plan residue
* artifact_manager param / _GATE_* constants are gone
* builtin openclaw subscription cannot be deleted via the API
* event contract JSON freezes the generic envelope schema (21 events)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.config import (
    HermesConfig,
    HermesWebhookConfig,
    OpenclawConfig,
    OpenclawHooksConfig,
    Settings,
)
from src.database import Database
from src.webhook_events import KNOWN_EVENTS
from src.webhook_notifier import WebhookNotifier


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


def _settings(token="test-token"):
    return Settings(
        openclaw=OpenclawConfig(
            hooks=OpenclawHooksConfig(
                enabled=True,
                url="http://localhost:18789/hooks/agent",
                token=token,
                default_channel="feishu",
                default_to="ou_default",
            )
        )
    )


@pytest.fixture
async def wn_openclaw(db):
    settings = _settings()
    n = WebhookNotifier(db, settings=settings)
    await n.bootstrap_builtin_subscriptions(settings)
    yield n, settings
    await n.close()


def _fake_response(status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    return resp


def _install_capture(wn):
    """Mock the HTTP client, return a dict that will capture POST args."""
    captured: dict = {}

    async def fake_post(url, content, headers):
        captured["url"] = url
        captured["body"] = json.loads(content)
        captured["headers"] = headers
        return _fake_response(200)

    client = AsyncMock()
    client.post = fake_post
    wn._client = client
    return captured


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

async def test_openclaw_subscription_bootstrapped(wn_openclaw):
    wn, _ = wn_openclaw
    subs = await wn.list_all()
    openclaw = [s for s in subs if s["slug"] == "openclaw"]
    assert len(openclaw) == 1
    assert openclaw[0]["secret"] == "test-token"
    assert openclaw[0]["url"] == "http://localhost:18789/hooks/agent"
    assert openclaw[0]["active"] == 1


async def test_openclaw_disabled_means_no_row(db):
    settings = Settings(
        openclaw=OpenclawConfig(hooks=OpenclawHooksConfig(enabled=False))
    )
    wn = WebhookNotifier(db, settings=settings)
    try:
        await wn.bootstrap_builtin_subscriptions(settings)
        subs = await wn.list_all()
        assert [s for s in subs if s["slug"] == "openclaw"] == []
    finally:
        await wn.close()


# ---------------------------------------------------------------------------
# Request shape — Bearer + legacy envelope
# ---------------------------------------------------------------------------

async def test_openclaw_sends_bearer_header(wn_openclaw):
    wn, _ = wn_openclaw
    cap = _install_capture(wn)
    await wn.deliver(
        "dev_work.completed",
        workspace_id="ws-1",
        correlation_id="dev-1",
        payload={"score": 90},
    )
    import asyncio

    await asyncio.gather(*wn._inflight, return_exceptions=True)
    assert cap["headers"]["Authorization"] == "Bearer test-token"


async def test_openclaw_body_has_legacy_seven_fields(wn_openclaw):
    wn, _ = wn_openclaw
    cap = _install_capture(wn)
    await wn.deliver(
        "workspace.created",
        workspace_id="ws-1",
        correlation_id="ws-1",
        payload={"workspace_id": "ws-1", "title": "t", "slug": "t"},
    )
    import asyncio

    await asyncio.gather(*wn._inflight, return_exceptions=True)
    assert set(cap["body"].keys()) == {
        "message", "name", "deliver", "channel", "to",
        "wakeMode", "idempotencyKey",
    }
    assert cap["body"]["name"] == "cooagents"
    assert cap["body"]["channel"] == "feishu"
    assert cap["body"]["to"] == "ou_default"
    assert cap["body"]["deliver"] is True


async def test_openclaw_message_has_no_dead_code_markers(wn_openclaw):
    wn, _ = wn_openclaw
    cap = _install_capture(wn)
    await wn.deliver(
        "dev_work.escalated",
        workspace_id="ws-1",
        correlation_id="dev-1",
        payload={"reason": "r", "rounds": 5},
    )
    import asyncio

    await asyncio.gather(*wn._inflight, return_exceptions=True)
    msg = cap["body"]["message"]
    for marker in (
        "feishu_doc",
        "Action plan",
        "请回复",
        "Task: cooagents workflow",
        "设计审批",
        "DES-",
        "REQ-",
        "TEST-REPORT-",
    ):
        assert marker not in msg, f"dead code marker still present: {marker}"


async def test_openclaw_message_carries_event_summary(wn_openclaw):
    wn, _ = wn_openclaw
    cap = _install_capture(wn)
    await wn.deliver(
        "dev_work.round_completed",
        workspace_id="ws-x",
        correlation_id="dev-x",
        payload={"round": 2, "score": 72},
    )
    import asyncio

    await asyncio.gather(*wn._inflight, return_exceptions=True)
    msg = cap["body"]["message"]
    assert msg.startswith("[cooagents:dev_work.round_completed]")
    assert "ws=dev-x" in msg
    assert "payload:" in msg


async def test_openclaw_idempotency_key_uses_event_id(wn_openclaw):
    wn, _ = wn_openclaw
    cap = _install_capture(wn)
    eid = await wn.deliver(
        "dev_work.completed",
        workspace_id="ws-1",
        correlation_id="dev-1",
        payload={"score": 90},
    )
    import asyncio

    await asyncio.gather(*wn._inflight, return_exceptions=True)
    assert cap["body"]["idempotencyKey"] == f"cooagents:{eid}"


# ---------------------------------------------------------------------------
# Dead code removal
# ---------------------------------------------------------------------------

def test_openclaw_notifier_has_no_artifact_manager_kwarg(db):
    with pytest.raises(TypeError):
        WebhookNotifier(db, artifact_manager=object())


def test_old_symbols_removed():
    import src.webhook_notifier as wn_mod

    for name in (
        "OPENCLAW_EVENTS",
        "_GATE_ARTIFACT_KIND",
        "_GATE_DOC_PREFIX",
        "_GATE_LABEL",
        "_GATE_DOC_TYPE",
        "_GATE_NEXT",
        "_MAX_ARTIFACT_BYTES",
        "_build_gate_waiting_message",
        "_fetch_gate_artifact",
    ):
        assert not hasattr(wn_mod, name), (
            f"{name} should be removed from webhook_notifier"
        )


# ---------------------------------------------------------------------------
# Contract JSON
# ---------------------------------------------------------------------------

def _contract_path() -> Path:
    return Path(__file__).with_name("openclaw_event_contract.json")


def test_event_contract_covers_all_known_events():
    contract = json.loads(_contract_path().read_text(encoding="utf-8"))
    assert set(contract["events"].keys()) == KNOWN_EVENTS


def test_event_contract_envelope_keys_frozen():
    contract = json.loads(_contract_path().read_text(encoding="utf-8"))
    assert contract["envelope_keys"] == [
        "event", "event_id", "ts", "correlation_id", "payload",
    ]


async def test_generic_path_envelope_matches_contract(db):
    contract = json.loads(_contract_path().read_text(encoding="utf-8"))
    wn = WebhookNotifier(db)
    try:
        await wn.register("http://example.com/hook")
        captured: list[dict] = []

        async def fake_post(url, content, headers):
            captured.append(json.loads(content))
            return _fake_response(200)

        client = AsyncMock()
        client.post = fake_post
        wn._client = client

        # Fire one representative event and verify its envelope matches.
        await wn.deliver(
            "workspace.created",
            workspace_id="ws-1",
            correlation_id="ws-1",
            payload={"workspace_id": "ws-1", "title": "t", "slug": "t"},
        )
        import asyncio

        await asyncio.gather(*wn._inflight, return_exceptions=True)
        assert captured
        assert list(captured[0].keys()) == contract["envelope_keys"]
    finally:
        await wn.close()


# ---------------------------------------------------------------------------
# Builtin cannot be deleted via HTTP
# ---------------------------------------------------------------------------

async def test_builtin_openclaw_subscription_cannot_be_deleted_via_api(
    wn_openclaw, db
):
    """HTTP DELETE path refuses to remove slug='openclaw'."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.exceptions import BadRequestError
    from routes.webhooks import router

    wn, _ = wn_openclaw

    app = FastAPI()

    @app.exception_handler(BadRequestError)
    async def bad(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=400, content={"error": str(exc)})

    app.include_router(router, prefix="/api/v1")
    app.state.db = db
    app.state.webhooks = wn

    subs = await wn.list_all()
    openclaw_id = next(s["id"] for s in subs if s["slug"] == "openclaw")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/webhooks/{openclaw_id}")
    assert resp.status_code == 400
    assert "builtin" in resp.json()["error"].lower()
