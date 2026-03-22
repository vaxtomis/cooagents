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
