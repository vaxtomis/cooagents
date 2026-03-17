import pytest
import time
from httpx import AsyncClient, ASGITransport
from src.database import Database
from src.artifact_manager import ArtifactManager
from src.host_manager import HostManager
from src.job_manager import JobManager
from src.acpx_executor import AcpxExecutor
from src.webhook_notifier import WebhookNotifier
from src.merge_manager import MergeManager
from src.state_machine import StateMachine
from src.scheduler import Scheduler
from src.config import load_settings


@pytest.fixture
async def client(tmp_path):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from src.exceptions import NotFoundError, ConflictError

    # Build a fresh app for testing
    test_app = FastAPI(title="cooagents-test")

    settings = load_settings()
    db = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await db.connect()

    artifacts = ArtifactManager(db)
    hosts = HostManager(db)
    jobs = JobManager(db)
    webhooks = WebhookNotifier(db)
    merger = MergeManager(db, webhooks)
    executor = AcpxExecutor(db, jobs, hosts, artifacts, webhooks, coop_dir=str(tmp_path / ".coop"))
    sm = StateMachine(db, artifacts, hosts, executor, webhooks, merger, coop_dir=str(tmp_path / ".coop"))
    executor.set_state_machine(sm)

    test_app.state.db = db
    test_app.state.sm = sm
    test_app.state.artifacts = artifacts
    test_app.state.hosts = hosts
    test_app.state.jobs = jobs
    test_app.state.executor = executor
    test_app.state.webhooks = webhooks
    test_app.state.merger = merger
    test_app.state.settings = settings
    test_app.state.start_time = time.time()

    @test_app.exception_handler(NotFoundError)
    async def not_found_handler(request, exc):
        return JSONResponse(status_code=404, content={"error": "not_found", "message": str(exc)})

    @test_app.exception_handler(ConflictError)
    async def conflict_handler(request, exc):
        return JSONResponse(status_code=409, content={"error": "conflict", "message": str(exc), "current_stage": exc.current_stage})

    @test_app.get("/health")
    async def health(request: Request):
        active_runs = await db.fetchone("SELECT COUNT(*) as c FROM runs WHERE status='running'")
        active_jobs = await db.fetchone("SELECT COUNT(*) as c FROM jobs WHERE status IN ('starting','running')")
        return {
            "status": "ok",
            "uptime": int(time.time() - test_app.state.start_time),
            "db": "connected",
            "active_runs": active_runs["c"],
            "active_jobs": active_jobs["c"],
        }

    from routes.runs import router as runs_router
    from routes.artifacts import router as artifacts_router
    from routes.agent_hosts import router as hosts_router
    from routes.webhooks import router as webhooks_router
    from routes.repos import router as repos_router

    test_app.include_router(runs_router, prefix="/api/v1")
    test_app.include_router(artifacts_router, prefix="/api/v1")
    test_app.include_router(hosts_router, prefix="/api/v1")
    test_app.include_router(webhooks_router, prefix="/api/v1")
    test_app.include_router(repos_router, prefix="/api/v1")

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        yield c

    await webhooks.close()
    await db.close()


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_create_run(client):
    resp = await client.post("/api/v1/runs", json={"ticket": "T-1", "repo_path": "/tmp/repo"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["current_stage"] == "REQ_COLLECTING"


async def test_list_runs(client):
    await client.post("/api/v1/runs", json={"ticket": "T-list", "repo_path": "/tmp/repo"})
    resp = await client.get("/api/v1/runs")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


async def test_get_run_not_found(client):
    resp = await client.get("/api/v1/runs/nonexistent")
    assert resp.status_code == 404


async def test_approve_wrong_stage(client):
    resp = await client.post("/api/v1/runs", json={"ticket": "T-ws", "repo_path": "/tmp/repo"})
    run_id = resp.json().get("run_id") or resp.json().get("id")
    resp = await client.post(f"/api/v1/runs/{run_id}/approve", json={"gate": "req", "by": "user"})
    assert resp.status_code == 409


async def test_list_agent_hosts(client):
    resp = await client.get("/api/v1/agent-hosts")
    assert resp.status_code == 200


async def test_list_webhooks(client):
    resp = await client.get("/api/v1/webhooks")
    assert resp.status_code == 200
