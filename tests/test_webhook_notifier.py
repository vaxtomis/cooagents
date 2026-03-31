import pytest
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
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

async def test_register_webhook(wn):
    wid = await wn.register("http://example.com/hook")
    assert wid is not None
    hooks = await wn.list_all()
    assert len(hooks) == 1
    assert hooks[0]["url"] == "http://example.com/hook"

async def test_register_with_events_filter(wn):
    wid = await wn.register("http://example.com/hook", events=["gate.waiting", "run.completed"])
    hooks = await wn.list_all()
    assert hooks[0]["events_json"] is not None
    events = json.loads(hooks[0]["events_json"])
    assert "gate.waiting" in events

async def test_notify_sends_to_active(wn, db):
    await wn.register("http://example.com/hook1")
    await wn.register("http://example.com/hook2")

    wn._deliver = AsyncMock(return_value=True)
    await wn.notify("stage.changed", {"run_id": "r1", "from": "INIT", "to": "REQ_COLLECTING"})
    assert wn._deliver.call_count == 2

async def test_notify_filters_by_event(wn):
    await wn.register("http://example.com/hook", events=["gate.waiting"])
    wn._deliver = AsyncMock(return_value=True)
    await wn.notify("stage.changed", {"run_id": "r1"})
    assert wn._deliver.call_count == 0

async def test_notify_skips_disabled(wn, db):
    wid = await wn.register("http://example.com/hook")
    await db.execute("UPDATE webhooks SET status='disabled' WHERE id=?", (wid,))
    wn._deliver = AsyncMock(return_value=True)
    await wn.notify("stage.changed", {"run_id": "r1"})
    assert wn._deliver.call_count == 0

async def test_remove_webhook(wn):
    wid = await wn.register("http://example.com/hook")
    await wn.remove(wid)
    hooks = await wn.list_all()
    assert len(hooks) == 0

async def test_delivery_failure_without_run_id_is_recorded_as_null(wn, db):
    wid = await wn.register("http://example.com/hook")
    wn._deliver = AsyncMock(return_value=False)

    with patch("src.webhook_notifier.asyncio.sleep", new=AsyncMock()) as sleep_mock:
        await wn.notify("gate.waiting", {})

    assert sleep_mock.await_count == 2
    row = await db.fetchone(
        "SELECT run_id, event_type, payload_json FROM events WHERE event_type='webhook.delivery_failed'"
    )
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert row["run_id"] is None
    assert row["event_type"] == "webhook.delivery_failed"
    assert payload == {"webhook_id": wid, "event_type": "gate.waiting"}

async def test_openclaw_failure_without_run_id_is_recorded_as_null(db):
    cfg = SimpleNamespace(
        enabled=True,
        default_channel="ops",
        default_to="duty",
        token="secret",
        url="http://example.com/hooks/agent",
    )
    notifier = WebhookNotifier(db, openclaw_hooks=cfg)
    notifier._deliver_to_openclaw_once = AsyncMock(return_value=(False, {"status_code": 502}))

    try:
        with patch("src.webhook_notifier.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await notifier.notify("run.completed", {})

        assert sleep_mock.await_count == 2
        assert notifier._deliver_to_openclaw_once.await_count == 3
        row = await db.fetchone(
            "SELECT run_id, event_type, payload_json FROM events WHERE event_type='openclaw.hooks.delivery_failed'"
        )
        assert row is not None
        payload = json.loads(row["payload_json"])
        assert row["run_id"] is None
        assert row["event_type"] == "openclaw.hooks.delivery_failed"
        assert payload == {"event_type": "run.completed", "status_code": 502}
    finally:
        await notifier.close()
