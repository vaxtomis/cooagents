"""Route-level tests for /api/v1/workspaces/{id}/events (Phase 5.5)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from routes.workspace_events import router as events_router
from src.database import Database
from src.exceptions import BadRequestError, NotFoundError


def _ts(seconds: int) -> str:
    return f"2026-04-23T00:00:{seconds:02d}+00:00"


@pytest.fixture
async def client(tmp_path):
    test_app = FastAPI(title="cooagents-test-workspace-events")

    db = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await db.connect()
    test_app.state.db = db
    test_app.state.start_time = time.time()

    @test_app.exception_handler(NotFoundError)
    async def _nf(request, exc):
        return JSONResponse(status_code=404, content={"message": str(exc)})

    @test_app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(status_code=400, content={"message": str(exc)})

    test_app.include_router(events_router, prefix="/api/v1")

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        ac.db = db  # type: ignore[attr-defined]
        yield ac

    await db.close()


async def _seed_workspace(db: Database, ws_id: str = "ws-aaa"):
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (ws_id, "W", ws_id, "active", str(Path.cwd()), _ts(0), _ts(0)),
    )


async def _seed_event(
    db: Database,
    *,
    event_id: str,
    name: str,
    ts: str,
    workspace_id: str = "ws-aaa",
    payload: dict | None = None,
):
    await db.execute(
        "INSERT INTO workspace_events(event_id,event_name,workspace_id,"
        "correlation_id,payload_json,ts) VALUES(?,?,?,?,?,?)",
        (event_id, name, workspace_id, None,
         json.dumps(payload) if payload is not None else None, ts),
    )


async def _seed_5(db: Database):
    await _seed_workspace(db)
    await _seed_event(db, event_id="e1", name="design_doc.published",
                      ts=_ts(1), payload={"a": 1})
    await _seed_event(db, event_id="e2", name="dev_work.completed",
                      ts=_ts(2))
    await _seed_event(db, event_id="e3", name="design_doc.published",
                      ts=_ts(3), payload={"a": 3})
    await _seed_event(db, event_id="e4", name="design_work.completed",
                      ts=_ts(4))
    await _seed_event(db, event_id="e5", name="dev_work.completed",
                      ts=_ts(5))


async def test_unknown_workspace_404(client):
    r = await client.get("/api/v1/workspaces/ws-missing/events")
    assert r.status_code == 404


async def test_default_returns_all_desc(client):
    await _seed_5(client.db)
    r = await client.get("/api/v1/workspaces/ws-aaa/events")
    assert r.status_code == 200
    body = r.json()
    assert len(body["events"]) == 5
    # Newest first
    assert body["events"][0]["event_id"] == "e5"
    assert body["events"][-1]["event_id"] == "e1"
    assert body["pagination"]["has_more"] is False


async def test_pagination(client):
    await _seed_5(client.db)
    r = await client.get(
        "/api/v1/workspaces/ws-aaa/events", params={"limit": 2, "offset": 0}
    )
    body = r.json()
    assert len(body["events"]) == 2
    assert body["pagination"]["has_more"] is True

    r2 = await client.get(
        "/api/v1/workspaces/ws-aaa/events", params={"limit": 2, "offset": 4}
    )
    body2 = r2.json()
    assert len(body2["events"]) == 1
    assert body2["pagination"]["has_more"] is False


async def test_event_name_single_filter(client):
    await _seed_5(client.db)
    r = await client.get(
        "/api/v1/workspaces/ws-aaa/events",
        params={"event_name": "design_doc.published"},
    )
    body = r.json()
    assert {e["event_id"] for e in body["events"]} == {"e1", "e3"}


async def test_event_name_multi_filter(client):
    await _seed_5(client.db)
    r = await client.get(
        "/api/v1/workspaces/ws-aaa/events",
        params=[("event_name", "design_doc.published"),
                ("event_name", "dev_work.completed")],
    )
    body = r.json()
    assert {e["event_id"] for e in body["events"]} == {"e1", "e3", "e2", "e5"}


async def test_event_name_dedup(client):
    await _seed_5(client.db)
    r = await client.get(
        "/api/v1/workspaces/ws-aaa/events",
        params=[("event_name", "design_doc.published"),
                ("event_name", "design_doc.published")],
    )
    body = r.json()
    assert {e["event_id"] for e in body["events"]} == {"e1", "e3"}


async def test_event_name_list_length_cap_422(client):
    await _seed_workspace(client.db)
    params = [("event_name", f"name-{i}") for i in range(21)]
    r = await client.get(
        "/api/v1/workspaces/ws-aaa/events", params=params
    )
    assert r.status_code == 422


async def test_event_name_per_value_length_cap_400(client):
    await _seed_workspace(client.db)
    big = "a" * 121
    r = await client.get(
        "/api/v1/workspaces/ws-aaa/events",
        params={"event_name": big},
    )
    assert r.status_code == 400


async def test_unknown_event_name_returns_empty(client):
    await _seed_5(client.db)
    r = await client.get(
        "/api/v1/workspaces/ws-aaa/events",
        params={"event_name": "nonexistent.event"},
    )
    body = r.json()
    assert body["events"] == []


async def test_payload_decoded(client):
    await _seed_5(client.db)
    r = await client.get("/api/v1/workspaces/ws-aaa/events")
    body = r.json()
    e1 = next(e for e in body["events"] if e["event_id"] == "e1")
    assert e1["payload"] == {"a": 1}
    assert "payload_json" not in e1
    e2 = next(e for e in body["events"] if e["event_id"] == "e2")
    assert e2["payload"] is None


async def test_tie_break_by_id_desc(client):
    await _seed_workspace(client.db)
    same_ts = _ts(7)
    await _seed_event(client.db, event_id="t1", name="a", ts=same_ts)
    await _seed_event(client.db, event_id="t2", name="a", ts=same_ts)
    r = await client.get("/api/v1/workspaces/ws-aaa/events")
    body = r.json()
    # Both have same ts; newer (higher id) first
    assert [e["event_id"] for e in body["events"][:2]] == ["t2", "t1"]


async def test_limit_max_200(client):
    await _seed_workspace(client.db)
    r = await client.get(
        "/api/v1/workspaces/ws-aaa/events", params={"limit": 200}
    )
    assert r.status_code == 200
    r2 = await client.get(
        "/api/v1/workspaces/ws-aaa/events", params={"limit": 201}
    )
    assert r2.status_code == 422
