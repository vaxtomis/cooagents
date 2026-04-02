import pytest
from src.database import Database
from src.host_manager import HostManager
from src.job_manager import JobManager

@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()

@pytest.fixture
async def hm(db):
    return HostManager(db)


@pytest.fixture
async def jobs(db):
    return JobManager(db)

async def test_register_host(hm):
    await hm.register("h1", "local", "both", max_concurrent=2)
    hosts = await hm.list_all()
    assert len(hosts) == 1
    assert hosts[0]["id"] == "h1"

async def test_select_host_least_loaded(hm, db, jobs):
    await hm.register("h1", "local", "both", max_concurrent=3)
    await hm.register("h2", "local", "both", max_concurrent=3)
    now = "2026-03-20T00:00:00+00:00"
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-loaded-h1", "T-H1", "/repo", "running", "DESIGN_RUNNING", now, now),
    )
    await jobs.create_job("run-loaded-h1", "h1", "claude", "DESIGN_RUNNING", "/task.md", "/wt", "abc123", 1800)
    job = await db.fetchone("SELECT id FROM jobs WHERE run_id=?", ("run-loaded-h1",))
    await jobs.update_status(job["id"], "running")
    host = await hm.select_host("claude")
    assert host["id"] == "h2"

async def test_select_host_filters_offline(hm):
    await hm.register("h1", "local", "both")
    await hm.set_status("h1", "offline")
    host = await hm.select_host("claude")
    assert host is None

async def test_select_host_filters_agent_type(hm):
    await hm.register("h1", "local", "codex")
    host = await hm.select_host("claude")
    assert host is None

async def test_select_host_respects_max(hm, db, jobs):
    await hm.register("h1", "local", "both", max_concurrent=1)
    now = "2026-03-20T00:00:00+00:00"
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-maxed-h1", "T-MAX", "/repo", "running", "DESIGN_RUNNING", now, now),
    )
    await jobs.create_job("run-maxed-h1", "h1", "claude", "DESIGN_RUNNING", "/task.md", "/wt", "abc123", 1800)
    job = await db.fetchone("SELECT id FROM jobs WHERE run_id=?", ("run-maxed-h1",))
    await jobs.update_status(job["id"], "running")
    host = await hm.select_host("claude")
    assert host is None

async def test_select_host_preference(hm):
    await hm.register("h1", "local", "both", max_concurrent=3)
    await hm.register("h2", "local", "both", max_concurrent=3)
    host = await hm.select_host("claude", preferred_host="h2")
    assert host["id"] == "h2"

async def test_increment_decrement(hm):
    await hm.register("h1", "local", "both")
    await hm.increment_load("h1")
    hosts = await hm.list_all()
    assert hosts[0]["current_load"] == 0
    await hm.decrement_load("h1")
    hosts = await hm.list_all()
    assert hosts[0]["current_load"] == 0


async def test_select_host_uses_real_jobs_only(hm, db, jobs):
    await hm.register("h1", "local", "both", max_concurrent=1)

    now = "2026-03-20T00:00:00+00:00"
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-real-load", "T-LOAD", "/repo", "running", "DESIGN_RUNNING", now, now),
    )
    await jobs.create_job("run-real-load", "h1", "claude", "DESIGN_RUNNING", "/task.md", "/wt", "abc123", 1800)
    job = await db.fetchone("SELECT id FROM jobs WHERE run_id=?", ("run-real-load",))
    await jobs.update_status(job["id"], "running")

    host = await hm.select_host("claude")
    assert host is None


async def test_select_host_ignores_orphan_jobs(hm, db):
    await hm.register("h1", "local", "both", max_concurrent=1)
    now = "2026-03-20T00:00:00+00:00"

    await db.execute("PRAGMA foreign_keys=OFF")
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,started_at) VALUES(?,?,?,?,?,?,?)",
        ("job-orphan", "missing-run", "h1", "claude", "DESIGN_RUNNING", "running", now),
    )
    await db.execute("PRAGMA foreign_keys=ON")

    hosts = await hm.list_all()
    host = await hm.select_host("claude")

    assert hosts[0]["current_load"] == 0
    assert host["id"] == "h1"

async def test_health_check_validates_claude_cli(hm, db):
    """Host with agent_type='claude' must have claude CLI available."""
    await hm.register("h-claude", "local", "claude")
    import unittest.mock
    with unittest.mock.patch("shutil.which") as mock_which:
        mock_which.side_effect = lambda cmd: "/usr/bin/codex" if cmd == "codex" else ("/usr/bin/acpx" if cmd == "acpx" else None)
        result = await hm.health_check("h-claude")
    assert result is False
    host = await db.fetchone("SELECT * FROM agent_hosts WHERE id='h-claude'")
    assert host["status"] == "offline"

async def test_health_check_validates_codex_cli(hm, db):
    """Host with agent_type='codex' must have codex CLI available."""
    await hm.register("h-codex", "local", "codex")
    import unittest.mock
    with unittest.mock.patch("shutil.which") as mock_which:
        mock_which.side_effect = lambda cmd: "/usr/bin/claude" if cmd == "claude" else ("/usr/bin/acpx" if cmd == "acpx" else None)
        result = await hm.health_check("h-codex")
    assert result is False
    host = await db.fetchone("SELECT * FROM agent_hosts WHERE id='h-codex'")
    assert host["status"] == "offline"

async def test_health_check_validates_both_cli(hm, db):
    """Host with agent_type='both' must have both CLIs available."""
    await hm.register("h-both", "local", "both")
    import unittest.mock
    with unittest.mock.patch("shutil.which") as mock_which:
        mock_which.side_effect = lambda cmd: "/usr/bin/claude" if cmd == "claude" else ("/usr/bin/acpx" if cmd == "acpx" else None)
        result = await hm.health_check("h-both")
    assert result is False

async def test_health_check_passes_when_cli_available(hm, db):
    """Host passes when required CLI is available."""
    await hm.register("h-ok", "local", "claude")
    import unittest.mock
    with unittest.mock.patch("shutil.which") as mock_which:
        mock_which.side_effect = lambda cmd: f"/usr/bin/{cmd}" if cmd in ("acpx", "claude") else None
        result = await hm.health_check("h-ok")
    assert result is True
    host = await db.fetchone("SELECT * FROM agent_hosts WHERE id='h-ok'")
    assert host["status"] == "active"

async def test_load_from_config(hm):
    config = [
        {"id": "pc1", "host": "local", "agent_type": "both", "max_concurrent": 2},
        {"id": "srv1", "host": "dev@10.0.0.5", "agent_type": "codex", "max_concurrent": 4}
    ]
    await hm.load_from_config(config)
    hosts = await hm.list_all()
    assert len(hosts) == 2
