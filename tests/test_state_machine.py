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
