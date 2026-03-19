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
    (tmp_path / ".git").mkdir(exist_ok=True)
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

async def test_create_run(sm, tmp_path):
    run = await sm.create_run("T-1", str(tmp_path))
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


async def test_design_queued_no_host_caps_host_unavailable_to_three(sm, mocks, db, tmp_path):
    webhook, _, host_mgr, _ = mocks
    host_mgr.select_host = AsyncMock(return_value=None)
    run = await sm.create_run("T-HOST-CAP", str(tmp_path))
    await sm.submit_requirement(run["run_id"], "# Req")
    await sm.approve(run["run_id"], "req", "user1")

    for _ in range(4):
        run = await sm.tick(run["run_id"])

    events = await db.fetchall(
        "SELECT * FROM events WHERE run_id=? AND event_type='host.unavailable'",
        (run["run_id"],),
    )
    assert run["current_stage"] == "DESIGN_QUEUED"
    assert len(events) == 3
    host_unavailable_calls = [
        call for call in webhook.notify.await_args_list
        if call.args and call.args[0] == "host.unavailable"
    ]
    assert len(host_unavailable_calls) == 3


async def test_design_queued_copies_requirement_into_design_worktree(sm, tmp_path):
    run = await sm.create_run("T-REQCOPY", str(tmp_path))
    await sm.submit_requirement(run["run_id"], "# Requirement content")
    await sm.approve(run["run_id"], "req", "user1")

    await sm.tick(run["run_id"])

    copied_req = tmp_path / ".worktrees" / "T-REQCOPY-design" / "docs" / "req" / "REQ-T-REQCOPY.md"
    assert copied_req.read_text(encoding="utf-8") == "# Requirement content"
    render_args = sm.artifacts.render_task.await_args.args
    assert render_args[1]["req_path"] == "docs/req/REQ-T-REQCOPY.md"


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
    sm._design_max_turns = 2

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


async def test_design_execution_timeout_uses_configured_value(db, mocks, tmp_path):
    webhook, executor, host_mgr, merge_mgr = mocks
    executor.start_session = AsyncMock(return_value="job-123")
    am = ArtifactManager(db)
    jm = JobManager(db)
    am.render_task = AsyncMock(return_value="task-path")
    (tmp_path / ".git").mkdir(exist_ok=True)

    class FakeConfig:
        class timeouts:
            design_execution = 222
            dev_execution = 333
        class turns:
            design_max_turns = 2
            dev_max_turns = 1

    async def _fake_ensure_worktree(repo_path, ticket, phase):
        branch = f"feat/{ticket}-{phase}"
        wt = str(tmp_path / f".worktrees/{ticket}-{phase}")
        return branch, wt

    machine = StateMachine(
        db,
        am,
        host_mgr,
        executor,
        webhook,
        merge_mgr,
        str(tmp_path),
        ensure_worktree_fn=_fake_ensure_worktree,
        config=FakeConfig(),
        job_manager=jm,
    )

    run = await machine.create_run("T-CONFIG-TIMEOUT", str(tmp_path))
    await machine.submit_requirement(run["run_id"], "# Req")
    await machine.approve(run["run_id"], "req", "user1")
    await machine.tick(run["run_id"])

    assert executor.start_session.await_args.args[-1] == 222


async def test_design_followup_uses_configured_timeout(db, mocks, tmp_path):
    webhook, executor, host_mgr, merge_mgr = mocks
    executor.send_followup = AsyncMock()
    executor.close_session = AsyncMock()
    am = ArtifactManager(db)
    jm = JobManager(db)
    am.render_task = AsyncMock(return_value="task-path")
    (tmp_path / ".git").mkdir(exist_ok=True)

    class FakeConfig:
        class timeouts:
            design_execution = 444
            dev_execution = 555
        class turns:
            design_max_turns = 2
            dev_max_turns = 1

    async def _fake_ensure_worktree(repo_path, ticket, phase):
        branch = f"feat/{ticket}-{phase}"
        wt = str(tmp_path / f".worktrees/{ticket}-{phase}")
        return branch, wt

    machine = StateMachine(
        db,
        am,
        host_mgr,
        executor,
        webhook,
        merge_mgr,
        str(tmp_path),
        ensure_worktree_fn=_fake_ensure_worktree,
        config=FakeConfig(),
        job_manager=jm,
    )

    run = await machine.create_run("T-CONFIG-FOLLOWUP", str(tmp_path))
    rid = run["run_id"]
    await machine.submit_requirement(rid, "# Req")
    await machine.approve(rid, "req", "user1")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,turn_count,started_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("job-config-followup", rid, "local", "claude", "DESIGN_DISPATCHED", "completed", "/t.md", str(tmp_path), "run-config-followup-design", 1, now)
    )
    await db.execute(
        "UPDATE runs SET current_stage='DESIGN_RUNNING', design_worktree=? WHERE id=?",
        (str(tmp_path), rid),
    )

    run = await machine.tick(rid)

    assert run["current_stage"] == "DESIGN_RUNNING"
    assert executor.send_followup.await_args.args[-1] == 444

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


# ---------------------------------------------------------------------------
# Bug fix: job failure transitions run to FAILED
# ---------------------------------------------------------------------------

async def test_tick_design_running_job_failed(sm, db, tmp_path):
    """When design job fails, run should transition to FAILED."""
    run = await sm.create_run("T-FAIL", str(tmp_path))
    rid = run["run_id"]

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,turn_count,started_at,ended_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("job-fail1", rid, "local", "claude", "DESIGN_RUNNING", "failed", "/t.md", str(tmp_path), "run-fail-design", 1, now, now)
    )
    await db.execute(
        "UPDATE runs SET current_stage='DESIGN_RUNNING' WHERE id=?", (rid,),
    )

    run = await sm.tick(rid)
    assert run["status"] == "failed"
    assert run["current_stage"] == "FAILED"
    assert run["failed_at_stage"] == "DESIGN_RUNNING"


async def test_tick_dev_running_job_timeout(sm, db, tmp_path):
    """When dev job times out, run should transition to FAILED."""
    run = await sm.create_run("T-TOUT", str(tmp_path))
    rid = run["run_id"]

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,turn_count,started_at,ended_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("job-tout1", rid, "local", "codex", "DEV_RUNNING", "timeout", "/t.md", str(tmp_path), "run-tout-dev", 1, now, now)
    )
    await db.execute(
        "UPDATE runs SET current_stage='DEV_RUNNING' WHERE id=?", (rid,),
    )

    run = await sm.tick(rid)
    assert run["status"] == "failed"
    assert run["current_stage"] == "FAILED"
    assert run["failed_at_stage"] == "DEV_RUNNING"


async def test_dev_queued_copies_design_into_dev_worktree(sm, db, tmp_path):
    run = await sm.create_run("T-DESCOPY", str(tmp_path))
    rid = run["run_id"]
    await db.execute("UPDATE runs SET current_stage='DEV_QUEUED' WHERE id=?", (rid,))

    design_source = tmp_path / "design-output" / "DES-T-DESCOPY.md"
    design_source.parent.mkdir(parents=True, exist_ok=True)
    design_source.write_text("# Design content", encoding="utf-8")
    await sm.artifacts.register(rid, "design", str(design_source), "DESIGN_RUNNING")

    await sm.tick(rid)

    copied_design = tmp_path / ".worktrees" / "T-DESCOPY-dev" / "docs" / "design" / "DES-T-DESCOPY.md"
    assert copied_design.read_text(encoding="utf-8") == "# Design content"
    render_args = sm.artifacts.render_task.await_args.args
    assert render_args[1]["design_path"] == "docs/design/DES-T-DESCOPY.md"


async def test_retry_after_job_failure(sm, db, tmp_path):
    """After a running design job fails, retry should re-queue design work."""
    run = await sm.create_run("T-RETRY", str(tmp_path))
    rid = run["run_id"]

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,turn_count,started_at,ended_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("job-rf1", rid, "local", "claude", "DESIGN_RUNNING", "failed", "/t.md", str(tmp_path), "run-rf-design", 1, now, now)
    )
    await db.execute(
        "UPDATE runs SET current_stage='DESIGN_RUNNING' WHERE id=?", (rid,),
    )

    # Tick to FAILED
    run = await sm.tick(rid)
    assert run["status"] == "failed"

    # Retry should restore to DESIGN_QUEUED so a fresh job is dispatched
    run = await sm.retry(rid, "user1")
    assert run["status"] == "running"
    assert run["current_stage"] == "DESIGN_QUEUED"


@pytest.mark.parametrize(
    ("failed_stage", "expected_stage"),
    [
        ("DESIGN_DISPATCHED", "DESIGN_QUEUED"),
        ("DEV_RUNNING", "DEV_QUEUED"),
        ("DEV_DISPATCHED", "DEV_QUEUED"),
    ],
)
async def test_retry_requeues_dispatched_or_running_agent_stages(sm, db, tmp_path, failed_stage, expected_stage):
    """Retry should map agent-owned stages back to their queue stage."""
    run = await sm.create_run(f"T-RETRY-{failed_stage}", str(tmp_path))
    rid = run["run_id"]

    await db.execute(
        "UPDATE runs SET status='failed', current_stage='FAILED', failed_at_stage=? WHERE id=?",
        (failed_stage, rid),
    )

    run = await sm.retry(rid, "user1")
    assert run["status"] == "running"
    assert run["current_stage"] == expected_stage


# ---------------------------------------------------------------------------
# Bug fix: MERGE_CONFLICT exit via resolve_conflict
# ---------------------------------------------------------------------------

async def test_resolve_conflict(sm, db, tmp_path):
    """resolve_conflict should re-queue from MERGE_CONFLICT to MERGE_QUEUED."""
    run = await sm.create_run("T-MC", str(tmp_path))
    rid = run["run_id"]
    await db.execute(
        "UPDATE runs SET current_stage='MERGE_CONFLICT' WHERE id=?", (rid,),
    )

    run = await sm.resolve_conflict(rid, "user1")
    assert run["current_stage"] == "MERGE_QUEUED"


async def test_resolve_conflict_wrong_stage(sm, db, tmp_path):
    """resolve_conflict should fail if not in MERGE_CONFLICT."""
    run = await sm.create_run("T-MC2", str(tmp_path))
    with pytest.raises(ConflictError):
        await sm.resolve_conflict(run["run_id"], "user1")


# ---------------------------------------------------------------------------
# Bug fix: MERGING → MERGED records step
# ---------------------------------------------------------------------------

async def test_merging_to_merged_records_step(sm, mocks, db, tmp_path):
    """MERGING → MERGED should create a step record and stage.changed event."""
    _, _, _, merge_mgr = mocks
    merge_mgr.get_status = AsyncMock(return_value="merged")

    run = await sm.create_run("T-MRG", str(tmp_path))
    rid = run["run_id"]
    await db.execute(
        "UPDATE runs SET current_stage='MERGING' WHERE id=?", (rid,),
    )

    run = await sm.tick(rid)
    assert run["current_stage"] == "MERGED"
    assert run["status"] == "completed"

    # Verify step was recorded
    step = await db.fetchone(
        "SELECT * FROM steps WHERE run_id=? AND to_stage='MERGED'", (rid,),
    )
    assert step is not None
    assert step["from_stage"] == "MERGING"


# ------------------------------------------------------------------
# DISPATCHED → FAILED on job failure (GitHub issue #1)
# ------------------------------------------------------------------

async def test_design_dispatched_transitions_to_failed_on_job_failure(sm, db, mocks, tmp_path):
    """DESIGN_DISPATCHED should transition to FAILED when the job fails."""
    from datetime import datetime, timezone
    run = await sm.create_run("T-DF", str(tmp_path))
    rid = run["id"]

    # Fast-forward to DESIGN_DISPATCHED and insert a failed job
    await db.execute(
        "UPDATE runs SET current_stage='DESIGN_DISPATCHED' WHERE id=?", (rid,),
    )
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,started_at) VALUES(?,?,?,?,?,?,?)",
        ("job-desfail", rid, "local", "claude", "DESIGN_DISPATCHED", "failed", now),
    )

    # Tick should transition to FAILED
    result = await sm.tick(rid)
    assert result["current_stage"] == "FAILED"
    assert result["status"] == "failed"


async def test_dev_dispatched_transitions_to_failed_on_job_failure(sm, db, mocks, tmp_path):
    """DEV_DISPATCHED should transition to FAILED when the job fails."""
    run = await sm.create_run("T-DDF", str(tmp_path))
    rid = run["id"]

    # Fast-forward to DEV_DISPATCHED
    await db.execute(
        "UPDATE runs SET current_stage='DEV_DISPATCHED' WHERE id=?", (rid,),
    )
    # Insert a failed job
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,started_at) VALUES(?,?,?,?,?,?,?)",
        ("job-devfail", rid, "local", "codex", "DEV_DISPATCHED", "failed", now),
    )

    result = await sm.tick(rid)
    assert result["current_stage"] == "FAILED"
    assert result["status"] == "failed"


async def test_design_dispatched_reconciles_dead_running_session_to_failed(sm, db, mocks, tmp_path):
    """DESIGN_DISPATCHED should fail when DB says running but ACP session is dead."""
    _, executor, _, _ = mocks
    executor.get_session_status = AsyncMock(return_value={"status": "dead", "summary": "queue owner unavailable"})

    run = await sm.create_run("T-DES-DEAD", str(tmp_path))
    rid = run["id"]
    await db.execute("UPDATE runs SET current_stage='DESIGN_DISPATCHED' WHERE id=?", (rid,))

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,session_name,worktree,started_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("job-des-dead", rid, "local", "claude", "DESIGN_DISPATCHED", "running", "run-des-dead-design", str(tmp_path), now),
    )

    result = await sm.tick(rid)
    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("job-des-dead",))

    assert result["current_stage"] == "FAILED"
    assert result["status"] == "failed"
    assert job["status"] == "interrupted"
    assert job["ended_at"] is not None


async def test_design_running_prefers_end_turn_events_over_dead_session(sm, db, mocks, tmp_path):
    """DESIGN_RUNNING should treat end_turn as completed even if ACP status later reports dead."""
    _, executor, _, _ = mocks
    executor.get_session_status = AsyncMock(
        return_value={"status": "dead", "summary": "queue owner unavailable", "signal": "SIGTERM"}
    )
    executor.close_session = AsyncMock()

    run = await sm.create_run("T-DES-ENDTURN", str(tmp_path))
    rid = run["id"]
    await sm.submit_requirement(rid, "# Req")
    await sm.approve(rid, "req", "user1")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    events_path = tmp_path / "jobs" / "job-des-endturn" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text('{"id":2,"result":{"stopReason":"end_turn"}}\n', encoding="utf-8")

    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,turn_count,events_file,started_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "job-des-endturn",
            rid,
            "local",
            "claude",
            "DESIGN_RUNNING",
            "running",
            "/t.md",
            str(tmp_path),
            "run-des-endturn-design",
            1,
            str(events_path),
            now,
        ),
    )
    await db.execute(
        "UPDATE runs SET current_stage='DESIGN_RUNNING', design_worktree=? WHERE id=?",
        (str(tmp_path), rid),
    )

    design_dir = tmp_path / "docs" / "design"
    design_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / "DES-T-DES-ENDTURN.md").write_text("# Design", encoding="utf-8")
    (design_dir / "ADR-T-DES-ENDTURN-001.md").write_text("# ADR", encoding="utf-8")

    result = await sm.tick(rid)
    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("job-des-endturn",))
    events = await db.fetchall("SELECT event_type FROM events WHERE run_id=? ORDER BY id", (rid,))
    event_types = [row["event_type"] for row in events]

    assert result["current_stage"] == "DESIGN_REVIEW"
    assert result["status"] == "running"
    assert job["status"] == "completed"
    assert "job.interrupted" not in event_types
    executor.close_session.assert_called_once()


async def test_design_dispatched_keeps_alive_session_running(sm, db, mocks, tmp_path):
    """DESIGN_DISPATCHED should treat ACP status=alive as healthy and advance to DESIGN_RUNNING."""
    _, executor, _, _ = mocks
    executor.get_session_status = AsyncMock(return_value={"status": "alive"})

    run = await sm.create_run("T-DES-ALIVE", str(tmp_path))
    rid = run["id"]
    await db.execute("UPDATE runs SET current_stage='DESIGN_DISPATCHED' WHERE id=?", (rid,))

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,session_name,worktree,started_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("job-des-alive", rid, "local", "claude", "DESIGN_DISPATCHED", "running", "run-des-alive-design", str(tmp_path), now),
    )

    result = await sm.tick(rid)
    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("job-des-alive",))

    assert result["current_stage"] == "DESIGN_RUNNING"
    assert result["status"] == "running"
    assert job["status"] == "running"
    assert job["ended_at"] is None


async def test_dev_running_reconciles_missing_running_session_to_failed(sm, db, mocks, tmp_path):
    """DEV_RUNNING should fail when DB says running but ACP session cannot be found."""
    _, executor, _, _ = mocks
    executor.get_session_status = AsyncMock(return_value=None)

    run = await sm.create_run("T-DEV-MISSING", str(tmp_path))
    rid = run["id"]
    await db.execute("UPDATE runs SET current_stage='DEV_RUNNING' WHERE id=?", (rid,))

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,session_name,worktree,started_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("job-dev-missing", rid, "local", "codex", "DEV_RUNNING", "running", "run-dev-missing-dev", str(tmp_path), now),
    )

    result = await sm.tick(rid)
    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("job-dev-missing",))

    assert result["current_stage"] == "FAILED"
    assert result["status"] == "failed"
    assert job["status"] == "interrupted"
    assert job["ended_at"] is not None
