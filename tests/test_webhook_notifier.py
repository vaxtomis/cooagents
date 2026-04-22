"""Phase 5 WebhookNotifier contract tests.

Covers:
* KNOWN_EVENTS guard on deliver()
* Generic path envelope shape + X-Cooagents-Signature HMAC header
* OpenClaw path legacy 7-field body + Bearer header + simplified message
* events_json filtering
* retry loop + failure row in workspace_events
* fire-and-forget (deliver does not block)
* bootstrap_builtin_subscriptions upsert idempotency
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import (
    HermesConfig,
    HermesWebhookConfig,
    OpenclawConfig,
    OpenclawHooksConfig,
    Settings,
)
from src.database import Database
from src.webhook_notifier import WebhookNotifier


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
async def wn(db):
    n = WebhookNotifier(db)
    yield n
    await n.close()


def _fake_response(status_code=200, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def _mock_httpx_client(return_value=None, side_effect=None):
    client = AsyncMock()
    if side_effect is not None:
        client.post = AsyncMock(side_effect=side_effect)
    else:
        client.post = AsyncMock(return_value=return_value or _fake_response())
    return client


# ---------------------------------------------------------------------------
# Subscription management
# ---------------------------------------------------------------------------

async def test_register_subscription(wn):
    sid = await wn.register("http://example.com/hook")
    assert sid is not None
    subs = await wn.list_all()
    assert len(subs) == 1
    assert subs[0]["url"] == "http://example.com/hook"
    assert subs[0]["slug"] is None


async def test_register_with_events(wn):
    await wn.register(
        "http://example.com/hook",
        events=["dev_work.completed", "workspace.created"],
    )
    subs = await wn.list_all()
    stored = json.loads(subs[0]["events_json"])
    assert "dev_work.completed" in stored


async def test_remove_subscription(wn):
    sid = await wn.register("http://example.com/hook")
    await wn.remove(sid)
    assert await wn.list_all() == []


# ---------------------------------------------------------------------------
# KNOWN_EVENTS guard
# ---------------------------------------------------------------------------

async def test_deliver_rejects_unknown_event(wn):
    with pytest.raises(AssertionError):
        await wn.deliver("foo.bar")


# ---------------------------------------------------------------------------
# Generic path
# ---------------------------------------------------------------------------

async def test_generic_path_envelope_shape(wn):
    await wn.register("http://example.com/hook")
    captured = {}

    async def fake_post(url, content, headers):
        captured["url"] = url
        captured["body"] = json.loads(content)
        captured["headers"] = headers
        return _fake_response(200)

    client = AsyncMock()
    client.post = fake_post
    wn._client = client

    await wn.deliver(
        "dev_work.completed",
        workspace_id="ws-1",
        correlation_id="dev-1",
        payload={"score": 90},
    )
    # Let the fire-and-forget task run.
    await asyncio.gather(*wn._inflight, return_exceptions=True)

    assert captured["url"] == "http://example.com/hook"
    body = captured["body"]
    assert set(body.keys()) == {
        "event", "event_id", "ts", "correlation_id", "payload",
    }
    assert body["event"] == "dev_work.completed"
    assert body["correlation_id"] == "dev-1"
    assert body["payload"] == {"score": 90}
    assert "X-Cooagents-Signature" not in captured["headers"]


async def test_generic_path_signature_header(wn):
    secret = "super-secret"
    await wn.register("http://example.com/hook", secret=secret)
    captured = {}

    async def fake_post(url, content, headers):
        captured["body_raw"] = content
        captured["headers"] = headers
        return _fake_response(200)

    client = AsyncMock()
    client.post = fake_post
    wn._client = client

    await wn.deliver(
        "workspace.created",
        workspace_id="ws-1",
        correlation_id="ws-1",
        payload={"workspace_id": "ws-1", "title": "t"},
    )
    await asyncio.gather(*wn._inflight, return_exceptions=True)

    header = captured["headers"]["X-Cooagents-Signature"]
    assert header.startswith("sha256=")
    expected = hmac.new(
        secret.encode(), captured["body_raw"].encode(), hashlib.sha256
    ).hexdigest()
    assert header == f"sha256={expected}"


async def test_generic_filters_by_events_json(wn):
    await wn.register(
        "http://example.com/hook",
        events=["dev_work.completed"],
    )
    posts: list = []

    async def fake_post(url, content, headers):
        posts.append(content)
        return _fake_response(200)

    client = AsyncMock()
    client.post = fake_post
    wn._client = client

    await wn.deliver(
        "workspace.created",
        workspace_id="ws-1",
        correlation_id="ws-1",
        payload={"workspace_id": "ws-1", "title": "t"},
    )
    await asyncio.gather(*wn._inflight, return_exceptions=True)
    assert posts == []


# ---------------------------------------------------------------------------
# OpenClaw path
# ---------------------------------------------------------------------------

def _openclaw_settings(token="tok-abc"):
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


async def test_openclaw_path_bearer_and_legacy_envelope(db):
    settings = _openclaw_settings(token="tok-abc")
    wn = WebhookNotifier(db, settings=settings)
    try:
        await wn.bootstrap_builtin_subscriptions(settings)

        captured = {}

        async def fake_post(url, content, headers):
            captured["url"] = url
            captured["body"] = json.loads(content)
            captured["headers"] = headers
            return _fake_response(200)

        client = AsyncMock()
        client.post = fake_post
        wn._client = client

        await wn.deliver(
            "dev_work.completed",
            workspace_id="ws-1",
            correlation_id="dev-1",
            payload={"score": 88},
        )
        await asyncio.gather(*wn._inflight, return_exceptions=True)

        assert captured["url"] == "http://localhost:18789/hooks/agent"
        assert captured["headers"]["Authorization"] == "Bearer tok-abc"
        body = captured["body"]
        assert set(body.keys()) == {
            "message", "name", "deliver", "channel", "to",
            "wakeMode", "idempotencyKey",
        }
        assert body["channel"] == "feishu"
        assert body["to"] == "ou_default"
        assert body["deliver"] is True
        assert body["wakeMode"] == "now"
        assert body["idempotencyKey"].startswith("cooagents:")
    finally:
        await wn.close()


async def test_openclaw_path_message_is_simplified(db):
    settings = _openclaw_settings()
    wn = WebhookNotifier(db, settings=settings)
    try:
        await wn.bootstrap_builtin_subscriptions(settings)

        captured = {}

        async def fake_post(url, content, headers):
            captured["body"] = json.loads(content)
            return _fake_response(200)

        client = AsyncMock()
        client.post = fake_post
        wn._client = client

        await wn.deliver(
            "dev_work.score_passed",
            workspace_id="ws-x",
            correlation_id="dev-x",
            payload={"score": 95},
        )
        await asyncio.gather(*wn._inflight, return_exceptions=True)

        msg = captured["body"]["message"]
        assert msg.startswith("[cooagents:dev_work.score_passed]")
        assert "ws=dev-x" in msg  # correlation_id is the identifier now
        assert "payload:" in msg
        # Dead-code markers must be gone
        for dead in ("feishu_doc", "Action plan", "请回复", "Task: cooagents"):
            assert dead not in msg, f"dead code marker still present: {dead}"
    finally:
        await wn.close()


async def test_openclaw_payload_truncated_when_large(db):
    settings = _openclaw_settings()
    wn = WebhookNotifier(db, settings=settings)
    try:
        await wn.bootstrap_builtin_subscriptions(settings)

        captured = {}

        async def fake_post(url, content, headers):
            captured["body"] = json.loads(content)
            return _fake_response(200)

        client = AsyncMock()
        client.post = fake_post
        wn._client = client

        big_payload = {"blob": "x" * 5000}
        await wn.deliver(
            "dev_work.completed",
            workspace_id="ws-1",
            correlation_id="dev-1",
            payload=big_payload,
        )
        await asyncio.gather(*wn._inflight, return_exceptions=True)

        msg = captured["body"]["message"]
        # Ellipsis appended when truncated.
        assert msg.endswith("…")
    finally:
        await wn.close()


# ---------------------------------------------------------------------------
# Retry + failure record
# ---------------------------------------------------------------------------

async def test_retry_three_times_then_records_failure(wn, db):
    await wn.register("http://example.com/hook")

    async def always_fail(url, content, headers):
        return _fake_response(500, text="bad")

    client = AsyncMock()
    client.post = always_fail
    wn._client = client

    with patch("src.webhook_notifier.asyncio.sleep", new=AsyncMock()) as sleep_mock:
        await wn.deliver(
            "dev_work.completed",
            workspace_id="ws-1",
            correlation_id="dev-1",
            payload={"score": 0},
        )
        await asyncio.gather(*wn._inflight, return_exceptions=True)

    # 2 sleeps (delays 5s and 30s; first attempt has delay 0)
    assert sleep_mock.await_count == 2
    row = await db.fetchone(
        "SELECT * FROM workspace_events "
        "WHERE event_name='webhook.delivery_failed'"
    )
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["event"] == "dev_work.completed"
    assert payload["status_code"] == 500


async def test_get_deliveries_route_filters_by_subscription_id(wn, db):
    """The /deliveries route uses a SQL LIKE on payload_json — guard against
    a json.dumps separator drift that would silently break the LIKE pattern.
    """
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from routes.webhooks import router

    sid_a = await wn.register("http://a.example/hook")
    sid_b = await wn.register("http://b.example/hook")

    async def always_fail(url, content, headers):
        return _fake_response(500, text="bad")

    client = AsyncMock()
    client.post = always_fail
    wn._client = client

    with patch("src.webhook_notifier.asyncio.sleep", new=AsyncMock()):
        await wn.deliver(
            "dev_work.completed",
            workspace_id="ws-1",
            correlation_id="dev-1",
            payload={"score": 0},
        )
        await asyncio.gather(*wn._inflight, return_exceptions=True)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.db = db
    app.state.webhooks = wn
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp_a = await c.get(f"/api/v1/webhooks/{sid_a}/deliveries")
        resp_b = await c.get(f"/api/v1/webhooks/{sid_b}/deliveries")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    rows_a = resp_a.json()
    rows_b = resp_b.json()
    assert len(rows_a) == 1
    assert rows_a[0]["payload"]["subscription_id"] == sid_a
    assert len(rows_b) == 1
    assert rows_b[0]["payload"]["subscription_id"] == sid_b


async def test_retry_on_exception_then_succeed(wn, db):
    await wn.register("http://example.com/hook")
    attempts = {"n": 0}

    async def flaky(url, content, headers):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient")
        return _fake_response(200)

    client = AsyncMock()
    client.post = flaky
    wn._client = client

    with patch("src.webhook_notifier.asyncio.sleep", new=AsyncMock()):
        await wn.deliver(
            "dev_work.completed",
            workspace_id="ws-1",
            correlation_id="dev-1",
            payload={"score": 80},
        )
        await asyncio.gather(*wn._inflight, return_exceptions=True)

    assert attempts["n"] == 3
    rows = await db.fetchall(
        "SELECT * FROM workspace_events "
        "WHERE event_name='webhook.delivery_failed'"
    )
    assert rows == []


# ---------------------------------------------------------------------------
# Fire-and-forget
# ---------------------------------------------------------------------------

async def test_deliver_is_fire_and_forget(wn):
    await wn.register("http://example.com/hook")

    async def slow_post(url, content, headers):
        await asyncio.sleep(5)
        return _fake_response(200)

    client = AsyncMock()
    client.post = slow_post
    wn._client = client

    # Should return well within 0.1s even though the HTTP call takes 5s.
    await asyncio.wait_for(
        wn.deliver(
            "dev_work.completed",
            workspace_id="ws-1",
            correlation_id="dev-1",
            payload={},
        ),
        timeout=0.1,
    )
    # Cancel the inflight task so the test exits cleanly.
    for task in list(wn._inflight):
        task.cancel()
    await asyncio.gather(*wn._inflight, return_exceptions=True)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

async def test_bootstrap_upserts_openclaw(db):
    settings = _openclaw_settings(token="tok-1")
    wn = WebhookNotifier(db, settings=settings)
    try:
        await wn.bootstrap_builtin_subscriptions(settings)
        subs = await wn.list_all()
        openclaw = [s for s in subs if s["slug"] == "openclaw"]
        assert len(openclaw) == 1
        assert openclaw[0]["secret"] == "tok-1"

        # Second bootstrap must not create a duplicate row.
        await wn.bootstrap_builtin_subscriptions(settings)
        subs2 = await wn.list_all()
        assert len([s for s in subs2 if s["slug"] == "openclaw"]) == 1
    finally:
        await wn.close()


async def test_bootstrap_registers_hermes(db):
    settings = Settings(
        hermes=HermesConfig(
            enabled=True,
            webhook=HermesWebhookConfig(
                enabled=True,
                url="http://hermes.local/webhook",
                secret="hermes-secret",
                events=["dev_work.completed"],
            ),
        )
    )
    wn = WebhookNotifier(db, settings=settings)
    try:
        await wn.bootstrap_builtin_subscriptions(settings)
        subs = await wn.list_all()
        hermes = [s for s in subs if s["slug"] == "hermes"]
        assert len(hermes) == 1
        assert hermes[0]["secret"] == "hermes-secret"
        assert json.loads(hermes[0]["events_json"]) == ["dev_work.completed"]
    finally:
        await wn.close()


async def test_shared_event_id_across_generic_subscriptions(wn):
    await wn.register("http://a.example/hook")
    await wn.register("http://b.example/hook")
    captured: list[dict] = []

    async def fake_post(url, content, headers):
        captured.append(json.loads(content))
        return _fake_response(200)

    client = AsyncMock()
    client.post = fake_post
    wn._client = client

    eid = await wn.deliver(
        "dev_work.completed",
        workspace_id="ws-1",
        correlation_id="dev-1",
        payload={"score": 90},
    )
    await asyncio.gather(*wn._inflight, return_exceptions=True)
    assert len(captured) == 2
    assert captured[0]["event_id"] == captured[1]["event_id"] == eid
