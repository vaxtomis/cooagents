import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from routes.diagnostics import create_diagnostics_router
from routes.events import create_events_router
from src.database import Database


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
async def client(db):
    test_app = FastAPI()
    test_app.include_router(create_events_router(db), prefix="/api/v1")
    test_app.include_router(create_diagnostics_router(db), prefix="/api/v1")
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_event_rows(db):
    now = datetime.now(timezone.utc)
    for run_id, ticket in (("run-1", "TKT-1"), ("run-2", "TKT-2")):
        created_at = now.replace(microsecond=0).isoformat()
        await db.execute(
            "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (run_id, ticket, "/repo", "running", "INIT", created_at, created_at),
        )

    events = [
        ("run-1", "event.debug", '{"seq":0}', "2026-03-31T09:00:00+00:00", "trace-0", "request", "debug", "system"),
        ("run-1", "event.old", '{"seq":1}', "2026-03-31T10:00:00+00:00", "trace-1", "run", "info", "system"),
        ("run-1", "event.mid", '{"seq":2}', "2026-03-31T11:00:00+00:00", "trace-2", "job", "warning", "agent"),
        ("run-2", "event.new", '{"seq":3}', "2026-03-31T12:00:00+00:00", "trace-3", "step", "error", "agent"),
        ("run-2", "event.no_payload", None, "2026-03-31T13:00:00+00:00", "trace-4", "run", "info", "system"),
    ]
    for row in events:
        await db.execute(
            "INSERT INTO events(run_id,event_type,payload_json,created_at,trace_id,span_type,level,source) "
            "VALUES(?,?,?,?,?,?,?,?)",
            row,
        )


async def test_run_trace_returns_events(db, client):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run-1", "TKT-1", "/repo", "failed", "FAILED", now, now),
    )
    await db.execute(
        "INSERT INTO events(run_id,event_type,payload_json,created_at,trace_id,span_type,level,source) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("run-1", "stage.transition", '{"from":"INIT","to":"REQ_COLLECTING"}', now, "t1", "run", "info", "state_machine"),
    )
    resp = await client.get("/api/v1/runs/run-1/trace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-1"
    assert len(data["events"]) == 1
    assert "summary" in data


async def test_run_trace_404(client):
    resp = await client.get("/api/v1/runs/nonexistent/trace")
    assert resp.status_code == 404


async def test_job_diagnosis_returns_data(db, client):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run-1", "TKT-1", "/repo", "failed", "FAILED", now, now),
    )
    await db.execute(
        "INSERT INTO jobs(id,run_id,agent_type,stage,status,started_at) VALUES(?,?,?,?,?,?)",
        ("job-1", "run-1", "claude", "DEV_RUNNING", "failed", now),
    )
    resp = await client.get("/api/v1/jobs/job-1/diagnosis")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == "job-1"
    assert "diagnosis" in data


async def test_trace_lookup(db, client):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run-1", "TKT-1", "/repo", "running", "INIT", now, now),
    )
    await db.execute(
        "INSERT INTO events(run_id,event_type,payload_json,created_at,trace_id,span_type,level,source) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("run-1", "request.received", None, now, "trace-abc", "request", "info", "middleware"),
    )
    resp = await client.get("/api/v1/traces/trace-abc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["trace_id"] == "trace-abc"
    assert "run-1" in data["affected_runs"]


async def test_events_index_orders_newest_first(db, client):
    await _seed_event_rows(db)
    resp = await client.get("/api/v1/events")
    assert resp.status_code == 200
    data = resp.json()
    assert [event["created_at"] for event in data["events"]] == [
        "2026-03-31T13:00:00+00:00",
        "2026-03-31T12:00:00+00:00",
        "2026-03-31T11:00:00+00:00",
        "2026-03-31T10:00:00+00:00",
        "2026-03-31T09:00:00+00:00",
    ]
    assert data["events"][0]["ticket"] == "TKT-2"
    assert data["events"][0]["payload"] is None
    assert data["events"][1]["payload"] == {"seq": 3}
    assert "payload_json" not in data["events"][1]
    assert data["pagination"] == {"limit": 100, "offset": 0, "has_more": False}


async def test_events_index_filters_by_level(db, client):
    await _seed_event_rows(db)
    resp = await client.get("/api/v1/events", params={"level": "warning"})
    assert resp.status_code == 200
    data = resp.json()
    assert [event["level"] for event in data["events"]] == ["warning"]
    assert [event["event_type"] for event in data["events"]] == ["event.mid"]


async def test_events_index_filters_by_span_type(db, client):
    await _seed_event_rows(db)
    resp = await client.get("/api/v1/events", params={"span_type": "run"})
    assert resp.status_code == 200
    data = resp.json()
    assert [event["span_type"] for event in data["events"]] == ["run", "run"]


async def test_events_index_filters_by_run_id(db, client):
    await _seed_event_rows(db)
    resp = await client.get("/api/v1/events", params={"run_id": "run-1"})
    assert resp.status_code == 200
    data = resp.json()
    assert {event["run_id"] for event in data["events"]} == {"run-1"}
    assert [event["ticket"] for event in data["events"]] == ["TKT-1", "TKT-1", "TKT-1"]


async def test_events_index_paginates(db, client):
    await _seed_event_rows(db)
    resp = await client.get("/api/v1/events", params={"limit": 2, "offset": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert [event["event_type"] for event in data["events"]] == ["event.new", "event.mid"]
    assert data["pagination"] == {"limit": 2, "offset": 1, "has_more": True}


async def test_events_index_rejects_non_positive_limit(db, client):
    await _seed_event_rows(db)
    resp = await client.get("/api/v1/events", params={"limit": -1})
    assert resp.status_code == 422