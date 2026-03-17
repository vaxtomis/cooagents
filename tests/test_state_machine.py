"""Tests for src/state_machine.py.

External dependencies that do not exist yet are mocked:
  - webhook_notifier  : notify(event_type, payload) → async no-op
  - agent_executor    : dispatch(run_id, host, agent_type, task_file,
                                 worktree, timeout_sec) → "job-123"
  - host_manager      : select_host(agent_type, preferred_host=None)
                        → dict or None
  - merge_manager     : enqueue / get_status as needed
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.database import Database
from src.artifact_manager import ArtifactManager
from src.state_machine import StateMachine
from src.exceptions import ConflictError
from src.job_manager import JobManager


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
def mocks():
    webhook = AsyncMock()
    webhook.notify = AsyncMock()
    executor = AsyncMock()
    executor.dispatch = AsyncMock(return_value="job-123")
    host_mgr = AsyncMock()
    host_mgr.select_host = AsyncMock(return_value={"id": "local", "host": "local"})
    merge_mgr = AsyncMock()
    return webhook, executor, host_mgr, merge_mgr


@pytest.fixture
async def sm(db, mocks, tmp_path):
    webhook, executor, host_mgr, merge_mgr = mocks
    am = ArtifactManager(db)
    jm = JobManager(db)

    # Provide a mock ensure_worktree so tests don't need a real git repository.
    async def _fake_ensure_worktree(repo_path, ticket, phase):
        branch = f"feat/{ticket}-{phase}"
        wt = str(tmp_path / f".worktrees/{ticket}-{phase}")
        return branch, wt

    # Stub out render_task so it doesn't try to read real template files.
    am.render_task = AsyncMock(return_value="task-path")

    machine = StateMachine(
        db,
        am,
        host_mgr,
        executor,
        webhook,
        merge_mgr,
        str(tmp_path),
        ensure_worktree_fn=_fake_ensure_worktree,
        job_manager=jm,
    )
    return machine


# ---------------------------------------------------------------------------
# create_run
# ---------------------------------------------------------------------------

async def test_create_run(sm):
    run = await sm.create_run("T-1", "/repo")
    assert run["current_stage"] == "REQ_COLLECTING"
    assert run["status"] == "running"


# ---------------------------------------------------------------------------
# submit_requirement
# ---------------------------------------------------------------------------

async def test_submit_requirement(sm, tmp_path):
    run = await sm.create_run("T-1", str(tmp_path))
    run = await sm.submit_requirement(run["run_id"], "# Requirement content")
    assert run["current_stage"] == "REQ_REVIEW"


# ---------------------------------------------------------------------------
# approve / reject at req gate
# ---------------------------------------------------------------------------

async def test_approve_req(sm, tmp_path):
    run = await sm.create_run("T-1", str(tmp_path))
    await sm.submit_requirement(run["run_id"], "# Req")
    run = await sm.approve(run["run_id"], "req", "user1")
    assert run["current_stage"] == "DESIGN_QUEUED"


async def test_reject_req(sm, tmp_path):
    run = await sm.create_run("T-1", str(tmp_path))
    await sm.submit_requirement(run["run_id"], "# Req")
    run = await sm.reject(run["run_id"], "req", "user1", "needs more detail")
    assert run["current_stage"] == "REQ_COLLECTING"


# ---------------------------------------------------------------------------
# tick: DESIGN_QUEUED
# ---------------------------------------------------------------------------

async def test_design_queued_with_host(sm, tmp_path):
    run = await sm.create_run("T-1", str(tmp_path))
    await sm.submit_requirement(run["run_id"], "# Req")
    await sm.approve(run["run_id"], "req", "user1")
    run = await sm.tick(run["run_id"])
    assert run["current_stage"] == "DESIGN_DISPATCHED"


async def test_design_queued_no_host(sm, mocks, tmp_path):
    _, _, host_mgr, _ = mocks
    host_mgr.select_host = AsyncMock(return_value=None)
    run = await sm.create_run("T-1", str(tmp_path))
    await sm.submit_requirement(run["run_id"], "# Req")
    await sm.approve(run["run_id"], "req", "user1")
    run = await sm.tick(run["run_id"])
    assert run["current_stage"] == "DESIGN_QUEUED"


# ---------------------------------------------------------------------------
# tick: review stages are idempotent
# ---------------------------------------------------------------------------

async def test_tick_review_idempotent(sm, tmp_path):
    run = await sm.create_run("T-1", str(tmp_path))
    await sm.submit_requirement(run["run_id"], "# Req")
    # REQ_REVIEW — tick should not change stage
    run = await sm.tick(run["run_id"])
    assert run["current_stage"] == "REQ_REVIEW"


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------

async def test_cancel(sm, tmp_path):
    run = await sm.create_run("T-1", str(tmp_path))
    run = await sm.cancel(run["run_id"])
    assert run["status"] == "cancelled"


# ---------------------------------------------------------------------------
# retry failed run
# ---------------------------------------------------------------------------

async def test_retry_failed(sm, db, tmp_path):
    run = await sm.create_run("T-1", str(tmp_path))
    rid = run["run_id"]
    await db.execute(
        "UPDATE runs SET status='failed', failed_at_stage='DESIGN_QUEUED' WHERE id=?",
        (rid,),
    )
    run = await sm.retry(rid, "user1")
    assert run["status"] == "running"
    assert run["current_stage"] == "DESIGN_QUEUED"


# ---------------------------------------------------------------------------
# Evaluator tests
# ---------------------------------------------------------------------------

async def test_evaluate_design_accept(sm):
    artifacts = [
        {"kind": "design", "path": "DES-T1.md"},
        {"kind": "adr", "path": "ADR-T1.md"},
    ]
    verdict, detail = sm._evaluate_design(artifacts)
    assert verdict == "accept"

async def test_evaluate_design_revise_missing_design(sm):
    artifacts = [{"kind": "adr", "path": "ADR-T1.md"}]
    verdict, detail = sm._evaluate_design(artifacts)
    assert verdict == "revise"
    assert "设计文档" in detail

async def test_evaluate_design_revise_missing_adr(sm):
    artifacts = [{"kind": "design", "path": "DES-T1.md"}]
    verdict, detail = sm._evaluate_design(artifacts)
    assert verdict == "revise"
    assert "ADR" in detail

async def test_evaluate_dev_accept(sm):
    artifacts = [{"kind": "test-report", "path": "TEST-REPORT-T1.md"}]
    verdict, detail = sm._evaluate_dev(artifacts)
    assert verdict == "accept"

async def test_evaluate_dev_revise_missing_report(sm):
    artifacts = []
    verdict, detail = sm._evaluate_dev(artifacts)
    assert verdict == "revise"

async def test_tick_design_running_multi_turn_revise(sm, mocks, db, tmp_path):
    """Design running: evaluator returns revise -> send_followup, stay in DESIGN_RUNNING."""
    _, executor, _, _ = mocks
    executor.send_followup = AsyncMock()
    executor.close_session = AsyncMock()

    run = await sm.create_run("T-MT", str(tmp_path))
    rid = run["run_id"]
    await sm.submit_requirement(rid, "# Req")
    await sm.approve(rid, "req", "user1")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,turn_count,started_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("job-mt1", rid, "local", "claude", "DESIGN_DISPATCHED", "completed", "/t.md", str(tmp_path), "run-mt-design", 1, now)
    )
    await db.execute(
        "UPDATE runs SET current_stage='DESIGN_RUNNING', design_worktree=? WHERE id=?",
        (str(tmp_path), rid),
    )

    # No design artifact -> evaluator should say "revise"
    run = await sm.tick(rid)
    assert run["current_stage"] == "DESIGN_RUNNING"  # stays in RUNNING
    executor.send_followup.assert_called_once()

async def test_tick_design_running_multi_turn_accept(sm, mocks, db, tmp_path):
    """Design running: evaluator returns accept -> advance to DESIGN_REVIEW."""
    _, executor, _, _ = mocks
    executor.close_session = AsyncMock()

    run = await sm.create_run("T-ACC", str(tmp_path))
    rid = run["run_id"]
    await sm.submit_requirement(rid, "# Req")
    await sm.approve(rid, "req", "user1")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,turn_count,started_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("job-acc1", rid, "local", "claude", "DESIGN_DISPATCHED", "completed", "/t.md", str(tmp_path), "run-acc-design", 1, now)
    )
    await db.execute(
        "UPDATE runs SET current_stage='DESIGN_RUNNING', design_worktree=? WHERE id=?",
        (str(tmp_path), rid),
    )

    # Create design + ADR artifacts so scan_and_register finds them and evaluator accepts
    design_dir = tmp_path / "docs" / "design"
    design_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / "DES-T-ACC.md").write_text("# Design")
    (design_dir / "ADR-T-ACC-001.md").write_text("# ADR")

    run = await sm.tick(rid)
    assert run["current_stage"] == "DESIGN_REVIEW"
    executor.close_session.assert_called_once()
