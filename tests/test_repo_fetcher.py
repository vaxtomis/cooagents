"""Unit tests for RepoFetcher (Phase 2, repo-registry).

The fetcher is pure I/O — no DB writes — so these tests run without a
Database fixture. The loop tests cover the registry-write contract.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Any

import pytest

from src.repos.fetcher import RepoFetcher


def _git(cwd, *args: str) -> str:
    proc_env = dict(os.environ)
    proc_env.setdefault("GIT_AUTHOR_NAME", "Test")
    proc_env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    proc_env.setdefault("GIT_COMMITTER_NAME", "Test")
    proc_env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=proc_env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


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
    assert "+refs/heads/*:refs/heads/*" in args


async def test_fetch_updates_heads_with_explicit_refspec(tmp_path):
    origin_src = tmp_path / "origin-src"
    origin_src.mkdir()
    _git(origin_src, "-c", "init.defaultBranch=main", "init")
    _git(origin_src, "config", "user.email", "test@example.com")
    _git(origin_src, "config", "user.name", "Test")
    (origin_src / "README.md").write_text("one\n", encoding="utf-8")
    _git(origin_src, "add", "README.md")
    _git(origin_src, "commit", "-m", "init")
    initial_sha = _git(origin_src, "rev-parse", "refs/heads/main")
    _git(origin_src, "branch", "stale")

    fetcher = RepoFetcher(workspaces_root=tmp_path, timeout_s=30)
    bare = fetcher.bare_path("repo-aaa")
    bare.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--bare", str(origin_src), str(bare)],
        check=True,
        capture_output=True,
    )

    (origin_src / "README.md").write_text("two\n", encoding="utf-8")
    _git(origin_src, "add", "README.md")
    _git(origin_src, "commit", "-m", "second")
    second_sha = _git(origin_src, "rev-parse", "refs/heads/main")
    _git(origin_src, "checkout", "-b", "feature/x")
    (origin_src / "FEATURE.md").write_text("feature\n", encoding="utf-8")
    _git(origin_src, "add", "FEATURE.md")
    _git(origin_src, "commit", "-m", "feature")
    feature_sha = _git(origin_src, "rev-parse", "refs/heads/feature/x")
    _git(origin_src, "checkout", "main")
    _git(origin_src, "branch", "-D", "stale")

    outcome = await fetcher.fetch_or_clone(_repo(url=str(origin_src)))
    assert outcome == "fetched"
    assert (
        _git(tmp_path, "--git-dir", str(bare), "rev-parse", "refs/heads/main")
        == second_sha
    )
    assert (
        _git(
            tmp_path,
            "--git-dir",
            str(bare),
            "rev-parse",
            "refs/heads/feature/x",
        )
        == feature_sha
    )
    stale = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "--verify", "refs/heads/stale"],
        check=False,
        capture_output=True,
    )
    assert stale.returncode != 0

    _git(origin_src, "reset", "--hard", initial_sha)
    await fetcher.fetch_or_clone(_repo(url=str(origin_src)))
    assert (
        _git(tmp_path, "--git-dir", str(bare), "rev-parse", "refs/heads/main")
        == initial_sha
    )


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
