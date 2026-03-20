import asyncio
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
from src.artifact_manager import ArtifactManager
from src.database import Database
from src.job_manager import JobManager
from src.scheduler import Scheduler
from src.state_machine import StateMachine

@pytest.fixture
def sched():
    db = AsyncMock()
    hm = AsyncMock()
    jm = AsyncMock()
    ae = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()

    class FakeConfig:
        class health_check:
            interval = 60
            ssh_timeout = 5
        class timeouts:
            dispatch_startup = 300
            design_execution = 1800
            dev_execution = 3600
            review_reminder = 86400

    return Scheduler(db, hm, jm, ae, wh, FakeConfig())


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()

async def test_start_stop(sched):
    await sched.start()
    assert len(sched._tasks) == 3
    await sched.stop()
    assert len(sched._tasks) == 0


async def test_tick_runnable_runs_includes_dispatched_and_running_stages():
    db = AsyncMock()
    hm = AsyncMock()
    jm = AsyncMock()
    ae = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()
    sm = AsyncMock()
    sm.tick = AsyncMock()

    class FakeConfig:
        class health_check:
            interval = 60
            ssh_timeout = 5
        class timeouts:
            dispatch_startup = 300
            design_execution = 1800
            dev_execution = 3600
            review_reminder = 86400

    db.fetchall = AsyncMock(return_value=[
        {"id": "run-design-queued"},
        {"id": "run-design-dispatched"},
        {"id": "run-design-running"},
        {"id": "run-dev-queued"},
        {"id": "run-dev-dispatched"},
        {"id": "run-dev-running"},
    ])

    sched = Scheduler(db, hm, jm, ae, wh, FakeConfig(), state_machine=sm)
    await sched._tick_runnable_runs()

    fetched_sql = db.fetchall.await_args.args[0]
    assert "DESIGN_DISPATCHED" in fetched_sql
    assert "DESIGN_RUNNING" in fetched_sql
    assert "DEV_DISPATCHED" in fetched_sql
    assert "DEV_RUNNING" in fetched_sql
    assert sm.tick.await_count == 6


async def test_notify_limited_caps_review_reminder_to_three(db):
    hm = AsyncMock()
    ae = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()
    jm = JobManager(db)

    class FakeConfig:
        class health_check:
            interval = 60
            ssh_timeout = 5
        class timeouts:
            dispatch_startup = 300
            design_execution = 1800
            dev_execution = 3600
            review_reminder = 86400

    sched = Scheduler(db, hm, jm, ae, wh, FakeConfig())
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-review-1", "T-1", "/repo", "running", "DESIGN_REVIEW", "2026-03-20T00:00:00+00:00", "2026-03-20T00:00:00+00:00"),
    )

    for _ in range(4):
        await sched._notify_limited(
            "run-review-1",
            "review.reminder",
            {"run_id": "run-review-1", "ticket": "T-1", "stage": "DESIGN_REVIEW"},
            limit_keys=("stage",),
        )

    rows = await db.fetchall(
        "SELECT * FROM events WHERE run_id=? AND event_type='review.reminder'",
        ("run-review-1",),
    )
    assert len(rows) == 3
    assert wh.notify.await_count == 3


async def test_handle_job_timeout_marks_run_failed_and_limits_notifications(db, tmp_path):
    hm = AsyncMock()
    ae = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()
    jm = JobManager(db)
    webhook = AsyncMock()
    webhook.notify = AsyncMock()
    host_mgr = AsyncMock()
    merge_mgr = AsyncMock()
    am = ArtifactManager(db)
    (tmp_path / ".git").mkdir(exist_ok=True)

    class FakeConfig:
        class health_check:
            interval = 60
            ssh_timeout = 5
        class timeouts:
            dispatch_startup = 300
            design_execution = 1800
            dev_execution = 3600
            review_reminder = 86400

    sm = StateMachine(db, am, host_mgr, ae, webhook, merge_mgr, str(tmp_path), job_manager=jm)
    sched = Scheduler(db, hm, jm, ae, wh, FakeConfig(), state_machine=sm)

    run = await sm.create_run("T-TIMEOUT", str(tmp_path))
    rid = run["id"]
    now = "2026-03-20T00:00:00+00:00"
    await db.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("local", "local", "both", 4, "active", now, now),
    )
    await db.execute("UPDATE runs SET current_stage='DESIGN_RUNNING' WHERE id=?", (rid,))
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,started_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("job-timeout-1", rid, "local", "claude", "DESIGN_RUNNING", "running", "/t.md", str(tmp_path), "run-timeout-1-design", now),
    )

    async def _mark_timeout(run_id, agent_type, final_status="cancelled"):
        await jm.update_status("job-timeout-1", final_status, ended_at=now)

    ae.cancel_session = AsyncMock(side_effect=_mark_timeout)
    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("job-timeout-1",))

    for _ in range(4):
        await sched._handle_job_timeout(job, now)

    updated_job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("job-timeout-1",))
    updated_run = await db.fetchone("SELECT * FROM runs WHERE id=?", (rid,))
    timeout_events = await db.fetchall(
        "SELECT * FROM events WHERE run_id=? AND event_type='job.timeout'",
        (rid,),
    )

    assert updated_job["status"] == "timeout"
    assert updated_run["status"] == "failed"
    assert updated_run["current_stage"] == "FAILED"
    assert len(timeout_events) == 1
    assert wh.notify.await_count == 1
    payload = json.loads(timeout_events[0]["payload_json"])
    assert payload["stage"] == "DESIGN_RUNNING"
    assert payload["job_stage"] == "DESIGN_RUNNING"
    assert payload["current_stage"] == "DESIGN_RUNNING"


async def test_timeout_enforcement_uses_running_started_at_and_job_timeout_sec(db, tmp_path):
    hm = AsyncMock()
    ae = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()
    jm = JobManager(db)
    (tmp_path / ".git").mkdir(exist_ok=True)

    class FakeConfig:
        class health_check:
            interval = 60
            ssh_timeout = 5
        class timeouts:
            dispatch_startup = 300
            design_execution = 30
            dev_execution = 30
            review_reminder = 86400

    sched = Scheduler(db, hm, jm, ae, wh, FakeConfig())
    now = "2026-03-20T00:00:00+00:00"
    await db.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("local", "local", "both", 4, "active", now, now),
    )
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-live-timeout", "T-LIVE", str(tmp_path), "running", "DESIGN_RUNNING", now, now),
    )
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,timeout_sec,started_at,running_started_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "job-live-timeout",
            "run-live-timeout",
            "local",
            "claude",
            "DESIGN_RUNNING",
            "running",
            "/t.md",
            str(tmp_path),
            "run-live-timeout-design",
            600,
            "2026-03-19T23:00:00+00:00",
            "2026-03-19T23:55:00+00:00",
        ),
    )

    sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    with patch("src.scheduler.asyncio.sleep", sleep_mock), patch("src.scheduler.datetime") as dt_mock:
        dt_mock.now.return_value = datetime.fromisoformat(now)
        dt_mock.fromisoformat.side_effect = datetime.fromisoformat
        dt_mock.side_effect = datetime
        dt_mock.timezone = timezone
        dt_mock.timedelta = timedelta
        try:
            await sched._timeout_enforcement_loop()
        except asyncio.CancelledError:
            pass

    ae.cancel_session.assert_not_called()


async def test_handle_job_timeout_skips_notification_after_stage_advances(db, tmp_path):
    hm = AsyncMock()
    ae = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()
    jm = JobManager(db)
    (tmp_path / ".git").mkdir(exist_ok=True)

    class FakeConfig:
        class health_check:
            interval = 60
            ssh_timeout = 5
        class timeouts:
            dispatch_startup = 300
            design_execution = 1800
            dev_execution = 3600
            review_reminder = 86400

    sched = Scheduler(db, hm, jm, ae, wh, FakeConfig())
    now = "2026-03-20T00:00:00+00:00"
    await db.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("local", "local", "both", 4, "active", now, now),
    )
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-advanced", "T-ADV", str(tmp_path), "running", "DESIGN_REVIEW", now, now),
    )
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,started_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "job-advanced",
            "run-advanced",
            "local",
            "claude",
            "DESIGN_RUNNING",
            "running",
            "/t.md",
            str(tmp_path),
            "run-advanced-design",
            now,
        ),
    )

    async def _mark_timeout(run_id, agent_type, final_status="cancelled"):
        await jm.update_status("job-advanced", final_status, ended_at=now)

    ae.cancel_session = AsyncMock(side_effect=_mark_timeout)
    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("job-advanced",))

    await sched._handle_job_timeout(job, datetime.fromisoformat(now))

    timeout_events = await db.fetchall(
        "SELECT * FROM events WHERE run_id=? AND event_type='job.timeout'",
        ("run-advanced",),
    )
    assert timeout_events == []
    assert wh.notify.await_count == 0


async def test_handle_job_timeout_routes_progression_through_job_status_callback(db, tmp_path):
    hm = AsyncMock()
    ae = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()
    jm = JobManager(db)
    sm = AsyncMock()
    sm.on_job_status_changed = AsyncMock()

    class FakeConfig:
        class health_check:
            interval = 60
            ssh_timeout = 5
        class timeouts:
            dispatch_startup = 300
            design_execution = 1800
            dev_execution = 3600
            review_reminder = 86400

    sched = Scheduler(db, hm, jm, ae, wh, FakeConfig(), state_machine=sm)
    now = "2026-03-20T00:00:00+00:00"
    await db.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("local", "local", "both", 4, "active", now, now),
    )
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-timeout-callback", "T-CB", str(tmp_path), "running", "DESIGN_RUNNING", now, now),
    )
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,started_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "job-timeout-callback",
            "run-timeout-callback",
            "local",
            "claude",
            "DESIGN_RUNNING",
            "running",
            "/t.md",
            str(tmp_path),
            "run-timeout-callback-design",
            now,
        ),
    )

    async def _mark_timeout(run_id, agent_type, final_status="cancelled"):
        await jm.update_status("job-timeout-callback", final_status, ended_at=now)

    ae.cancel_session = AsyncMock(side_effect=_mark_timeout)
    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("job-timeout-callback",))

    await sched._handle_job_timeout(job, datetime.fromisoformat(now))

    sm.on_job_status_changed.assert_awaited_once_with("run-timeout-callback", "job-timeout-callback", "timeout")
    sm.tick.assert_not_called()


async def test_timeout_enforcement_continues_to_auto_tick_after_timeout_handler_error():
    db = AsyncMock()
    hm = AsyncMock()
    jm = AsyncMock()
    ae = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()
    sm = AsyncMock()
    sm.tick = AsyncMock()

    class FakeConfig:
        class health_check:
            interval = 60
            ssh_timeout = 5
        class timeouts:
            dispatch_startup = 300
            design_execution = 1800
            dev_execution = 3600
            review_reminder = 86400

    db.fetchall = AsyncMock(side_effect=[
        [],
        [{"id": "job-timeout", "run_id": "run-1", "stage": "DESIGN_RUNNING", "status": "running", "started_at": "2026-03-20T00:00:00+00:00"}],
    ])
    sched = Scheduler(db, hm, jm, ae, wh, FakeConfig(), state_machine=sm)
    sched._handle_job_timeout = AsyncMock(side_effect=RuntimeError("boom"))
    sched._tick_runnable_runs = AsyncMock()

    sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    with patch("src.scheduler.asyncio.sleep", sleep_mock), patch("src.scheduler.datetime") as dt_mock:
        dt_mock.now.return_value = datetime.fromisoformat("2026-03-20T01:00:00+00:00")
        dt_mock.fromisoformat.side_effect = datetime.fromisoformat
        dt_mock.side_effect = datetime
        dt_mock.timezone = timezone
        dt_mock.timedelta = timedelta
        try:
            await sched._timeout_enforcement_loop()
        except asyncio.CancelledError:
            pass

    sched._tick_runnable_runs.assert_awaited_once()
