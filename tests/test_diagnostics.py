import json
import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from src.database import Database


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
async def client(db):
    from routes.diagnostics import create_diagnostics_router
    app = FastAPI()
    router = create_diagnostics_router(db)
    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


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
    resp = await client.get("/runs/run-1/trace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-1"
    assert len(data["events"]) == 1
    assert "summary" in data


async def test_run_trace_404(client):
    resp = await client.get("/runs/nonexistent/trace")
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
    resp = await client.get("/jobs/job-1/diagnosis")
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
    resp = await client.get("/traces/trace-abc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["trace_id"] == "trace-abc"
    assert "run-1" in data["affected_runs"]
