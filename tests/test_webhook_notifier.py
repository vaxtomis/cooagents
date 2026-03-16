import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
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

    # Mock the _deliver method
    wn._deliver = AsyncMock(return_value=True)
    await wn.notify("stage.changed", {"run_id": "r1", "from": "INIT", "to": "REQ_COLLECTING"})
    assert wn._deliver.call_count == 2

async def test_notify_filters_by_event(wn):
    await wn.register("http://example.com/hook", events=["gate.waiting"])
    wn._deliver = AsyncMock(return_value=True)
    await wn.notify("stage.changed", {"run_id": "r1"})
    assert wn._deliver.call_count == 0  # Not matching event

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
