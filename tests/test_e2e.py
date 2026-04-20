"""End-to-end workflow integration tests.

Tests the full lifecycle through the HTTP API with mocked agent execution.
"""
import time
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.database import Database
from src.artifact_manager import ArtifactManager
from src.host_manager import HostManager
from src.job_manager import JobManager
from src.acpx_executor import AcpxExecutor
from src.webhook_notifier import WebhookNotifier
from src.merge_manager import MergeManager
from src.state_machine import StateMachine
from src.auth import get_current_user
from src.config import load_settings
from src.exceptions import NotFoundError, ConflictError, BadRequestError


@pytest.fixture
async def setup(tmp_path):
    """Set up a full test app with a real DB and mocked agent dispatch."""
    (tmp_path / ".git").mkdir(exist_ok=True)
    test_app = FastAPI(title="cooagents-e2e")

    settings = load_settings()
    settings.security.workspace_root = str(tmp_path)
    db = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await db.connect()

    coop = str(tmp_path / ".coop")

    artifacts = ArtifactManager(db)
    hosts = HostManager(db)
    jobs = JobManager(db)
    webhooks = WebhookNotifier(db)
    merger = MergeManager(db, webhooks)
    executor = AcpxExecutor(db, jobs, hosts, artifacts, webhooks, coop_dir=coop)
    executor.close_session = AsyncMock()
    executor.send_followup = AsyncMock()
    executor.get_session_status = AsyncMock(return_value={"status": "alive"})

    # Fake worktree function — avoids needing a real git repo in e2e tests
    async def fake_ensure_worktree(repo_path, ticket, phase, run_suffix=""):
        wt = str(tmp_path / f".worktrees/{ticket}-{phase}")
        import os
        os.makedirs(wt, exist_ok=True)
        branch = f"feat/{ticket}-{phase}"
        return branch, wt

    sm = StateMachine(db, artifacts, hosts, executor, webhooks, merger, coop_dir=coop, ensure_worktree_fn=fake_ensure_worktree, job_manager=jobs)
    executor.set_state_machine(sm)

    # Register a local test host
    await hosts.register("test-host", "local", "both", max_concurrent=4)

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

    # Auth is covered in dedicated tests; bypass here so behavioural e2e flows
    # don't need token plumbing.
    test_app.dependency_overrides[get_current_user] = lambda: "test"

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
        return {"status": "ok", "uptime": 0, "db": "connected", "active_runs": active_runs["c"], "active_jobs": active_jobs["c"]}

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

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        yield client, db, sm, executor, artifacts, tmp_path

    await webhooks.close()
    await db.close()


async def test_full_workflow_happy_path(setup):
    """Full happy path: create → submit req → approve req → (mock design) → approve design → (mock dev) → approve dev → merge."""
    client, db, sm, executor, artifacts, tmp_path = setup

    # 1. Create run
    resp = await client.post("/api/v1/runs", json={"ticket": "E2E-1", "repo_path": str(tmp_path)})
    assert resp.status_code == 201
    data = resp.json()
    run_id = data.get("run_id") or data.get("id")
    assert data["current_stage"] == "REQ_COLLECTING"

    # 2. Submit requirement
    resp = await client.post(f"/api/v1/runs/{run_id}/submit-requirement", json={"content": "# E2E Requirement\nBuild a widget."})
    assert resp.status_code == 200
    assert resp.json()["current_stage"] == "REQ_REVIEW"

    # 3. Check artifacts
    resp = await client.get(f"/api/v1/runs/{run_id}/artifacts")
    assert resp.status_code == 200
    arts = resp.json()
    assert any(a["kind"] == "req" for a in arts)

    # 4. Approve req gate → DESIGN_QUEUED
    resp = await client.post(f"/api/v1/runs/{run_id}/approve", json={"gate": "req", "by": "tester"})
    assert resp.status_code == 200
    assert resp.json()["current_stage"] == "DESIGN_QUEUED"

    # 5. Simulate design agent: mock dispatch to create job row + advance
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    async def mock_design_dispatch(rid, host, atype, tf, wt, timeout, revision=None):
        await db.execute(
            "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,started_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("job-design-1", rid, host["id"], atype, "DESIGN_DISPATCHED", "starting", tf, wt, now)
        )
        return "job-design-1"

    with patch.object(executor, "start_session", side_effect=mock_design_dispatch):
        resp = await client.post(f"/api/v1/runs/{run_id}/tick")
        assert resp.json()["current_stage"] == "DESIGN_DISPATCHED"

    # Simulate job running then completed
    await db.execute("UPDATE jobs SET status='running' WHERE id='job-design-1'")
    resp = await client.post(f"/api/v1/runs/{run_id}/tick")
    assert resp.json()["current_stage"] == "DESIGN_RUNNING"

    # Create fake design + ADR artifacts in the worktree directory
    wt_design = tmp_path / ".worktrees" / "E2E-1-design"
    design_dir = wt_design / "docs" / "design"
    design_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / "DES-E2E-1.md").write_text("# Design for E2E-1\nArchitecture details.")
    (design_dir / "ADR-E2E-1.md").write_text("# ADR for E2E-1\nDecision record.")
    await db.execute("UPDATE jobs SET status='completed', ended_at=? WHERE id='job-design-1'", (now,))
    resp = await client.post(f"/api/v1/runs/{run_id}/tick")
    assert resp.json()["current_stage"] == "DESIGN_REVIEW"

    # 6. Approve design → DEV_QUEUED
    resp = await client.post(f"/api/v1/runs/{run_id}/approve", json={"gate": "design", "by": "tester"})
    assert resp.status_code == 200
    assert resp.json()["current_stage"] == "DEV_QUEUED"

    # 7. Simulate dev agent
    async def mock_dev_dispatch(rid, host, atype, tf, wt, timeout, revision=None):
        dev_now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,started_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("job-dev-1", rid, host["id"], atype, "DEV_DISPATCHED", "starting", tf, wt, dev_now)
        )
        return "job-dev-1"

    with patch.object(executor, "start_session", side_effect=mock_dev_dispatch):
        resp = await client.post(f"/api/v1/runs/{run_id}/tick")
        assert resp.json()["current_stage"] == "DEV_DISPATCHED"

    await db.execute("UPDATE jobs SET status='running' WHERE id='job-dev-1'")
    resp = await client.post(f"/api/v1/runs/{run_id}/tick")
    assert resp.json()["current_stage"] == "DEV_RUNNING"

    # Create fake test-report artifact in the dev worktree
    wt_dev = tmp_path / ".worktrees" / "E2E-1-dev"
    dev_dir = wt_dev / "docs" / "dev"
    dev_dir.mkdir(parents=True, exist_ok=True)
    (dev_dir / "TEST-REPORT-E2E-1.md").write_text("# Test Report\nAll tests passed.")
    await db.execute("UPDATE jobs SET status='completed', ended_at=? WHERE id='job-dev-1'", (now,))
    resp = await client.post(f"/api/v1/runs/{run_id}/tick")
    assert resp.json()["current_stage"] == "DEV_REVIEW"

    # 8. Approve dev → MERGE_QUEUED
    resp = await client.post(f"/api/v1/runs/{run_id}/approve", json={"gate": "dev", "by": "tester"})
    assert resp.status_code == 200
    assert resp.json()["current_stage"] == "MERGE_QUEUED"

    # 9. Verify full detail response
    resp = await client.get(f"/api/v1/runs/{run_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert len(detail["steps"]) >= 8  # multiple stage transitions
    assert len(detail["approvals"]) == 3


async def test_design_rejection_and_redo(setup):
    """Reject design and verify it goes back to DESIGN_QUEUED."""
    client, db, sm, executor, artifacts, tmp_path = setup

    # Create and advance to DESIGN_REVIEW
    resp = await client.post("/api/v1/runs", json={"ticket": "E2E-REJ", "repo_path": str(tmp_path)})
    run_id = resp.json().get("run_id") or resp.json().get("id")

    await client.post(f"/api/v1/runs/{run_id}/submit-requirement", json={"content": "# Req"})
    await client.post(f"/api/v1/runs/{run_id}/approve", json={"gate": "req", "by": "tester"})

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    async def mock_dispatch(rid, host, atype, tf, wt, timeout, revision=None):
        await db.execute(
            "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,started_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("job-d1", rid, host["id"], atype, "DESIGN_DISPATCHED", "starting", tf, wt, now)
        )
        return "job-d1"

    with patch.object(executor, "start_session", side_effect=mock_dispatch):
        await client.post(f"/api/v1/runs/{run_id}/tick")

    await db.execute("UPDATE jobs SET status='running' WHERE id='job-d1'")
    await client.post(f"/api/v1/runs/{run_id}/tick")

    # Create design + ADR artifacts in the worktree
    wt_design = tmp_path / ".worktrees" / "E2E-REJ-design"
    design_dir = wt_design / "docs" / "design"
    design_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / "DES-E2E-REJ.md").write_text("# Design\nInitial design.")
    (design_dir / "ADR-E2E-REJ.md").write_text("# ADR\nDecision record.")
    await db.execute("UPDATE jobs SET status='completed', ended_at=? WHERE id='job-d1'", (now,))
    await client.post(f"/api/v1/runs/{run_id}/tick")

    # Now at DESIGN_REVIEW — reject
    resp = await client.post(f"/api/v1/runs/{run_id}/reject", json={"gate": "design", "by": "tester", "reason": "Needs more detail on error handling"})
    assert resp.status_code == 200
    assert resp.json()["current_stage"] == "DESIGN_QUEUED"

    # Verify rejection recorded
    resp = await client.get(f"/api/v1/runs/{run_id}")
    approvals = resp.json()["approvals"]
    rejected = [a for a in approvals if a["decision"] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["comment"] == "Needs more detail on error handling"


async def test_cancel_running_task(setup):
    """Cancel a task and verify status changes."""
    client, db, sm, executor, artifacts, tmp_path = setup

    resp = await client.post("/api/v1/runs", json={"ticket": "E2E-CANCEL", "repo_path": str(tmp_path)})
    run_id = resp.json().get("run_id") or resp.json().get("id")

    # Cancel
    resp = await client.delete(f"/api/v1/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # Verify cannot approve after cancel
    resp = await client.post(f"/api/v1/runs/{run_id}/approve", json={"gate": "req", "by": "tester"})
    # Should fail — run is not in a running state
    assert resp.status_code in (409, 500)


async def test_retry_failed_task(setup):
    """Retry a failed task and verify it restores to the correct stage."""
    client, db, sm, executor, artifacts, tmp_path = setup

    resp = await client.post("/api/v1/runs", json={"ticket": "E2E-RETRY", "repo_path": str(tmp_path)})
    run_id = resp.json().get("run_id") or resp.json().get("id")

    # Manually fail the run
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE runs SET status='failed', failed_at_stage='DESIGN_QUEUED', updated_at=? WHERE id=?",
        (now, run_id)
    )

    # Retry
    resp = await client.post(f"/api/v1/runs/{run_id}/retry", json={"by": "tester", "note": "Fixed the issue"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert data["current_stage"] == "DESIGN_QUEUED"
