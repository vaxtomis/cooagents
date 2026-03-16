import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.database import Database
from src.job_manager import JobManager
from src.agent_executor import AgentExecutor

@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()

@pytest.fixture
async def setup(db, tmp_path):
    jm = JobManager(db)
    hm = AsyncMock()
    hm.increment_load = AsyncMock()
    hm.decrement_load = AsyncMock()
    am = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()

    # Insert a dummy run
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r1", "T-1", str(tmp_path), "running", "DESIGN_QUEUED", now, now)
    )

    ae = AgentExecutor(db, jm, hm, am, wh, coop_dir=str(tmp_path / ".coop"))
    return ae, jm, hm, am, wh

async def test_build_command_claude(setup, tmp_path):
    ae, _, _, _, _ = setup
    task_file = tmp_path / "task.md"
    task_file.write_text("Do the design")
    cmd = ae._build_command("claude", str(task_file))
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "Do the design" in cmd

async def test_build_command_codex(setup, tmp_path):
    ae, _, _, _, _ = setup
    task_file = tmp_path / "task.md"
    task_file.write_text("Do the dev")
    cmd = ae._build_command("codex", str(task_file))
    assert cmd[0] == "codex"
    assert "-q" in cmd

async def test_job_manager_create(db, tmp_path):
    jm = JobManager(db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r1", "T-1", "/repo", "running", "INIT", now, now)
    )
    job_id = await jm.create_job("r1", "h1", "claude", "DESIGN_DISPATCHED", "/task.md", "/wt", "abc123", 1800)
    assert job_id.startswith("job-")
    jobs = await jm.get_jobs("r1")
    assert len(jobs) == 1
    assert jobs[0]["status"] == "starting"

async def test_job_manager_active_job(db, tmp_path):
    jm = JobManager(db)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r1", "T-1", "/repo", "running", "INIT", now, now)
    )
    job_id = await jm.create_job("r1", "h1", "claude", "DESIGN", "/task.md", "/wt", "abc", 1800)
    active = await jm.get_active_job("r1")
    assert active is not None
    assert dict(active)["id"] == job_id

async def test_restore_on_startup(setup, db, tmp_path):
    ae, jm, _, _, _ = setup
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,started_at) VALUES(?,?,?,?,?,?,?)",
        ("j1", "r1", "h1", "claude", "DESIGN", "running", now)
    )
    await ae.restore_on_startup()
    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("j1",))
    assert job["status"] == "interrupted"
