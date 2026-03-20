import pytest
from src.database import Database
from src.job_manager import JobManager


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


async def test_create_job_with_session(db, tmp_path):
    jm = JobManager(db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("h1", "local", "both", 4, "active", now, now)
    )
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r-sess", "T-1", "/repo", "running", "INIT", now, now)
    )
    job_id = await jm.create_job(
        "r-sess", "h1", "claude", "DESIGN_DISPATCHED", "/task.md", "/wt", "abc123", 1800,
        session_name="run-r1-design"
    )
    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert job["session_name"] == "run-r1-design"
    assert job["turn_count"] == 1


async def test_create_job_persists_timeout_metadata(db):
    jm = JobManager(db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("h1", "local", "both", 4, "active", now, now)
    )
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r-timeout", "T-2", "/repo", "running", "INIT", now, now)
    )

    job_id = await jm.create_job(
        "r-timeout", "h1", "claude", "DESIGN_DISPATCHED", "/task.md", "/wt", "abc123", 222
    )

    job = await db.fetchone("SELECT timeout_sec, running_started_at FROM jobs WHERE id=?", (job_id,))
    assert job["timeout_sec"] == 222
    assert job["running_started_at"] is None


async def test_mark_running_sets_running_started_at(db):
    jm = JobManager(db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("h1", "local", "both", 4, "active", now, now)
    )
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r-running", "T-3", "/repo", "running", "INIT", now, now)
    )
    job_id = await jm.create_job("r-running", "h1", "claude", "DESIGN", "/t.md", "/wt", "abc", 1800)

    await jm.mark_running(job_id, started_at=now)

    job = await db.fetchone("SELECT status, running_started_at FROM jobs WHERE id=?", (job_id,))
    assert job["status"] == "running"
    assert job["running_started_at"] == now


async def test_increment_turn(db, tmp_path):
    jm = JobManager(db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("h1", "local", "both", 4, "active", now, now)
    )
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r-turn", "T-1", "/repo", "running", "INIT", now, now)
    )
    job_id = await jm.create_job("r-turn", "h1", "claude", "DESIGN", "/t.md", "/wt", "abc", 1800)
    new_turn = await jm.increment_turn(job_id)
    assert new_turn == 2
    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert job["turn_count"] == 2


async def test_record_and_get_turns(db, tmp_path):
    jm = JobManager(db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("h1", "local", "both", 4, "active", now, now)
    )
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r-turns", "T-1", "/repo", "running", "INIT", now, now)
    )
    job_id = await jm.create_job("r-turns", "h1", "claude", "DESIGN", "/t.md", "/wt", "abc", 1800)
    await jm.record_turn(job_id, 1, "/t.md", "revise", "missing ADR")
    await jm.record_turn(job_id, 2, "/rev.md", "accept", "")
    turns = await jm.get_turns(job_id)
    assert len(turns) == 2
    assert turns[0]["verdict"] == "revise"
    assert turns[1]["verdict"] == "accept"


async def test_get_output_uses_configured_coop_dir(db, tmp_path):
    jm = JobManager(db, coop_dir=str(tmp_path / ".coop-custom"))
    events_path = tmp_path / ".coop-custom" / "jobs" / "job-out" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text('{"event":"ok"}\n', encoding="utf-8")

    output = await jm.get_output("job-out")

    assert output == '{"event":"ok"}\n'
