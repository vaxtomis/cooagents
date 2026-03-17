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


async def test_increment_turn(db, tmp_path):
    jm = JobManager(db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
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
