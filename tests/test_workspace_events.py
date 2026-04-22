"""Phase 3: workspace_events helper tests."""
import json

import pytest

from src.database import Database
from src.workspace_events import emit_workspace_event


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await d.connect()
    # Seed a workspace so FK-enabled inserts into workspace_events pass
    await d.execute(
        "INSERT INTO workspaces(id, title, slug, status, root_path, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
        (
            "ws-x", "Test", "test", "active", "/tmp/ws/test",
            "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
        ),
    )
    await d.execute(
        "INSERT INTO workspaces(id, title, slug, status, root_path, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
        (
            "ws-y", "Test Y", "test-y", "active", "/tmp/ws/test-y",
            "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
        ),
    )
    yield d
    await d.close()


async def test_emit_basic(db):
    eid = await emit_workspace_event(
        db, event_name="design_work.started",
        workspace_id="ws-x", correlation_id="desw-1",
        payload={"mode": "new", "title": "测试"},
    )
    row = await db.fetchone(
        "SELECT * FROM workspace_events WHERE event_id=?", (eid,)
    )
    assert row is not None
    assert row["event_name"] == "design_work.started"
    assert row["workspace_id"] == "ws-x"
    # payload roundtrips, chinese preserved (ensure_ascii=False)
    assert "测试" in row["payload_json"]
    data = json.loads(row["payload_json"])
    assert data["mode"] == "new"


async def test_emit_without_payload(db):
    eid = await emit_workspace_event(
        db, event_name="design_work.cancelled",
        workspace_id="ws-y", correlation_id="desw-2",
    )
    row = await db.fetchone(
        "SELECT * FROM workspace_events WHERE event_id=?", (eid,)
    )
    assert row["payload_json"] is None


async def test_event_ids_unique(db):
    ids = set()
    for _ in range(5):
        ids.add(
            await emit_workspace_event(
                db, event_name="x", workspace_id=None,
            )
        )
    assert len(ids) == 5
