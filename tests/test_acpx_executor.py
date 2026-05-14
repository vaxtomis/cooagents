"""Tests for the minimum AcpxExecutor (Phase 7 — post one-cut cleanup).

Phase 7 shrinks AcpxExecutor to a one-shot subprocess runner with a command
builder. The session / host / job-lifecycle tests that preceded this file
covered deleted code paths and are intentionally gone.
"""
from __future__ import annotations

import asyncio

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


def test_build_codex_exec_cmd_uses_full_access_noninteractive_mode(executor):
    cmd = executor._build_codex_exec_cmd("/tmp/worktree")
    assert cmd[:2] == ["codex", "exec"]
    assert "--json" in cmd
    assert "--skip-git-repo-check" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "danger-full-access"
    assert cmd[cmd.index("--ask-for-approval") + 1] == "never"
    assert cmd[cmd.index("--cd") + 1] == "/tmp/worktree"
    assert cmd[-1] == "-"


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
        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_data(b"done\n")
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.returncode = 0

        async def wait(self):
            return self.returncode

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
    assert captured["kwargs"]["start_new_session"] is True


@pytest.mark.asyncio
async def test_run_once_returns_nonzero_exit(monkeypatch, executor, tmp_path):
    class FakeProc:
        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_data(b"boom")
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.returncode = 7

        async def wait(self):
            return self.returncode

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    stdout, rc = await executor.run_once("codex", str(tmp_path), 10, prompt="p")
    assert rc == 7
    assert stdout == "boom"


@pytest.mark.asyncio
async def test_run_once_codex_direct_exec_feeds_prompt_on_stdin(
    monkeypatch, executor, tmp_path,
):
    captured = {"args": None, "kwargs": None, "stdin": b""}

    class FakeStdin:
        def write(self, data):
            captured["stdin"] += data

        async def drain(self):
            return None

        def close(self):
            return None

    class FakeProc:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_data(b"done")
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.returncode = 0
            self.pid = None

        async def wait(self):
            return self.returncode

    async def fake_exec(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    stdout, rc = await executor.run_once(
        "codex", str(tmp_path), 10, prompt="write the file"
    )

    assert (stdout, rc) == ("done", 0)
    assert captured["args"][:2] == ["codex", "exec"]
    sandbox_idx = captured["args"].index("--sandbox")
    approval_idx = captured["args"].index("--ask-for-approval")
    assert captured["args"][sandbox_idx + 1] == "danger-full-access"
    assert captured["args"][approval_idx + 1] == "never"
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.PIPE
    assert captured["stdin"] == b"write the file"


@pytest.mark.asyncio
async def test_run_once_returns_when_descendant_keeps_pipe_open(
    monkeypatch, executor
):
    """A detached acpx child may keep stdout open after the main rc is ready."""

    class PipeHolderProc:
        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_data(b"ack\n")
            self.returncode: int | None = None

        async def wait(self):
            await asyncio.sleep(0.005)
            self.returncode = 0
            return self.returncode

        async def communicate(self):
            await asyncio.Event().wait()

    async def fake_exec(*args, **kwargs):
        return PipeHolderProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    stdout, rc = await asyncio.wait_for(
        executor.run_once("claude", "/tmp/wt", 10, prompt="p"),
        timeout=0.5,
    )
    assert (stdout, rc) == ("ack", 0)
