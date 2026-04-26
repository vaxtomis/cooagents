"""Unit tests for RepoFetcher (Phase 2, repo-registry).

The fetcher is pure I/O — no DB writes — so these tests run without a
Database fixture. The loop tests cover the registry-write contract.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from src.repos.fetcher import RepoFetcher


def _repo(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "repo-aaa",
        "name": "frontend",
        "url": "git@github.com:org/frontend.git",
        "ssh_key_path": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def env(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []

    async def fake_run_git(*args, cwd=None, check=True, env=None, timeout=None):
        calls.append(
            {"args": list(args), "cwd": cwd, "env": env, "timeout": timeout},
        )
        return ("", "", 0)

    monkeypatch.setattr("src.repos.fetcher.run_git", fake_run_git)
    fetcher = RepoFetcher(
        workspaces_root=tmp_path,
        strict_host_key=True,
        known_hosts_path=str(tmp_path / "known_hosts"),
        timeout_s=30,
    )
    return {"fetcher": fetcher, "calls": calls, "tmp": tmp_path}


async def test_clone_runs_git_clone_bare(env):
    bare = env["fetcher"].bare_path("repo-aaa")
    assert not bare.exists()
    outcome = await env["fetcher"].fetch_or_clone(_repo())
    assert outcome == "cloned"
    args = env["calls"][0]["args"]
    assert args[:2] == ["clone", "--bare"]
    assert args[2] == "git@github.com:org/frontend.git"
    assert args[3] == str(bare)


async def test_fetch_when_bare_exists(env):
    bare = env["fetcher"].bare_path("repo-aaa")
    bare.mkdir(parents=True)
    outcome = await env["fetcher"].fetch_or_clone(_repo())
    assert outcome == "fetched"
    args = env["calls"][0]["args"]
    assert args[:2] == ["--git-dir", str(bare)]
    assert "fetch" in args
    assert "--prune" in args


async def test_env_omits_git_ssh_command_for_public_repo(env):
    await env["fetcher"].fetch_or_clone(_repo())
    call_env = env["calls"][0]["env"]
    assert "GIT_SSH_COMMAND" not in call_env
    # Parent env preserved (PATH inherited so git itself is reachable).
    assert call_env.get("PATH") == os.environ.get("PATH")


async def test_env_injects_git_ssh_command_for_private_repo(env, tmp_path):
    key = tmp_path / "id_rsa"
    key.write_text("FAKE")
    await env["fetcher"].fetch_or_clone(
        _repo(ssh_key_path=str(key.resolve())),
    )
    cmd = env["calls"][0]["env"]["GIT_SSH_COMMAND"]
    assert cmd.startswith("ssh ")
    assert "IdentitiesOnly=yes" in cmd
    assert "BatchMode=yes" in cmd
    assert "StrictHostKeyChecking=yes" in cmd
    assert str(key) in cmd


async def test_clone_failure_propagates(env, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("git clone exploded: Repository not found")

    monkeypatch.setattr("src.repos.fetcher.run_git", boom)
    with pytest.raises(RuntimeError, match="Repository not found"):
        await env["fetcher"].fetch_or_clone(_repo())


async def test_strict_off_uses_accept_new(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []

    async def fake_run_git(*args, cwd=None, check=True, env=None, timeout=None):
        calls.append({"args": list(args), "env": env, "timeout": timeout})
        return ("", "", 0)

    monkeypatch.setattr("src.repos.fetcher.run_git", fake_run_git)
    fetcher = RepoFetcher(
        workspaces_root=tmp_path, strict_host_key=False,
    )
    key = tmp_path / "id_rsa"
    key.write_text("FAKE")
    await fetcher.fetch_or_clone(
        _repo(ssh_key_path=str(key.resolve())),
    )
    cmd = calls[0]["env"]["GIT_SSH_COMMAND"]
    assert "StrictHostKeyChecking=accept-new" in cmd


def test_bare_path_layout(tmp_path):
    f = RepoFetcher(workspaces_root=tmp_path)
    assert f.bare_path("repo-xyz") == (
        tmp_path / ".coop" / "registry" / "repos" / "repo-xyz.git"
    )


async def test_timeout_passed_to_run_git(env):
    await env["fetcher"].fetch_or_clone(_repo())
    assert env["calls"][0]["timeout"] == 30


async def test_timeout_surfaces_as_runtime_error(tmp_path, monkeypatch):
    async def slow_run_git(*args, cwd=None, check=True, env=None, timeout=None):
        raise asyncio.TimeoutError()

    monkeypatch.setattr("src.repos.fetcher.run_git", slow_run_git)
    fetcher = RepoFetcher(workspaces_root=tmp_path, timeout_s=5)
    with pytest.raises(RuntimeError, match="5s timeout"):
        await fetcher.fetch_or_clone(_repo())
