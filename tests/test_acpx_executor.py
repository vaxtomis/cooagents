import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.database import Database
from src.job_manager import JobManager


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


def test_build_prompt_cmd(executor):
    cmd = executor._build_acpx_prompt_cmd("claude", "run-abc-design", "/wt", 1800, "/task.md")
    assert cmd == [
        "acpx", "claude",
        "-s", "run-abc-design",
        "--cwd", "/wt",
        "--format", "json",
        "--approve-all",
        "--timeout", "1800",
        "--file", "/task.md",
    ]


def test_build_prompt_cmd_codex(executor):
    cmd = executor._build_acpx_prompt_cmd("codex", "run-abc-dev", "/wt", 3600)
    assert cmd == [
        "acpx", "codex",
        "-s", "run-abc-dev",
        "--cwd", "/wt",
        "--format", "json",
        "--approve-all",
        "--timeout", "3600",
    ]


def test_build_ensure_cmd(executor):
    cmd = executor._build_acpx_ensure_cmd("claude", "run-abc-design", "/wt")
    assert cmd == ["acpx", "claude", "--cwd", "/wt", "sessions", "ensure", "--name", "run-abc-design"]


def test_build_cancel_cmd(executor):
    cmd = executor._build_acpx_cancel_cmd("claude", "run-abc-design", "/wt")
    assert cmd == ["acpx", "claude", "cancel", "-s", "run-abc-design", "--cwd", "/wt"]


def test_build_close_cmd(executor):
    cmd = executor._build_acpx_close_cmd("claude", "run-abc-design", "/wt")
    assert cmd == ["acpx", "claude", "--cwd", "/wt", "sessions", "close", "run-abc-design"]


def test_build_status_cmd(executor):
    cmd = executor._build_acpx_status_cmd("claude", "run-abc-design", "/wt")
    assert cmd == ["acpx", "claude", "status", "-s", "run-abc-design", "--cwd", "/wt", "--format", "json"]


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
