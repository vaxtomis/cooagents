"""Phase 11 regression: _run_local returns on child exit, not pipe EOF.

The real bug behind the server hang is uvloop waiting forever in
``proc.communicate()`` after the direct ``acpx`` child exits 0 and flushes
JSON if a detached descendant still holds the stdout/stderr pipe fd open.
These tests lock the intended behavior:

1. Parent exits quickly but grandchild holds the pipe -> return normally.
2. Direct child never exits -> timeout still fires.
3. CLI-shape seams stay aligned with acpx 0.6.x.
"""
from __future__ import annotations

import sys
import time

import pytest

from src.llm_runner import LLMRunner, Session


PIPE_HOLDER_HELPER = """
import os, sys, time
# Double-fork a grandchild that holds parent's stdout/stderr open.
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        time.sleep(9999)
    sys.exit(0)
os.wait()
print("ack", flush=True)
sys.exit(0)
"""

NEVER_EXIT_HELPER = """
import time
time.sleep(9999)
"""

HAPPY_HELPER = """
import sys
print("ok", flush=True)
sys.exit(0)
"""


def _make_runner() -> LLMRunner:
    """Minimal LLMRunner stub: only ``_run_local`` / command builders are
    exercised. No AcpxExecutor / acpx binary needed.
    """
    runner = LLMRunner.__new__(LLMRunner)
    runner._executor = None
    runner._config = None
    runner._clock = lambda: "t"
    runner._monotonic = lambda: 0.0
    return runner


def _make_session(
    name: str = "dw-test-r1-plan",
    cwd: str = "/tmp",
    agent: str = "codex",
) -> Session:
    return Session(name=name, anchor_cwd=cwd, agent=agent, created_at="t")


# ---- timeout enforcement (Bug class A) -----------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="double-fork shape is POSIX; Windows has no os.fork()",
)
async def test_run_local_returns_when_parent_exits_but_grandchild_holds_pipe(
    tmp_path,
):
    runner = _make_runner()
    stdout, stderr, rc = await runner._run_local(
        [sys.executable, "-c", PIPE_HOLDER_HELPER],
        str(tmp_path),
        timeout=10.0,
    )
    assert rc == 0
    assert stdout == "ack"
    assert stderr == ""


async def test_run_local_times_out_when_child_never_exits(tmp_path):
    runner = _make_runner()
    t0 = time.monotonic()
    with pytest.raises(TimeoutError) as excinfo:
        await runner._run_local(
            [sys.executable, "-c", NEVER_EXIT_HELPER],
            str(tmp_path),
            timeout=2.0,
        )
    elapsed = time.monotonic() - t0
    assert "timed out after 2.0s" in str(excinfo.value)
    assert elapsed < 5.0, f"timeout enforcement is too slow: {elapsed:.2f}s"


async def test_run_local_returns_normally_when_subprocess_finishes(tmp_path):
    runner = _make_runner()
    stdout, stderr, rc = await runner._run_local(
        [sys.executable, "-c", HAPPY_HELPER],
        str(tmp_path),
        timeout=10.0,
    )
    assert rc == 0
    assert stdout == "ok"
    assert stderr == ""


# ---- CLI shape regression seam (Bug class B) -----------------------------


def test_close_cmd_uses_positional_name_not_dash_dash_name():
    """acpx 0.6.x: ``sessions close [name]`` — positional, not flag.

    Regression seam against Phase 2's wrong ``--name`` shape that returned
    rc=1 with empty stderr (silent failure).
    """
    runner = _make_runner()
    cmd = runner._build_close_cmd(_make_session("dw-x-r1-plan"))
    assert cmd[-1] == "dw-x-r1-plan"
    assert "--name" not in cmd
    assert cmd[-4:] == ["codex", "sessions", "close", "dw-x-r1-plan"]


def test_no_prune_machinery_remains():
    """acpx 0.6.x: no ``sessions prune`` subcommand exists.

    Regression seam against re-introducing dead code.
    """
    runner = _make_runner()
    assert not hasattr(runner, "_build_prune_cmd")
    assert not hasattr(runner, "_deferred_prune")
    assert not hasattr(runner, "_pending_tasks")
    session = _make_session()
    builders = (
        ("_build_close_cmd", lambda b: b(session)),
        ("_build_cancel_cmd", lambda b: b(session)),
        ("_build_status_cmd", lambda b: b(session)),
        ("_build_ensure_cmd", lambda b: b("dw-x", "/tmp", "codex")),
    )
    for name, invoke in builders:
        if not hasattr(runner, name):
            continue
        cmd = invoke(getattr(runner, name))
        assert "prune" not in cmd, f"{name} leaked 'prune'"


def test_cancel_cmd_at_top_level_not_under_sessions():
    """acpx 0.6.x: ``cancel`` is at the codex top level, not under sessions."""
    runner = _make_runner()
    cmd = runner._build_cancel_cmd(_make_session("dw-x-r1-plan"))
    idx = cmd.index("cancel")
    # Token before 'cancel' must be the agent name, not 'sessions'.
    assert cmd[idx - 1] == "codex"
    assert "sessions" not in cmd[idx - 1:idx + 1]


def test_ensure_cmd_uses_dash_dash_name():
    """acpx 0.6.x: ``sessions ensure --name X`` — flag, not positional.

    Regression seam: confirms ensure differs from close in argv shape.
    """
    runner = _make_runner()
    cmd = runner._build_ensure_cmd("dw-x-r1-plan", "/tmp", "codex")
    assert "--name" in cmd
    idx = cmd.index("--name")
    assert cmd[idx + 1] == "dw-x-r1-plan"
