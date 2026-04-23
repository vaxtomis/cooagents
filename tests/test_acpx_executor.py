"""Tests for the minimum AcpxExecutor (Phase 7 — post one-cut cleanup).

Phase 7 shrinks AcpxExecutor to a one-shot subprocess runner with a command
builder. The session / host / job-lifecycle tests that preceded this file
covered deleted code paths and are intentionally gone.
"""
from __future__ import annotations

import pytest

from src.acpx_executor import AcpxExecutor
from src.config import Settings


@pytest.fixture
def executor():
    return AcpxExecutor(db=None, webhook_notifier=None)


@pytest.fixture
def executor_with_config():
    settings = Settings()
    settings.acpx.permission_mode = "approve-all"
    settings.acpx.model = "claude-opus-4"
    settings.acpx.json_strict = True
    return AcpxExecutor(db=None, webhook_notifier=None, config=settings)


def test_constructor_minimum_surface():
    """The executor must not expose any legacy session / job-lifecycle API."""
    exe = AcpxExecutor(db=None, webhook_notifier=None)
    assert hasattr(exe, "run_once")
    assert hasattr(exe, "_build_acpx_exec_cmd")
    for attr in (
        "set_state_machine",
        "start_session",
        "send_followup",
        "close_session",
        "cancel_session",
        "get_session_status",
        "restore_on_startup",
        "recover",
        "_notify_job_status_changed",
    ):
        assert not hasattr(exe, attr), f"legacy attr {attr} must be gone"


def test_resolve_agent(executor):
    assert executor._resolve_agent("claude") == "claude"
    assert executor._resolve_agent("codex") == "codex"
    assert executor._resolve_agent("anything-else") == "codex"


def test_permission_flag_defaults_without_config(executor):
    assert executor._permission_flag() == "--approve-all"


def test_permission_flag_honours_config():
    s = Settings()
    s.acpx.permission_mode = "approve-reads"
    exe = AcpxExecutor(db=None, webhook_notifier=None, config=s)
    assert exe._permission_flag() == "--approve-reads"


def test_build_exec_cmd_with_file(executor, tmp_path):
    task = tmp_path / "task.md"
    task.write_text("hi")
    cmd = executor._build_acpx_exec_cmd("claude", "/tmp/worktree", 60, task_file=str(task))
    assert cmd[:5] == ["acpx", "--cwd", "/tmp/worktree", "--format", "json"]
    assert "--approve-all" in cmd
    assert "--timeout" in cmd and "60" in cmd
    assert "--file" in cmd
    file_idx = cmd.index("--file")
    # Task file is normalised to absolute
    import os
    assert cmd[file_idx + 1] == os.path.abspath(str(task))


def test_build_exec_cmd_with_prompt(executor):
    cmd = executor._build_acpx_exec_cmd("codex", "/tmp/worktree", 30, prompt="hello world")
    # Agent + subcommand + prompt positional
    assert cmd[-3:] == ["codex", "exec", "hello world"]


def test_build_exec_cmd_with_config(executor_with_config):
    cmd = executor_with_config._build_acpx_exec_cmd("claude", "/tmp/worktree", 60)
    assert "--json-strict" in cmd
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "claude-opus-4"


def test_build_exec_cmd_defaults_to_codex_for_unknown_agent(executor):
    cmd = executor._build_acpx_exec_cmd("unknown", "/wt", 10, prompt="p")
    assert "codex" in cmd
    assert "claude" not in cmd


@pytest.mark.asyncio
async def test_run_once_spawns_subprocess(monkeypatch, executor):
    """run_once wires _build_acpx_exec_cmd into asyncio.create_subprocess_exec."""
    captured = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"done\n", b""

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    stdout, rc = await executor.run_once("claude", "/tmp/wt", 10, prompt="p")
    assert stdout == "done"
    assert rc == 0
    assert captured["args"][0] == "acpx"
    assert captured["kwargs"]["cwd"] == "/tmp/wt"


@pytest.mark.asyncio
async def test_run_once_returns_nonzero_exit(monkeypatch, executor):
    class FakeProc:
        returncode = 7

        async def communicate(self):
            return b"boom", b""

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    stdout, rc = await executor.run_once("codex", "/tmp/wt", 10, prompt="p")
    assert rc == 7
    assert stdout == "boom"
