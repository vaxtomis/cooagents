"""Phase 8a: AcpxExecutor host_id branching."""
from __future__ import annotations

import pytest

from src.acpx_executor import AcpxExecutor


class DummyDispatcher:
    """Records the run_remote call shape."""
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run_remote(self, host_id: str, **kwargs):
        self.calls.append({"host_id": host_id, **kwargs})
        # Phase 8a: explicit NotImplementedError to force the SM into its
        # except branch. Phase 8b will replace this stub.
        raise NotImplementedError("Phase 8b")


@pytest.fixture
def executor_no_dispatcher(tmp_path):
    return AcpxExecutor(
        db=None, webhook_notifier=None, project_root=tmp_path,
    )


@pytest.fixture
def executor_with_dispatcher(tmp_path):
    return AcpxExecutor(
        db=None, webhook_notifier=None, project_root=tmp_path,
        ssh_dispatcher=DummyDispatcher(),
    )


async def test_local_host_runs_subprocess(executor_no_dispatcher, tmp_path, monkeypatch):
    """``host_id='local'`` must hit _run_local, never the dispatcher."""
    captured = {}

    async def fake_run_local(self, cmd, worktree, **_kwargs):
        captured["cmd"] = cmd
        captured["worktree"] = worktree
        return ("ok", 0)

    monkeypatch.setattr(AcpxExecutor, "_run_local", fake_run_local)
    out, rc = await executor_no_dispatcher.run_once(
        "claude", str(tmp_path), 60, prompt="hi",
    )
    assert (out, rc) == ("ok", 0)
    assert captured["worktree"] == str(tmp_path)
    assert "acpx" in captured["cmd"]


async def test_local_default_host_id_branch(executor_no_dispatcher, tmp_path, monkeypatch):
    """No host_id kwarg passed → still local branch (preserves old call sites)."""
    monkeypatch.setattr(
        AcpxExecutor, "_run_local",
        lambda self, cmd, wt, **_kwargs: _coro_return(("", 0)),
    )
    await executor_no_dispatcher.run_once("codex", str(tmp_path), 30, prompt="x")


async def _coro_return(value):
    return value


async def test_remote_without_dispatcher_raises(executor_no_dispatcher, tmp_path):
    with pytest.raises(RuntimeError, match="ssh_dispatcher"):
        await executor_no_dispatcher.run_once(
            "codex", str(tmp_path), 30, prompt="x", host_id="ah-remote",
        )


async def test_remote_phase_8a_raises_not_implemented(executor_with_dispatcher, tmp_path):
    with pytest.raises(NotImplementedError, match="Phase 8b"):
        await executor_with_dispatcher.run_once(
            "codex", str(tmp_path), 30, prompt="x",
            host_id="ah-remote",
            workspace_id="ws-1",
            correlation_id="dw-1",
        )
    call = executor_with_dispatcher.ssh_dispatcher.calls[0]
    assert call["host_id"] == "ah-remote"
    assert call["workspace_id"] == "ws-1"
    assert call["correlation_id"] == "dw-1"
    assert call["agent"] == "codex"
