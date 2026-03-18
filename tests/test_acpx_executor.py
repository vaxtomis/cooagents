import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.database import Database
from src.job_manager import JobManager
from src.config import Settings


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
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
        "claude",
        "-s", "run-abc-design",
        "--format", "json",
        "--approve-all",
        "--timeout", "1800",
        "--file", "/task.md",
    ]


def test_build_prompt_cmd_codex(executor):
    cmd = executor._build_acpx_prompt_cmd("codex", "run-abc-dev", "/wt", 3600)
    assert cmd == [
        "acpx", "--cwd", "/wt",
        "codex",
        "-s", "run-abc-dev",
        "--format", "json",
        "--approve-all",
        "--timeout", "3600",
    ]


# ------------------------------------------------------------------
# Command builders — with config
# ------------------------------------------------------------------

def test_build_prompt_cmd_with_config(executor_with_config):
    """With config, prompt cmd includes --ttl, --json-strict, --model, --allowed-tools."""
    cmd = executor_with_config._build_acpx_prompt_cmd("claude", "run-abc-design", "/wt", 1800, "/task.md")
    assert "--ttl" in cmd
    assert cmd[cmd.index("--ttl") + 1] == "600"
    assert "--json-strict" in cmd
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-20250514"
    assert "--allowed-tools" in cmd
    assert cmd[cmd.index("--allowed-tools") + 1] == "fs/read_text_file"
    assert "--file" in cmd


def test_build_prompt_cmd_codex_no_allowed_tools(executor_with_config):
    """Codex (dev) with allowed_tools_dev=None should not include --allowed-tools."""
    cmd = executor_with_config._build_acpx_prompt_cmd("codex", "run-abc-dev", "/wt", 3600)
    assert "--allowed-tools" not in cmd
    assert "--ttl" in cmd
    assert "--json-strict" in cmd
    assert "--model" in cmd


# ------------------------------------------------------------------
# New command builders
# ------------------------------------------------------------------

def test_build_exec_cmd(executor):
    cmd = executor._build_acpx_exec_cmd("claude", "/wt", 60, prompt="summarize")
    assert cmd[:4] == ["acpx", "--cwd", "/wt", "claude"]
    assert "--cwd" in cmd
    assert "--approve-all" in cmd
    assert "summarize" in cmd
    assert "--file" not in cmd


def test_build_exec_cmd_with_file(executor):
    cmd = executor._build_acpx_exec_cmd("codex", "/wt", 120, task_file="/prompt.md")
    assert cmd[:4] == ["acpx", "--cwd", "/wt", "codex"]
    assert "--file" in cmd
    assert cmd[cmd.index("--file") + 1] == "/prompt.md"


def test_build_exec_cmd_with_config(executor_with_config):
    cmd = executor_with_config._build_acpx_exec_cmd("claude", "/wt", 60, prompt="check")
    assert "--json-strict" in cmd
    assert "--model" in cmd
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
    assert cmd == ["acpx", "--cwd", "/wt", "claude", "status", "-s", "run-abc-design", "--format", "json"]


def test_build_show_cmd(executor):
    cmd = executor._build_acpx_show_cmd("claude", "run-abc-design", "/wt")
    assert "sessions" in cmd
    assert "show" in cmd
    assert "--format" in cmd
    assert "json" in cmd[cmd.index("--format") + 1]


def test_build_history_cmd(executor):
    cmd = executor._build_acpx_history_cmd("codex", "run-abc-dev", "/wt", limit=50)
    assert "sessions" in cmd
    assert "history" in cmd
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
