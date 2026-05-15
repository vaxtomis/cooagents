"""Tests for src.llm_runner — Phase 2.

Covers Session/SessionLifecycleError types, every command builder,
run_oneshot delegation, the session lifecycle methods (start/prompt/status/
cancel/delete) and orphan_sweep_at_boot. Subprocess plumbing is exercised
via ``monkeypatch.setattr("asyncio.create_subprocess_exec", ...)`` mirroring
``tests/test_acpx_executor.py``.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

from src.acpx_executor import AcpxExecutor
from src.config import Settings
from src.llm_runner import (
    DESIGN_SESSION_PREFIX,
    DW_SESSION_PREFIX,
    IdleTimeoutError,
    LLMRunner,
    ProgressTick,
    Session,
    SessionLifecycleError,
    dw_session_name,
)


FIXED_CLOCK = "2026-04-28T00:00:00+00:00"


# ---- helpers -------------------------------------------------------------

class _FakeProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", rc: int = 0):
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode = rc
        self._waited = asyncio.Event()
        self._waited.set()

    async def wait(self):
        await self._waited.wait()
        return self.returncode


def _capture_subprocess(monkeypatch, *, stdout=b"", stderr=b"", rc=0):
    """Patch asyncio.create_subprocess_exec; return the captured-args dict."""
    captured = {"args": None, "kwargs": None}

    async def fake_exec(*args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return _FakeProc(stdout, stderr, rc)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    return captured


@pytest.fixture
def runner():
    executor = AcpxExecutor(db=None, webhook_notifier=None)
    return LLMRunner(executor=executor, clock=lambda: FIXED_CLOCK)


@pytest.fixture
def runner_with_config():
    s = Settings()
    s.acpx.permission_mode = "approve-all"
    s.acpx.model = "claude-opus-4"
    s.acpx.json_strict = True
    executor = AcpxExecutor(db=None, webhook_notifier=None, config=s)
    return LLMRunner(executor=executor, config=s, clock=lambda: FIXED_CLOCK)


# ---- types ---------------------------------------------------------------

def test_session_dataclass_frozen():
    s = Session(name="dw-x", anchor_cwd="/A", agent="claude", created_at=FIXED_CLOCK)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.name = "other"  # type: ignore[misc]


def test_dw_session_name_format():
    assert dw_session_name("dev-abc", 3, "build") == "dw-dev-abc-r3-build"


def test_dw_session_name_rejects_unknown_role():
    with pytest.raises(AssertionError):
        dw_session_name("dev-abc", 1, "other")


def test_session_prefix_constants():
    assert DW_SESSION_PREFIX == "dw-"
    assert DESIGN_SESSION_PREFIX == "design-"


def test_session_lifecycle_error_attrs():
    err = SessionLifecycleError("ensure", 4, "stderr tail")
    assert err.op == "ensure"
    assert err.rc == 4
    assert err.stderr_tail == "stderr tail"
    assert "rc=4" in str(err)


# ---- command builders ----------------------------------------------------

def test_build_oneshot_cmd_matches_acpx_executor(runner):
    cmd = runner._build_oneshot_cmd("claude", "/tmp/wt", 60, prompt="p")
    expected = runner._executor._build_acpx_exec_cmd(
        "claude", "/tmp/wt", 60, None, "p",
    )
    assert cmd == expected


def test_build_ensure_cmd_shape(runner):
    cmd = runner._build_ensure_cmd("dw-x-r1-plan", "/A", "claude")
    assert cmd[:6] == ["acpx", "--cwd", "/A", "--format", "json", "--approve-all"]
    assert cmd[-5:] == ["claude", "sessions", "ensure", "--name", "dw-x-r1-plan"]


def test_build_set_mode_cmd_shape(runner):
    cmd = runner._build_set_mode_cmd("dw-x-r1-review", "/A", "codex", "auto")
    assert cmd[:6] == ["acpx", "--cwd", "/A", "--format", "json", "--approve-all"]
    assert cmd[-5:] == ["codex", "set-mode", "--session", "dw-x-r1-review", "auto"]


def test_build_prompt_cmd_with_text(runner):
    s = Session(name="n", anchor_cwd="/A", agent="claude", created_at=FIXED_CLOCK)
    cmd = runner._build_prompt_cmd(s, text="hello", task_file=None, timeout_sec=30)
    assert cmd[:6] == ["acpx", "--cwd", "/A", "--format", "json", "--approve-all"]
    assert "--timeout" in cmd
    assert cmd[cmd.index("--timeout") + 1] == "30"
    assert cmd[-5:] == ["claude", "prompt", "--session", "n", "hello"]


def test_build_prompt_cmd_with_file(runner):
    s = Session(name="n", anchor_cwd="/A", agent="codex", created_at=FIXED_CLOCK)
    cmd = runner._build_prompt_cmd(s, text=None, task_file="/abs/task.md", timeout_sec=10)
    assert cmd[-6:] == ["codex", "prompt", "--session", "n", "--file", "/abs/task.md"]


def test_build_prompt_cmd_can_omit_timeout(runner):
    s = Session(name="n", anchor_cwd="/A", agent="codex", created_at=FIXED_CLOCK)
    cmd = runner._build_prompt_cmd(
        s, text=None, task_file="/abs/task.md", timeout_sec=None,
    )
    assert "--timeout" not in cmd
    assert cmd[-6:] == ["codex", "prompt", "--session", "n", "--file", "/abs/task.md"]


def test_build_prompt_cmd_rejects_both_text_and_file(runner):
    s = Session(name="n", anchor_cwd="/A", agent="claude", created_at=FIXED_CLOCK)
    with pytest.raises(AssertionError):
        runner._build_prompt_cmd(s, text="x", task_file="/a", timeout_sec=10)


def test_build_prompt_cmd_rejects_neither_text_nor_file(runner):
    s = Session(name="n", anchor_cwd="/A", agent="claude", created_at=FIXED_CLOCK)
    with pytest.raises(AssertionError):
        runner._build_prompt_cmd(s, text=None, task_file=None, timeout_sec=10)


def test_build_close_cmd_includes_anchor_cwd(runner):
    """Phase 11: close uses positional name (acpx 0.6.x), not --name."""
    s = Session(name="n", anchor_cwd="/anchor", agent="claude", created_at=FIXED_CLOCK)
    cmd = runner._build_close_cmd(s)
    assert "--cwd" in cmd
    assert cmd[cmd.index("--cwd") + 1] == "/anchor"
    assert cmd[-4:] == ["claude", "sessions", "close", "n"]
    assert "--name" not in cmd


def test_build_list_cmd_uses_format_json(runner):
    cmd = runner._build_list_cmd("claude", "/A")
    assert "--format" in cmd
    assert cmd[cmd.index("--format") + 1] == "json"
    assert cmd[-3:] == ["claude", "sessions", "list"]


def test_build_status_cmd_shape(runner):
    s = Session(name="n", anchor_cwd="/A", agent="claude", created_at=FIXED_CLOCK)
    cmd = runner._build_status_cmd(s)
    assert cmd[-4:] == ["claude", "status", "--session", "n"]


def test_build_cancel_cmd_shape(runner):
    s = Session(name="n", anchor_cwd="/A", agent="codex", created_at=FIXED_CLOCK)
    cmd = runner._build_cancel_cmd(s)
    assert cmd[-4:] == ["codex", "cancel", "--session", "n"]


def test_common_flags_includes_config_extras(runner_with_config):
    cmd = runner_with_config._common_flags("/A")
    assert "--json-strict" in cmd
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4"


# ---- run_oneshot ---------------------------------------------------------

@pytest.mark.asyncio
async def test_run_oneshot_delegates_to_executor():
    calls: list[dict] = []

    class _Exec:
        async def run_once(self, agent_type, worktree, timeout_sec,
                           task_file=None, prompt=None, *,
                           host_id="local", workspace_id=None, correlation_id=None):
            calls.append(dict(
                agent_type=agent_type, worktree=worktree, timeout_sec=timeout_sec,
                task_file=task_file, prompt=prompt, host_id=host_id,
                workspace_id=workspace_id, correlation_id=correlation_id,
            ))
            return ("hello", 0)

    runner = LLMRunner(executor=_Exec())
    out, rc = await runner.run_oneshot(
        "claude", "/tmp/wt", 30,
        task_file="/t.md",
        host_id="local", workspace_id="ws-1", correlation_id="dw-1",
    )
    assert (out, rc) == ("hello", 0)
    assert len(calls) == 1
    c = calls[0]
    assert c["agent_type"] == "claude"
    assert c["worktree"] == "/tmp/wt"
    assert c["timeout_sec"] == 30
    assert c["task_file"] == "/t.md"
    assert c["workspace_id"] == "ws-1"
    assert c["correlation_id"] == "dw-1"


# ---- session lifecycle ---------------------------------------------------

@pytest.mark.asyncio
async def test_start_session_runs_ensure_and_returns_session(monkeypatch, runner):
    captured = _capture_subprocess(monkeypatch, stdout=b"ok", rc=0)
    s = await runner.start_session(name="dw-x-r1-plan", anchor_cwd="/A", agent="claude")
    assert isinstance(s, Session)
    assert s.name == "dw-x-r1-plan"
    assert s.anchor_cwd == "/A"
    assert s.agent == "claude"
    assert s.created_at == FIXED_CLOCK
    args = captured["args"]
    assert args[0] == "acpx"
    assert "sessions" in args and "ensure" in args
    assert captured["kwargs"]["cwd"] == "/A"


@pytest.mark.asyncio
async def test_start_codex_session_sets_writable_mode(monkeypatch):
    s = Settings()
    s.acpx.session_mode = "auto"
    executor = AcpxExecutor(db=None, webhook_notifier=None, config=s)
    runner = LLMRunner(executor=executor, config=s, clock=lambda: FIXED_CLOCK)
    seen_cmds: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        seen_cmds.append(list(args))
        return _FakeProc(b"ok", b"", 0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    session = await runner.start_session(
        name="dw-x-r1-review", anchor_cwd="/A", agent="codex",
    )

    assert session.agent == "codex"
    assert len(seen_cmds) == 2
    assert seen_cmds[0][-5:] == [
        "codex", "sessions", "ensure", "--name", "dw-x-r1-review",
    ]
    assert seen_cmds[1][-5:] == [
        "codex", "set-mode", "--session", "dw-x-r1-review", "auto",
    ]


@pytest.mark.asyncio
async def test_start_codex_session_raises_when_set_mode_fails(monkeypatch):
    s = Settings()
    s.acpx.session_mode = "auto"
    executor = AcpxExecutor(db=None, webhook_notifier=None, config=s)
    runner = LLMRunner(executor=executor, config=s, clock=lambda: FIXED_CLOCK)
    call = {"n": 0}

    async def fake_exec(*args, **kwargs):
        call["n"] += 1
        if call["n"] == 1:
            return _FakeProc(b"ok", b"", 0)
        return _FakeProc(b"", b"bad mode", 2)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(SessionLifecycleError) as excinfo:
        await runner.start_session(
            name="dw-x-r1-review", anchor_cwd="/A", agent="codex",
        )

    assert excinfo.value.op == "set-mode"
    assert excinfo.value.rc == 2
    assert "bad mode" in excinfo.value.stderr_tail


@pytest.mark.asyncio
async def test_start_session_raises_lifecycle_error_on_nonzero_rc(monkeypatch, runner):
    _capture_subprocess(monkeypatch, stdout=b"", stderr=b"NO_SESSION", rc=4)
    with pytest.raises(SessionLifecycleError) as excinfo:
        await runner.start_session(name="x", anchor_cwd="/A", agent="claude")
    err = excinfo.value
    assert err.op == "ensure"
    assert err.rc == 4
    assert "NO_SESSION" in err.stderr_tail


@pytest.mark.asyncio
async def test_prompt_session_pins_anchor_cwd(monkeypatch, runner):
    captured = _capture_subprocess(monkeypatch, stdout=b"reply", rc=0)
    s = Session(name="n", anchor_cwd="/anchor-A", agent="claude", created_at=FIXED_CLOCK)
    out, rc = await runner.prompt_session(s, text="ping", timeout_sec=10)
    assert (out, rc) == ("reply", 0)
    assert captured["kwargs"]["cwd"] == "/anchor-A"


@pytest.mark.asyncio
async def test_status_session_parses_kv_body(monkeypatch, runner):
    _capture_subprocess(
        monkeypatch,
        stdout=b"session: -\nstatus: no-session\n",
        rc=0,
    )
    s = Session(name="n", anchor_cwd="/A", agent="claude", created_at=FIXED_CLOCK)
    parsed = await runner.status_session(s)
    assert parsed == {"session": "-", "status": "no-session"}


@pytest.mark.asyncio
async def test_status_session_parses_json_body(monkeypatch, runner):
    _capture_subprocess(
        monkeypatch,
        stdout=json.dumps({
            "action": "status_snapshot",
            "status": "alive",
            "summary": "queue owner healthy",
            "pid": 123,
        }).encode("utf-8"),
        rc=0,
    )
    s = Session(name="n", anchor_cwd="/A", agent="claude", created_at=FIXED_CLOCK)
    parsed = await runner.status_session(s)
    assert parsed["status"] == "alive"
    assert parsed["pid"] == 123


@pytest.mark.asyncio
async def test_status_session_returns_empty_on_nonzero_rc(monkeypatch, runner):
    _capture_subprocess(monkeypatch, stdout=b"", rc=1)
    s = Session(name="n", anchor_cwd="/A", agent="claude", created_at=FIXED_CLOCK)
    parsed = await runner.status_session(s)
    assert parsed == {}


@pytest.mark.asyncio
async def test_cancel_session_swallows_nonzero(monkeypatch, runner):
    _capture_subprocess(monkeypatch, stderr=b"already stopped", rc=1)
    s = Session(name="n", anchor_cwd="/A", agent="claude", created_at=FIXED_CLOCK)
    # Should NOT raise.
    await runner.cancel_session(s)


@pytest.mark.asyncio
async def test_delete_session_close_with_anchor_cwd(monkeypatch, runner):
    """delete_session: close must be invoked with --cwd matching anchor."""
    seen_cwds: list[str] = []
    seen_cmds: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        seen_cwds.append(kwargs.get("cwd"))
        seen_cmds.append(list(args))
        return _FakeProc(b"", b"", 0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    s = Session(name="n", anchor_cwd="/anchor", agent="claude", created_at=FIXED_CLOCK)
    await runner.delete_session(s)
    # Phase 11: cancel + close = 2 subprocess calls (no deferred prune).
    assert len(seen_cwds) == 2
    assert all(c == "/anchor" for c in seen_cwds)
    close_cmd = seen_cmds[1]
    assert "sessions" in close_cmd and "close" in close_cmd
    assert "--cwd" in close_cmd
    assert close_cmd[close_cmd.index("--cwd") + 1] == "/anchor"
    # Phase 11: positional name, not --name.
    assert close_cmd[-1] == "n"
    assert "--name" not in close_cmd


@pytest.mark.asyncio
async def test_delete_session_swallows_no_named_session_close_error(monkeypatch, runner):
    """close rc=1 with 'no named session' stderr is a no-op success."""
    call = {"n": 0}

    async def fake_exec(*args, **kwargs):
        call["n"] += 1
        # cancel returns 0; close returns 1 with no-named-session stderr.
        if call["n"] == 1:
            return _FakeProc(b"", b"", 0)
        return _FakeProc(b"", b"No named session for that name", 1)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    s = Session(name="missing", anchor_cwd="/A", agent="claude", created_at=FIXED_CLOCK)
    # Should NOT raise.
    await runner.delete_session(s)


@pytest.mark.asyncio
async def test_delete_session_raises_on_other_close_failure(monkeypatch, runner):
    call = {"n": 0}

    async def fake_exec(*args, **kwargs):
        call["n"] += 1
        if call["n"] == 1:
            return _FakeProc(b"", b"", 0)
        return _FakeProc(b"", b"boom", 1)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    s = Session(name="n", anchor_cwd="/A", agent="claude", created_at=FIXED_CLOCK)
    with pytest.raises(SessionLifecycleError) as excinfo:
        await runner.delete_session(s)
    assert excinfo.value.op == "close"


# ---- orphan_sweep_at_boot ------------------------------------------------

@pytest.mark.asyncio
async def test_orphan_sweep_filters_by_prefix(monkeypatch, runner):
    list_payload = json.dumps([
        {"name": "dw-keep", "cwd": "/anchor1", "closed": False, "createdAt": "t1"},
        {"name": "design-keep", "cwd": "/anchor2", "closed": False, "createdAt": "t2"},
        {"name": "unrelated-z", "cwd": "/anchor3", "closed": False, "createdAt": "t3"},
    ]).encode()

    call_log: list[dict] = []

    async def fake_exec(*args, **kwargs):
        call_log.append({"args": list(args), "cwd": kwargs.get("cwd")})
        argv = list(args)
        # `sessions list` returns the JSON; everything else returns rc=0/empty.
        if "list" in argv and "sessions" in argv:
            return _FakeProc(list_payload, b"", 0)
        return _FakeProc(b"", b"", 0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    cleaned = await runner.orphan_sweep_at_boot(
        name_prefixes=("dw-", "design-"),
    )
    cleaned_names = {s.name for s in cleaned}
    # Both agents iterate; sessions matching prefixes are deleted.
    # Test JSON has 2 matching entries, and we sweep once per agent (claude+codex).
    # Each agent sees the same JSON, so 2 names × 2 agents = 4 cleaned entries.
    assert "dw-keep" in cleaned_names
    assert "design-keep" in cleaned_names
    assert "unrelated-z" not in cleaned_names


@pytest.mark.asyncio
async def test_orphan_sweep_skips_closed_entries(monkeypatch, runner):
    list_payload = json.dumps([
        {"name": "dw-closed", "cwd": "/A", "closed": True, "createdAt": "t1"},
        {"name": "dw-open", "cwd": "/B", "closed": False, "createdAt": "t2"},
    ]).encode()

    deletes: list[str] = []

    async def fake_exec(*args, **kwargs):
        argv = list(args)
        if "list" in argv and "sessions" in argv:
            return _FakeProc(list_payload, b"", 0)
        if "close" in argv:
            # Phase 11: close uses positional name (last argv).
            deletes.append(argv[-1])
        return _FakeProc(b"", b"", 0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await runner.orphan_sweep_at_boot(name_prefixes=("dw-",))
    assert "dw-closed" not in deletes
    assert "dw-open" in deletes


@pytest.mark.asyncio
async def test_orphan_sweep_logs_and_continues_on_per_session_failure(monkeypatch, runner):
    list_payload = json.dumps([
        {"name": "dw-bad", "cwd": "/A", "closed": False, "createdAt": "t1"},
        {"name": "dw-good", "cwd": "/B", "closed": False, "createdAt": "t2"},
    ]).encode()

    async def fake_exec(*args, **kwargs):
        argv = list(args)
        if "list" in argv and "sessions" in argv:
            return _FakeProc(list_payload, b"", 0)
        if "close" in argv:
            # Phase 11: close uses positional name (last argv).
            if argv[-1] == "dw-bad":
                # Simulate an unexpected close failure → SessionLifecycleError.
                return _FakeProc(b"", b"unexpected boom", 1)
        return _FakeProc(b"", b"", 0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    cleaned = await runner.orphan_sweep_at_boot(name_prefixes=("dw-",))
    cleaned_names = {s.name for s in cleaned}
    assert "dw-good" in cleaned_names
    assert "dw-bad" not in cleaned_names


@pytest.mark.asyncio
async def test_orphan_sweep_handles_malformed_json(monkeypatch, runner):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(b"not json at all", b"", 0)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    cleaned = await runner.orphan_sweep_at_boot(name_prefixes=("dw-",))
    assert cleaned == []


@pytest.mark.asyncio
async def test_orphan_sweep_skips_when_list_rc_nonzero(monkeypatch, runner):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(b"", b"list failed", 1)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    cleaned = await runner.orphan_sweep_at_boot(name_prefixes=("dw-",))
    assert cleaned == []


# ---- prompt_session_with_progress (Phase 9) -----------------------------


@pytest.mark.asyncio
async def test_prompt_session_with_progress_polls_session_activity():
    """Session-mode dispatch uses session activity instead of acpx timeout."""

    class _FakeExecutor:
        def _resolve_agent(self, t):
            return t

    runner = LLMRunner(executor=_FakeExecutor(), config=None)
    session = Session(
        name="dw-x-r1-plan", anchor_cwd="/anchor",
        agent="claude", created_at=FIXED_CLOCK,
    )

    captured: dict = {}

    async def _fake_rwp(
        *, cmd, cwd, heartbeat, heartbeat_interval_s,
        idle_timeout_s, step_tag, advance_probe,
    ):
        captured["cmd"] = list(cmd)
        captured["cwd"] = cwd
        captured["step_tag"] = step_tag
        captured["heartbeat_interval_s"] = heartbeat_interval_s
        captured["idle_timeout_s"] = idle_timeout_s
        captured["advanced"] = await advance_probe()
        await heartbeat(ProgressTick(ts=FIXED_CLOCK, elapsed_s=1))
        return "ok", 0, [ProgressTick(ts=FIXED_CLOCK, elapsed_s=1)]

    runner.run_with_progress = _fake_rwp  # type: ignore[method-assign]
    activity = [
        {"name": "dw-x-r1-plan", "cwd": "/anchor", "lastSeq": 1},
        {"name": "dw-x-r1-plan", "cwd": "/anchor", "lastSeq": 2},
    ]

    async def _fake_session_record(_session):
        return activity.pop(0)

    runner._session_record = _fake_session_record  # type: ignore[method-assign]

    ticks: list[ProgressTick] = []

    async def hb(t: ProgressTick) -> None:
        ticks.append(t)

    stdout, rc, log = await runner.prompt_session_with_progress(
        session,
        task_file="/tmp/p.md",
        timeout_sec=30,
        heartbeat=hb,
        heartbeat_interval_s=0.5,
        idle_timeout_s=10.0,
        step_tag="STEP2_ITERATION",
    )

    assert (stdout, rc) == ("ok", 0)
    assert len(log) == 1
    assert len(ticks) == 1
    assert "prompt" in captured["cmd"]
    assert "--timeout" not in captured["cmd"]
    assert "--session" in captured["cmd"]
    assert "dw-x-r1-plan" in captured["cmd"]
    assert "--file" in captured["cmd"]
    assert captured["cwd"] == "/anchor"
    assert captured["step_tag"] == "STEP2_ITERATION"
    assert captured["heartbeat_interval_s"] == 0.5
    assert captured["idle_timeout_s"] == 10.0
    assert captured["advanced"] is True


# ---- run_with_progress (Phase 3) ----------------------------------------

class _SlowFakeProc:
    """Subprocess fake whose process exits after ``sleep_s``.

    Lets the heartbeat ticker fire several ticks against a deterministic
    asyncio sleep without spawning a real process.
    """

    def __init__(
        self,
        *,
        sleep_s: float,
        stdout: bytes = b"ok",
        stderr: bytes = b"",
        rc: int = 0,
    ) -> None:
        self._sleep_s = sleep_s
        self._stdout = stdout
        self._stderr = stderr
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode: int | None = None
        self._rc_on_exit = rc
        self.killed = 0
        self._done = asyncio.Event()

    async def wait(self):
        await asyncio.sleep(self._sleep_s)
        self.returncode = self._rc_on_exit
        self.stdout.feed_data(self._stdout)
        self.stdout.feed_eof()
        self.stderr.feed_data(self._stderr)
        self.stderr.feed_eof()
        self._done.set()
        return self.returncode

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._done.set()


class _NeverFinishingProc:
    """Subprocess fake whose direct child never exits until killed."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = 0
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.005)
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        return self.returncode

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9


class _PipeHolderExitedProc:
    """Direct child exits, but descendant keeps the pipe fd open.

    ``wait()`` returns quickly with rc=0, stdout already contains bytes,
    but EOF never arrives unless the runner cancels the reader task.
    """

    def __init__(self, *, stdout: bytes = b"ack\n", stderr: bytes = b"", rc: int = 0):
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stderr.feed_data(stderr)
        self.returncode: int | None = None
        self._rc_on_exit = rc

    async def wait(self):
        await asyncio.sleep(0.005)
        self.returncode = self._rc_on_exit
        return self.returncode


@pytest.mark.asyncio
async def test_run_with_progress_emits_ticks_at_interval(monkeypatch, runner):
    proc = _SlowFakeProc(sleep_s=0.06)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    ticks: list[ProgressTick] = []

    async def heartbeat(t: ProgressTick) -> None:
        ticks.append(t)

    stdout, rc, log = await runner.run_with_progress(
        cmd=["acpx", "exec"], cwd=".",
        heartbeat=heartbeat,
        heartbeat_interval_s=0.02,
        idle_timeout_s=5.0,
        step_tag="STEP4_DEVELOP",
    )
    assert (stdout, rc) == ("ok", 0)
    assert len(ticks) >= 2
    assert log == ticks  # progress_log mirrors callback invocations
    # Each tick carries a non-decreasing elapsed_s.
    assert all(
        ticks[i].elapsed_s <= ticks[i + 1].elapsed_s
        for i in range(len(ticks) - 1)
    )


@pytest.mark.asyncio
async def test_run_with_progress_refreshes_execution_lease(monkeypatch):
    proc = _SlowFakeProc(sleep_s=0.06)
    proc.pid = 123

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    class _Repo:
        def __init__(self) -> None:
            self.started: list[str] = []
            self.heartbeats: list[str] = []
            self.exited: list[str] = []

        async def mark_process_started(self, execution_id, **_kwargs):
            self.started.append(execution_id)

        async def heartbeat(self, execution_id):
            self.heartbeats.append(execution_id)

        async def mark_exited(self, execution_id, *, exit_code):
            self.exited.append(execution_id)

    repo = _Repo()
    runner = LLMRunner(
        executor=AcpxExecutor(db=None, webhook_notifier=None),
        agent_execution_repo=repo,
    )

    async def heartbeat(_t: ProgressTick) -> None:
        return

    await runner.run_with_progress(
        cmd=["acpx"], cwd=".",
        heartbeat=heartbeat,
        heartbeat_interval_s=0.02,
        idle_timeout_s=5.0,
        step_tag="STEP4_DEVELOP",
        execution_id="aex-1",
        run_token="tok",
    )
    assert repo.started == ["aex-1"]
    assert repo.heartbeats
    assert repo.exited == ["aex-1"]


@pytest.mark.asyncio
async def test_run_with_progress_returns_stdout_rc_and_log(monkeypatch, runner):
    proc = _SlowFakeProc(sleep_s=0.005, stdout=b"hello\n", rc=0)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    async def heartbeat(_t: ProgressTick) -> None:
        return

    stdout, rc, log = await runner.run_with_progress(
        cmd=["acpx"], cwd=".",
        heartbeat=heartbeat,
        heartbeat_interval_s=1.0,  # never fires before proc exits
        idle_timeout_s=5.0,
        step_tag="STEP2_ITERATION",
    )
    assert (stdout, rc) == ("hello", 0)
    assert log == []  # heartbeat never had time to fire


@pytest.mark.asyncio
async def test_run_with_progress_returns_when_child_exits_but_pipe_stays_open(
    monkeypatch, runner
):
    proc = _PipeHolderExitedProc(stdout=b"ack\n", rc=0)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    ticks: list[ProgressTick] = []

    async def heartbeat(t: ProgressTick) -> None:
        ticks.append(t)

    stdout, rc, log = await runner.run_with_progress(
        cmd=["acpx"], cwd=".",
        heartbeat=heartbeat,
        heartbeat_interval_s=0.01,
        idle_timeout_s=5.0,
        step_tag="STEP2_ITERATION",
    )
    assert (stdout, rc) == ("ack", 0)
    assert log == ticks


@pytest.mark.asyncio
async def test_run_with_progress_heartbeat_callback_failure_does_not_abort(
    monkeypatch, runner
):
    proc = _SlowFakeProc(sleep_s=0.05, stdout=b"done", rc=0)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    async def heartbeat(_t: ProgressTick) -> None:
        raise ValueError("boom")

    stdout, rc, log = await runner.run_with_progress(
        cmd=["acpx"], cwd=".",
        heartbeat=heartbeat,
        heartbeat_interval_s=0.01,
        idle_timeout_s=5.0,
        step_tag="STEP3_CONTEXT",
    )
    # Subprocess still completes normally even though every heartbeat raised.
    assert (stdout, rc) == ("done", 0)
    # Ticks were still recorded (the callback failure is logged, not fatal).
    assert len(log) >= 1


@pytest.mark.asyncio
async def test_run_with_progress_uses_injected_monotonic(monkeypatch):
    """Phase 3: tests pass a deterministic monotonic so elapsed_s is stable."""
    proc = _SlowFakeProc(sleep_s=0.04, stdout=b"", rc=0)

    async def fake_exec(*args, **kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    fake_now = [100.0]

    def fake_monotonic() -> float:
        # Advance by 7s on each call so the very first tick reports
        # elapsed_s=7 (start=100, first tick reads 107).
        fake_now[0] += 7.0
        return fake_now[0]

    runner = LLMRunner(
        executor=AcpxExecutor(db=None, webhook_notifier=None),
        clock=lambda: FIXED_CLOCK,
        monotonic=fake_monotonic,
    )

    captured: list[ProgressTick] = []

    async def heartbeat(t: ProgressTick) -> None:
        captured.append(t)

    await runner.run_with_progress(
        cmd=["acpx"], cwd=".",
        heartbeat=heartbeat,
        heartbeat_interval_s=0.01,
        idle_timeout_s=10000.0,
        step_tag="STEP4_DEVELOP",
    )
    assert captured, "expected at least one tick before proc exited"
    # First tick: start was monotonic call #1 (107), tick reads call #2 (114),
    # elapsed_s = 114 - 107 = 7.
    assert captured[0].elapsed_s == 7
    assert captured[0].ts == FIXED_CLOCK
