import os
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.database import Database
from src.job_manager import JobManager
from src.config import Settings


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    await d.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("local", "local", "both", 4, "active", "2026-03-20T00:00:00+00:00", "2026-03-20T00:00:00+00:00"),
    )
    yield d
    await d.close()


@pytest.fixture
async def executor(db, tmp_path):
    from src.acpx_executor import AcpxExecutor
    jm = JobManager(db)
    hm = AsyncMock()
    hm.increment_load = AsyncMock()
    hm.decrement_load = AsyncMock()
    am = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()

    ae = AcpxExecutor(db, jm, hm, am, wh, coop_dir=str(tmp_path / ".coop"))
    return ae


@pytest.fixture
async def executor_with_config(db, tmp_path):
    """Executor with full config for testing config-driven flags."""
    from src.acpx_executor import AcpxExecutor
    jm = JobManager(db)
    hm = AsyncMock()
    hm.increment_load = AsyncMock()
    hm.decrement_load = AsyncMock()
    am = AsyncMock()
    wh = AsyncMock()
    wh.notify = AsyncMock()

    settings = Settings()
    settings.acpx.ttl = 600
    settings.acpx.json_strict = True
    settings.acpx.model = "claude-sonnet-4-20250514"
    settings.acpx.allowed_tools_design = "fs/read_text_file"
    settings.acpx.allowed_tools_dev = None

    ae = AcpxExecutor(db, jm, hm, am, wh, config=settings, coop_dir=str(tmp_path / ".coop"))
    return ae


# ------------------------------------------------------------------
# Command builders — no config
# ------------------------------------------------------------------

def test_build_prompt_cmd(executor):
    """Without config, prompt cmd has no --ttl/--json-strict/--model."""
    cmd = executor._build_acpx_prompt_cmd("claude", "run-abc-design", "/wt", 1800, "/task.md")
    assert cmd == [
        "acpx", "--cwd", "/wt",
        "--format", "json",
        "--approve-all",
        "--timeout", "1800",
        "claude",
        "-s", "run-abc-design",
        "--file", os.path.abspath("/task.md"),
    ]


def test_build_prompt_cmd_codex(executor):
    cmd = executor._build_acpx_prompt_cmd("codex", "run-abc-dev", "/wt", 3600)
    assert cmd == [
        "acpx", "--cwd", "/wt",
        "--format", "json",
        "--approve-all",
        "--timeout", "3600",
        "codex",
        "-s", "run-abc-dev",
    ]


def test_build_prompt_cmd_resolves_relative_task_file_to_absolute(executor, tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    task_file = repo_root / ".coop" / "runs" / "run-abc" / "TASK-design.md"
    task_file.parent.mkdir(parents=True)
    task_file.write_text("# task\n", encoding="utf-8")
    monkeypatch.chdir(repo_root)

    cmd = executor._build_acpx_prompt_cmd(
        "claude",
        "run-abc-design",
        "/wt",
        1800,
        ".coop/runs/run-abc/TASK-design.md",
    )

    assert cmd[-2:] == ["--file", str(task_file.resolve())]


# ------------------------------------------------------------------
# Command builders — with config
# ------------------------------------------------------------------

def test_build_prompt_cmd_with_config(executor_with_config):
    """With config, prompt cmd includes --ttl, --json-strict, --model, --allowed-tools before agent."""
    cmd = executor_with_config._build_acpx_prompt_cmd("claude", "run-abc-design", "/wt", 1800, "/task.md")
    agent_idx = cmd.index("claude")
    # All global options must appear before the agent subcommand
    assert cmd.index("--ttl") < agent_idx
    assert cmd[cmd.index("--ttl") + 1] == "600"
    assert cmd.index("--json-strict") < agent_idx
    assert cmd.index("--model") < agent_idx
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-20250514"
    assert cmd.index("--allowed-tools") < agent_idx
    assert cmd[cmd.index("--allowed-tools") + 1] == "fs/read_text_file"
    assert cmd.index("--format") < agent_idx
    assert cmd.index("--approve-all") < agent_idx
    assert cmd.index("--timeout") < agent_idx
    # Subcommand options must appear after the agent
    assert cmd.index("-s") > agent_idx
    assert cmd.index("--file") > agent_idx


def test_build_prompt_cmd_codex_no_allowed_tools(executor_with_config):
    """Codex (dev) with allowed_tools_dev=None should not include --allowed-tools."""
    cmd = executor_with_config._build_acpx_prompt_cmd("codex", "run-abc-dev", "/wt", 3600)
    agent_idx = cmd.index("codex")
    assert "--allowed-tools" not in cmd
    assert cmd.index("--ttl") < agent_idx
    assert cmd.index("--json-strict") < agent_idx
    assert cmd.index("--model") < agent_idx


# ------------------------------------------------------------------
# New command builders
# ------------------------------------------------------------------

def test_build_exec_cmd(executor):
    cmd = executor._build_acpx_exec_cmd("claude", "/wt", 60, prompt="summarize")
    agent_idx = cmd.index("claude")
    # Global options before agent
    assert cmd.index("--cwd") < agent_idx
    assert cmd.index("--approve-all") < agent_idx
    assert cmd.index("--format") < agent_idx
    assert cmd.index("--timeout") < agent_idx
    # exec subcommand and prompt after agent
    assert cmd.index("exec") > agent_idx
    assert "summarize" in cmd
    assert cmd.index("summarize") > agent_idx
    assert "--file" not in cmd


def test_build_exec_cmd_with_file(executor):
    cmd = executor._build_acpx_exec_cmd("codex", "/wt", 120, task_file="/prompt.md")
    agent_idx = cmd.index("codex")
    assert cmd.index("--cwd") < agent_idx
    assert cmd.index("exec") > agent_idx
    assert cmd.index("--file") > agent_idx
    assert cmd[cmd.index("--file") + 1] == os.path.abspath("/prompt.md")


def test_build_exec_cmd_resolves_relative_task_file_to_absolute(executor, tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    task_file = repo_root / ".coop" / "runs" / "run-abc" / "TASK-dev.md"
    task_file.parent.mkdir(parents=True)
    task_file.write_text("# task\n", encoding="utf-8")
    monkeypatch.chdir(repo_root)

    cmd = executor._build_acpx_exec_cmd(
        "codex",
        "/wt",
        120,
        task_file=".coop/runs/run-abc/TASK-dev.md",
    )

    assert cmd[cmd.index("--file") + 1] == str(task_file.resolve())


def test_build_exec_cmd_with_config(executor_with_config):
    cmd = executor_with_config._build_acpx_exec_cmd("claude", "/wt", 60, prompt="check")
    agent_idx = cmd.index("claude")
    assert cmd.index("--json-strict") < agent_idx
    assert cmd.index("--model") < agent_idx
    # exec mode should NOT include --ttl (no session to keep alive)
    assert "--ttl" not in cmd


def test_build_ensure_cmd(executor):
    cmd = executor._build_acpx_ensure_cmd("claude", "run-abc-design", "/wt")
    assert cmd == ["acpx", "--cwd", "/wt", "claude", "sessions", "ensure", "--name", "run-abc-design"]


def test_build_cancel_cmd(executor):
    cmd = executor._build_acpx_cancel_cmd("claude", "run-abc-design", "/wt")
    assert cmd == ["acpx", "--cwd", "/wt", "claude", "cancel", "-s", "run-abc-design"]


def test_build_close_cmd(executor):
    cmd = executor._build_acpx_close_cmd("claude", "run-abc-design", "/wt")
    assert cmd == ["acpx", "--cwd", "/wt", "claude", "sessions", "close", "run-abc-design"]


def test_build_status_cmd(executor):
    cmd = executor._build_acpx_status_cmd("claude", "run-abc-design", "/wt")
    assert cmd == ["acpx", "--cwd", "/wt", "--format", "json", "claude", "status", "-s", "run-abc-design"]


def test_build_show_cmd(executor):
    cmd = executor._build_acpx_show_cmd("claude", "run-abc-design", "/wt")
    agent_idx = cmd.index("claude")
    assert cmd.index("--format") < agent_idx
    assert cmd[cmd.index("--format") + 1] == "json"
    assert cmd.index("sessions") > agent_idx
    assert cmd.index("show") > agent_idx


def test_build_history_cmd(executor):
    cmd = executor._build_acpx_history_cmd("codex", "run-abc-dev", "/wt", limit=50)
    agent_idx = cmd.index("codex")
    assert cmd.index("--format") < agent_idx
    assert cmd.index("sessions") > agent_idx
    assert cmd.index("history") > agent_idx
    assert "--limit" in cmd
    assert cmd[cmd.index("--limit") + 1] == "50"


def test_build_set_mode_cmd(executor):
    cmd = executor._build_acpx_set_mode_cmd("codex", "run-abc-dev", "/wt", "plan")
    assert cmd == ["acpx", "--cwd", "/wt", "codex", "set-mode", "plan", "-s", "run-abc-dev"]


def test_build_set_cmd(executor):
    cmd = executor._build_acpx_set_cmd("codex", "run-abc-dev", "/wt", "reasoning_effort", "high")
    assert cmd == ["acpx", "--cwd", "/wt", "codex", "set", "reasoning_effort", "high", "-s", "run-abc-dev"]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def test_session_name_generation(executor):
    name = executor._make_session_name("run-abc123", "design")
    assert name == "run-abc123-design"
    name2 = executor._make_session_name("run-abc123", "dev", revision=2)
    assert name2 == "run-abc123-dev-r2"


def test_exit_code_mapping(executor):
    assert executor._map_exit_code(0) == "completed"
    assert executor._map_exit_code(1) == "failed"
    assert executor._map_exit_code(2) == "failed"
    assert executor._map_exit_code(3) == "timeout"
    assert executor._map_exit_code(4) == "failed"
    assert executor._map_exit_code(5) == "failed"
    assert executor._map_exit_code(130) == "interrupted"
    assert executor._map_exit_code(99) == "failed"


def test_resolve_agent(executor):
    assert executor._resolve_agent("claude") == "claude"
    assert executor._resolve_agent("codex") == "codex"
    assert executor._resolve_agent("other") == "codex"


def test_allowed_tools_routing(executor_with_config):
    assert executor_with_config._get_allowed_tools("claude") == "fs/read_text_file"
    assert executor_with_config._get_allowed_tools("codex") is None


def test_allowed_tools_no_config(executor):
    assert executor._get_allowed_tools("claude") is None
    assert executor._get_allowed_tools("codex") is None


# ------------------------------------------------------------------
# SSH routing (cancel / close)
# ------------------------------------------------------------------

async def test_cancel_session_routes_ssh(executor, db):
    """cancel_session should use _route_cmd (SSH-aware) instead of local-only."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    # Register an SSH host and create a job
    await db.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("ssh-host", "dev@10.0.0.5", "claude", 4, "active", now, now),
    )
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-ssh-1", "T-1", "/repo", "running", "DESIGN_RUNNING", now, now),
    )
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("job-ssh-1", "run-ssh-1", "ssh-host", "claude", "DESIGN_RUNNING", "running", "/t.md", "/wt", "run-ssh-1-design", now),
    )

    with patch.object(executor, "_route_cmd", new_callable=AsyncMock, return_value=("", "", 0)) as mock_route:
        await executor.cancel_session("run-ssh-1", "claude")
        mock_route.assert_called_once()
        # Verify it passed the SSH host_id
        assert mock_route.call_args[0][0] == "ssh-host"


async def test_close_session_routes_ssh(executor, db):
    """close_session should use _route_cmd (SSH-aware) instead of local-only."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("ssh-host-2", "dev@10.0.0.5", "codex", 4, "active", now, now),
    )
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-ssh-2", "T-2", "/repo", "running", "DEV_RUNNING", now, now),
    )
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("job-ssh-2", "run-ssh-2", "ssh-host-2", "codex", "DEV_RUNNING", "running", "/t.md", "/wt", "run-ssh-2-dev", now),
    )

    with patch.object(executor, "_route_cmd", new_callable=AsyncMock, return_value=("", "", 0)) as mock_route:
        await executor.close_session("run-ssh-2", "codex")
        mock_route.assert_called_once()
        assert mock_route.call_args[0][0] == "ssh-host-2"


@pytest.mark.parametrize(
    ("current_stage", "agent_type", "expected_stage", "session_name"),
    [
        ("DESIGN_QUEUED", "claude", "DESIGN_DISPATCHED", "run-stage-design"),
        ("DEV_QUEUED", "codex", "DEV_DISPATCHED", "run-stage-dev"),
    ],
)
async def test_start_session_records_dispatched_stage_for_queued_runs(
    executor, db, current_stage, agent_type, expected_stage, session_name
):
    """Newly created jobs should be recorded at dispatched stage, not remain on queued stage."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-stage", "T-STAGE", "/repo", "running", current_stage, now, now),
    )

    host = {"id": "local", "host": "local"}
    executor._run_cmd = AsyncMock(return_value=("", "", 0))
    executor._start_local = AsyncMock(return_value=MagicMock())
    executor._emit_event = AsyncMock()
    executor._watch = AsyncMock(return_value=None)

    with patch("src.git_utils.get_head_commit", new_callable=AsyncMock, return_value="abc123"):
        job_id = await executor.start_session("run-stage", host, agent_type, "/task.md", "/wt", 120)

    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))

    assert job["stage"] == expected_stage
    assert job["status"] == "running"
    assert job["session_name"] == session_name
    assert job["timeout_sec"] == 120
    assert job["running_started_at"] is not None


@pytest.mark.parametrize(
    "current_stage,agent_type,expected_stage",
    [
        ("DESIGN_QUEUED", "claude", "DESIGN_DISPATCHED"),
        ("DEV_QUEUED", "codex", "DEV_DISPATCHED"),
    ],
)
async def test_start_session_notifies_state_machine_when_job_enters_running(
    executor, db, current_stage, agent_type, expected_stage
):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-running-event", "T-RUNNING", "/repo", "running", current_stage, now, now),
    )

    host = {"id": "local", "host": "local"}
    executor._run_cmd = AsyncMock(return_value=("", "", 0))
    executor._start_local = AsyncMock(return_value=MagicMock())
    executor._emit_event = AsyncMock()
    executor._watch = AsyncMock(return_value=None)
    state_machine = AsyncMock()
    executor.set_state_machine(state_machine)

    with patch("src.git_utils.get_head_commit", new_callable=AsyncMock, return_value="abc123"):
        job_id = await executor.start_session("run-running-event", host, agent_type, "/task.md", "/wt", 120)

    state_machine.on_job_status_changed.assert_awaited_once_with("run-running-event", job_id, "running")

    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert job["stage"] == expected_stage
    assert job["status"] == "running"


async def test_start_session_times_out_stuck_ensure(executor, db):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-stuck-ensure", "T-STUCK", "/repo", "running", "DESIGN_QUEUED", now, now),
    )

    class FakeConfig:
        class timeouts:
            dispatch_ensure = 0.01

    executor.config = FakeConfig()

    async def _slow_run_cmd(cmd, cwd):
        await asyncio.sleep(0.05)
        return "", "", 0

    host = {"id": "local", "host": "local"}
    executor._run_cmd = AsyncMock(side_effect=_slow_run_cmd)
    executor._start_local = AsyncMock()
    executor._emit_event = AsyncMock()

    with patch("src.git_utils.get_head_commit", new_callable=AsyncMock, return_value="abc123"):
        with pytest.raises(asyncio.TimeoutError):
            await executor.start_session("run-stuck-ensure", host, "claude", "/task.md", "/wt", 120)

    job = await db.fetchone("SELECT * FROM jobs WHERE run_id=?", ("run-stuck-ensure",))

    assert job["status"] == "timeout"
    assert job["ended_at"] is not None
    executor._start_local.assert_not_called()


# ------------------------------------------------------------------
# Resource cleanup
# ------------------------------------------------------------------

def test_cleanup_resources_closes_handles(executor):
    """_cleanup_resources should close stderr file handles and SSH connections."""
    mock_fh = MagicMock()
    mock_conn = MagicMock()
    executor._resources["job-1"] = {"stderr_fh": mock_fh, "ssh_conn": mock_conn}

    executor._cleanup_resources("job-1")

    mock_fh.close.assert_called_once()
    mock_conn.close.assert_called_once()
    assert "job-1" not in executor._resources


def test_cleanup_resources_noop_when_empty(executor):
    """_cleanup_resources should not raise when no resources exist."""
    executor._cleanup_resources("nonexistent-job")
    assert len(executor._resources) == 0


# ------------------------------------------------------------------
# Startup reconciliation
# ------------------------------------------------------------------

async def test_restore_on_startup_ticks_run_after_reconciling_dead_session(executor, db):
    """restore_on_startup should tick the run after marking a stale job interrupted."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-startup-1", "T-1", "/repo", "running", "DESIGN_DISPATCHED", now, now),
    )
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("job-startup-1", "run-startup-1", None, "claude", "DESIGN_DISPATCHED", "running", "/t.md", "/wt", "run-startup-1-design", now),
    )

    executor.get_session_status = AsyncMock(return_value={"status": "dead"})
    state_machine = AsyncMock()
    state_machine.on_job_status_changed = AsyncMock()
    executor.set_state_machine(state_machine)

    await executor.restore_on_startup()

    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("job-startup-1",))
    assert job["status"] == "interrupted"
    assert job["ended_at"] is not None
    state_machine.on_job_status_changed.assert_awaited_once_with(
        "run-startup-1",
        "job-startup-1",
        "interrupted",
    )


async def test_restore_on_startup_prefers_end_turn_events_over_interrupted(executor, db, tmp_path):
    """restore_on_startup should keep completed work completed when events already contain end_turn."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    events_path = tmp_path / ".coop" / "jobs" / "job-startup-endturn" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text('{"result":{"stopReason":"end_turn"}}\n', encoding="utf-8")

    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-startup-endturn", "T-2", "/repo", "running", "DESIGN_RUNNING", now, now),
    )
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,events_file,started_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "job-startup-endturn",
            "run-startup-endturn",
            None,
            "claude",
            "DESIGN_RUNNING",
            "running",
            "/t.md",
            "/wt",
            "run-startup-endturn-design",
            str(events_path),
            now,
        ),
    )

    executor.get_session_status = AsyncMock(return_value={"status": "dead"})
    state_machine = AsyncMock()
    state_machine.on_job_status_changed = AsyncMock()
    executor.set_state_machine(state_machine)

    await executor.restore_on_startup()

    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("job-startup-endturn",))
    assert job["status"] == "completed"
    assert job["ended_at"] is not None
    state_machine.on_job_status_changed.assert_awaited_once_with(
        "run-startup-endturn",
        "job-startup-endturn",
        "completed",
    )


async def test_restore_on_startup_reconciles_orphan_job_without_ticking_missing_run(executor, db):
    """restore_on_startup should terminate orphan jobs without ticking a missing run."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    await db.execute("PRAGMA foreign_keys=OFF")
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("job-orphan-startup", "missing-run", None, "claude", "DESIGN_DISPATCHED", "running", "/t.md", "/wt", "missing-run-design", now),
    )
    await db.execute("PRAGMA foreign_keys=ON")

    executor.get_session_status = AsyncMock(return_value=None)
    state_machine = AsyncMock()
    state_machine.on_job_status_changed = AsyncMock()
    executor.set_state_machine(state_machine)

    await executor.restore_on_startup()

    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("job-orphan-startup",))
    assert job["status"] == "interrupted"
    assert job["ended_at"] is not None
    state_machine.on_job_status_changed.assert_not_called()


class _FakeStdout:
    def __init__(self, lines):
        self._lines = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration


class _FakeProcess:
    def __init__(self, lines, returncode):
        self.stdout = _FakeStdout(lines)
        self.returncode = returncode

    async def wait(self):
        return self.returncode


async def test_watch_prefers_end_turn_events_over_interrupted_exit_code(executor, db):
    """_watch should mark the job completed when end_turn was already observed."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-watch-endturn", "T-3", "/repo", "running", "DESIGN_RUNNING", now, now),
    )
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "job-watch-endturn",
            "run-watch-endturn",
            "local",
            "claude",
            "DESIGN_RUNNING",
            "running",
            "/t.md",
            "/wt",
            "run-watch-endturn-design",
            now,
        ),
    )

    state_machine = AsyncMock()
    state_machine.on_job_status_changed = AsyncMock()
    executor.set_state_machine(state_machine)

    process = _FakeProcess([b'{"result":{"stopReason":"end_turn"}}\n'], 130)
    await executor._watch("job-watch-endturn", process, "run-watch-endturn", "local", "run-watch-endturn-design")

    job = await db.fetchone("SELECT * FROM jobs WHERE id=?", ("job-watch-endturn",))
    events = await db.fetchall("SELECT event_type FROM events WHERE run_id=? ORDER BY id", ("run-watch-endturn",))
    event_types = [row["event_type"] for row in events]

    assert job["status"] == "completed"
    assert job["events_file"] is not None
    assert "job.completed" in event_types
    assert "job.interrupted" not in event_types
    state_machine.on_job_status_changed.assert_awaited_once_with(
        "run-watch-endturn",
        "job-watch-endturn",
        "completed",
    )


@pytest.mark.parametrize(
    "returncode,expected_status",
    [
        (0, "completed"),
        (1, "failed"),
        (3, "timeout"),
        (130, "interrupted"),
    ],
)
async def test_watch_notifies_state_machine_about_terminal_job_status(executor, db, returncode, expected_status):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("run-watch-terminal", "T-TERM", "/repo", "running", "DESIGN_RUNNING", now, now),
    )
    await db.execute(
        "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,session_name,started_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "job-watch-terminal",
            "run-watch-terminal",
            "local",
            "claude",
            "DESIGN_RUNNING",
            "running",
            "/t.md",
            "/wt",
            "run-watch-terminal-design",
            now,
        ),
    )

    state_machine = AsyncMock()
    executor.set_state_machine(state_machine)

    process = _FakeProcess([], returncode)
    await executor._watch("job-watch-terminal", process, "run-watch-terminal", "local", "run-watch-terminal-design")

    state_machine.on_job_status_changed.assert_awaited_once_with(
        "run-watch-terminal",
        "job-watch-terminal",
        expected_status,
    )
