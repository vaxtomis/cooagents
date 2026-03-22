import pytest
from datetime import datetime, timezone, timedelta
from src.database import Database
from src.run_brief import build_brief, resolve_run_by_ticket


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


async def test_brief_running_job(db):
    """Brief for a run with an active job shows current job details and previous step."""
    now = datetime.now(timezone.utc)
    t = now.isoformat()
    t_prev = (now - timedelta(minutes=5)).isoformat()

    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run-1", "PROJ-42", "/repo", "running", "DEV_RUNNING", t, t),
    )
    await db.execute(
        "INSERT INTO steps(run_id,from_stage,to_stage,triggered_by,created_at) VALUES(?,?,?,?,?)",
        ("run-1", "DEV_REVIEW", "DEV_QUEUED", "system", t_prev),
    )
    await db.execute(
        "INSERT INTO steps(run_id,from_stage,to_stage,triggered_by,created_at) VALUES(?,?,?,?,?)",
        ("run-1", "DEV_QUEUED", "DEV_DISPATCHED", "system", t),
    )
    await db.execute(
        "INSERT INTO steps(run_id,from_stage,to_stage,triggered_by,created_at) VALUES(?,?,?,?,?)",
        ("run-1", "DEV_DISPATCHED", "DEV_RUNNING", "system", t),
    )
    await db.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        ("host-2", "host-2.local", "codex", "active", t, t),
    )
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,turn_count,timeout_sec,started_at,running_started_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("job-1", "run-1", "host-2", "codex", "DEV_RUNNING", "running", 3, 3600, t_prev, t),
    )
    await db.execute(
        "INSERT INTO approvals(run_id,gate,decision,by,comment,created_at) VALUES(?,?,?,?,?,?)",
        ("run-1", "dev", "rejected", "reviewer", "测试覆盖率不足", t_prev),
    )

    brief = await build_brief(db, "run-1")

    assert brief["run_id"] == "run-1"
    assert brief["ticket"] == "PROJ-42"
    assert brief["status"] == "running"

    c = brief["current"]
    assert c["stage"] == "DEV_RUNNING"
    assert c["job_id"] == "job-1"
    assert c["job_status"] == "running"
    assert c["turn_count"] == 3
    assert c["host"] == "host-2.local"
    assert "summary" in c

    p = brief["previous"]
    assert p["stage"] == "DEV_REVIEW"
    assert p["result"] == "rejected"
    assert "测试覆盖率不足" in p["reason"]

    pr = brief["progress"]
    assert isinstance(pr["gates_passed"], list)
    assert isinstance(pr["gates_remaining"], list)


async def test_brief_minimal_run(db):
    """Brief for a freshly created run with no jobs or steps."""
    t = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run-2", "PROJ-99", "/repo", "running", "REQ_COLLECTING", t, t),
    )
    brief = await build_brief(db, "run-2")

    assert brief["run_id"] == "run-2"
    assert brief["current"]["stage"] == "REQ_COLLECTING"
    assert brief["current"]["description"] == "等待需求提交"
    assert brief["previous"] is None
    assert brief["progress"]["gates_passed"] == []
    assert brief["progress"]["gates_remaining"] == ["req", "design", "dev"]


async def test_brief_not_found(db):
    """build_brief returns None for nonexistent run."""
    result = await build_brief(db, "nonexistent")
    assert result is None


async def test_resolve_ticket_picks_running_over_completed(db):
    """resolve_run_by_ticket returns the active running run, not a completed one."""
    t = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run-old", "PROJ-T", "/repo", "completed", "MERGED", t, t),
    )
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run-new", "PROJ-T", "/repo", "running", "DEV_RUNNING", t, t),
    )
    result = await resolve_run_by_ticket(db, "PROJ-T")
    assert result == "run-new"


async def test_resolve_ticket_not_found(db):
    """resolve_run_by_ticket returns None for unknown ticket."""
    result = await resolve_run_by_ticket(db, "NOPE-999")
    assert result is None


# --- HTTP-level tests ---

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import AsyncClient, ASGITransport
from src.exceptions import NotFoundError, BadRequestError


@pytest.fixture
async def client(db):
    from routes.runs import router
    app = FastAPI()
    app.state.db = db

    @app.exception_handler(NotFoundError)
    async def _nf(request, exc):
        return JSONResponse(status_code=404, content={"error": "not_found", "message": str(exc)})

    @app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(status_code=400, content={"error": "bad_request", "message": str(exc)})

    app.include_router(router, prefix="/api/v1")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_brief_by_run_id(db, client):
    """GET /runs/{run_id}/brief returns 200 with brief data."""
    t = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run-http", "PROJ-HTTP", "/repo", "running", "DESIGN_RUNNING", t, t),
    )
    resp = await client.get("/api/v1/runs/run-http/brief")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-http"
    assert data["current"]["stage"] == "DESIGN_RUNNING"
    assert "previous" in data
    assert "progress" in data


async def test_brief_by_run_id_404(client):
    """GET /runs/{run_id}/brief returns 404 for unknown run."""
    resp = await client.get("/api/v1/runs/nope/brief")
    assert resp.status_code == 404


async def test_brief_by_ticket(db, client):
    """GET /runs/brief?ticket=X returns brief for the most recent active run."""
    t = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run-tk", "PROJ-TK", "/repo", "running", "REQ_REVIEW", t, t),
    )
    resp = await client.get("/api/v1/runs/brief", params={"ticket": "PROJ-TK"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticket"] == "PROJ-TK"
    assert data["run_id"] == "run-tk"


async def test_brief_by_ticket_404(client):
    """GET /runs/brief?ticket=X returns 404 for unknown ticket."""
    resp = await client.get("/api/v1/runs/brief", params={"ticket": "NOPE-999"})
    assert resp.status_code == 404


async def test_brief_by_ticket_missing_param(client):
    """GET /runs/brief without ticket param returns 400."""
    resp = await client.get("/api/v1/runs/brief")
    assert resp.status_code == 400
