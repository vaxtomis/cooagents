import asyncio
import json
import pytest
import time
from pathlib import Path
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


async def _make_test_repo(path: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git", "init", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    for cmd in [
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "checkout", "-b", "main"],
    ]:
        p = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await p.communicate()
    (path / "README.md").write_text("# test\n")
    for cmd in [
        ["git", "add", "README.md"],
        ["git", "commit", "-m", "init"],
    ]:
        p = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await p.communicate()


@pytest.fixture
async def client(tmp_path):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from src.exceptions import NotFoundError, ConflictError, BadRequestError

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

    @test_app.exception_handler(BadRequestError)
    async def bad_request_handler(request, exc):
        return JSONResponse(status_code=400, content={"error": "bad_request", "message": str(exc)})

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


@pytest.fixture
async def test_repo(tmp_path):
    repo = tmp_path / "test-repo"
    repo.mkdir()
    await _make_test_repo(repo)
    return str(repo)


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_create_run(client, test_repo):
    resp = await client.post("/api/v1/runs", json={"ticket": "T-1", "repo_path": test_repo})
    assert resp.status_code == 201
    data = resp.json()
    assert data["current_stage"] == "REQ_COLLECTING"


async def _seed_list_runs(client, test_repo):
    app = client._transport.app
    db = app.state.db

    rows = [
        ("run-a", "T-alpha-match", "DESIGN_QUEUED", "2026-03-18T00:00:01Z"),
        ("run-b", "T-beta-match", "REQ_COLLECTING", "2026-03-18T00:00:02Z"),
        ("run-c", "T-gamma-match", "DESIGN_QUEUED", "2026-03-18T00:00:04Z"),
        ("run-d", "T-delta", "DESIGN_QUEUED", "2026-03-18T00:00:03Z"),
    ]
    for run_id, ticket, current_stage, created_at in rows:
        await db.execute(
            "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (run_id, ticket, test_repo, "running", current_stage, created_at, created_at),
        )


async def test_list_runs_filters_ticket_and_current_stage(client, test_repo):
    await _seed_list_runs(client, test_repo)

    resp = await client.get(
        "/api/v1/runs",
        params={
            "ticket": "match",
            "current_stage": "DESIGN_QUEUED",
            "limit": 10,
            "offset": 0,
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    assert {row["ticket"] for row in resp.json()} == {"T-alpha-match", "T-gamma-match"}
    assert all(row["current_stage"] == "DESIGN_QUEUED" for row in resp.json())


async def test_list_runs_sorts_ticket_ascending(client, test_repo):
    await _seed_list_runs(client, test_repo)

    resp = await client.get(
        "/api/v1/runs",
        params={
            "ticket": "match",
            "sort_by": "ticket",
            "sort_order": "asc",
            "limit": 3,
            "offset": 0,
        },
    )
    assert resp.status_code == 200
    assert [row["ticket"] for row in resp.json()] == [
        "T-alpha-match",
        "T-beta-match",
        "T-gamma-match",
    ]


async def test_list_runs_returns_total_count_for_paginated_results(client, test_repo):
    await _seed_list_runs(client, test_repo)

    resp = await client.get(
        "/api/v1/runs",
        params={
            "ticket": "match",
            "current_stage": "DESIGN_QUEUED",
            "sort_by": "ticket",
            "sort_order": "asc",
            "limit": 1,
            "offset": 1,
        },
    )
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "2"
    assert len(resp.json()) == 1
    assert resp.json()[0]["ticket"] == "T-gamma-match"
    assert resp.json()[0]["current_stage"] == "DESIGN_QUEUED"


async def test_get_run_not_found(client):
    resp = await client.get("/api/v1/runs/nonexistent")
    assert resp.status_code == 404


async def test_approve_wrong_stage(client, test_repo):
    resp = await client.post("/api/v1/runs", json={"ticket": "T-ws", "repo_path": test_repo})
    run_id = resp.json().get("run_id") or resp.json().get("id")
    resp = await client.post(f"/api/v1/runs/{run_id}/approve", json={"gate": "req", "by": "user"})
    assert resp.status_code == 409


async def test_list_agent_hosts(client):
    resp = await client.get("/api/v1/agent-hosts")
    assert resp.status_code == 200


async def test_list_webhooks(client):
    resp = await client.get("/api/v1/webhooks")
    assert resp.status_code == 200


async def test_webhook_deliveries_include_openclaw_and_filter_webhook_id(client):
    app = client._transport.app
    db = app.state.db
    wid = await app.state.webhooks.register("http://example.com/hook")

    await db.execute(
        "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        (None, "webhook.delivery_failed",
         json.dumps({"webhook_id": wid, "event_type": "gate.waiting"}), "2026-03-18T00:00:01Z"),
    )
    await db.execute(
        "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        (None, "webhook.delivery_failed",
         json.dumps({"webhook_id": wid + 1, "event_type": "run.completed"}), "2026-03-18T00:00:02Z"),
    )
    await db.execute(
        "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        (None, "openclaw.hooks.delivery_failed",
         json.dumps({"event_type": "job.failed", "status_code": 502}), "2026-03-18T00:00:03Z"),
    )

    resp = await client.get(f"/api/v1/webhooks/{wid}/deliveries")
    assert resp.status_code == 200
    data = resp.json()
    event_types = [row["event_type"] for row in data]

    assert "openclaw.hooks.delivery_failed" in event_types
    assert event_types.count("webhook.delivery_failed") == 1
    assert json.loads(data[-1]["payload_json"])["webhook_id"] == wid


# ---------------------------------------------------------------------------
# Repo ensure endpoint tests
# ---------------------------------------------------------------------------

async def test_ensure_repo_existing(client, tmp_path):
    repo = tmp_path / "ensure-existing"
    repo.mkdir()
    await _make_test_repo(repo)
    resp = await client.post("/api/v1/repos/ensure", json={"repo_path": str(repo)})
    assert resp.status_code == 200
    assert resp.json()["status"] == "exists"


async def test_ensure_repo_init(client, tmp_path):
    repo = tmp_path / "ensure-init"
    resp = await client.post("/api/v1/repos/ensure", json={"repo_path": str(repo)})
    assert resp.status_code == 201
    assert resp.json()["status"] == "initialized"
    assert (repo / ".git").is_dir()


async def test_ensure_repo_not_git(client, tmp_path):
    plain = tmp_path / "ensure-plain"
    plain.mkdir()
    resp = await client.post("/api/v1/repos/ensure", json={"repo_path": str(plain)})
    assert resp.status_code == 400


async def test_create_run_invalid_repo(client, tmp_path):
    resp = await client.post("/api/v1/runs", json={
        "ticket": "T-bad-repo",
        "repo_path": str(tmp_path / "nonexistent"),
    })
    assert resp.status_code == 400


async def test_create_run_with_repo_url(client, tmp_path):
    repo = tmp_path / "url-repo"
    repo.mkdir()
    await _make_test_repo(repo)
    resp = await client.post("/api/v1/runs", json={
        "ticket": "T-url",
        "repo_path": str(repo),
        "repo_url": "git@github.com:user/project.git",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["current_stage"] == "REQ_COLLECTING"
    # Verify repo_url is persisted in DB
    run_id = data.get("run_id") or data.get("id")
    resp2 = await client.get(f"/api/v1/runs/{run_id}")
    assert resp2.json().get("repo_url") == "git@github.com:user/project.git"


def _make_spa_app(project_root: Path):
    from fastapi import FastAPI
    from src.app import mount_dashboard_spa

    app = FastAPI()

    @app.get("/api/v1/ping")
    async def ping():
        return {"ok": True}

    mount_dashboard_spa(app, project_root=project_root)
    return app


async def test_api_routes_still_resolve_when_spa_mounted(tmp_path):
    project_root = tmp_path / "spa-project"
    dist_dir = project_root / "web" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>dashboard shell</body></html>", encoding="utf-8")

    app = _make_spa_app(project_root)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/ping")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_spa_fallback_serves_index_html_for_non_api_routes(tmp_path):
    project_root = tmp_path / "spa-project"
    dist_dir = project_root / "web" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>dashboard shell</body></html>", encoding="utf-8")

    app = _make_spa_app(project_root)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/runs/run-123")

    assert resp.status_code == 200
    assert "dashboard shell" in resp.text
    assert resp.headers["content-type"].startswith("text/html")


async def test_spa_serves_static_assets_from_dist(tmp_path):
    project_root = tmp_path / "spa-project"
    dist_dir = project_root / "web" / "dist"
    asset_dir = dist_dir / "assets"
    asset_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>dashboard shell</body></html>", encoding="utf-8")
    (asset_dir / "app.js").write_text("console.log('dashboard');", encoding="utf-8")

    app = _make_spa_app(project_root)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/assets/app.js")

    assert resp.status_code == 200
    assert resp.text == "console.log('dashboard');"
