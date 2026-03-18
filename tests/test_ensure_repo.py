"""Tests for git_utils.ensure_repo()."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.git_utils import ensure_repo


async def _git(*args, cwd) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def _make_repo(path: Path) -> None:
    await _git("init", cwd=path)
    await _git("config", "user.email", "test@example.com", cwd=path)
    await _git("config", "user.name", "Test User", cwd=path)
    await _git("checkout", "-b", "main", cwd=path)
    (path / "README.md").write_text("# init\n")
    await _git("add", "README.md", cwd=path)
    await _git("commit", "-m", "init", cwd=path)


async def test_ensure_repo_existing_git_repo(tmp_path):
    """Existing git repo returns 'exists'."""
    repo = tmp_path / "repo"
    repo.mkdir()
    await _make_repo(repo)
    result = await ensure_repo(str(repo))
    assert result == "exists"


async def test_ensure_repo_existing_non_git_dir(tmp_path):
    """Existing non-git directory raises ValueError."""
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    with pytest.raises(ValueError, match="not a git repository"):
        await ensure_repo(str(plain_dir))


async def test_ensure_repo_init_new(tmp_path):
    """Non-existent path without URL does git init."""
    repo = tmp_path / "new-repo"
    result = await ensure_repo(str(repo))
    assert result == "initialized"
    assert (repo / ".git").is_dir()


async def test_ensure_repo_clone(tmp_path):
    """Non-existent path with URL does git clone."""
    source = tmp_path / "source"
    source.mkdir()
    await _make_repo(source)
    target = tmp_path / "cloned"
    result = await ensure_repo(str(target), repo_url=str(source))
    assert result == "cloned"
    assert (target / ".git").is_dir()
    assert (target / "README.md").is_file()


async def test_ensure_repo_clone_failure(tmp_path):
    """Clone from invalid URL raises RuntimeError."""
    target = tmp_path / "bad-clone"
    with pytest.raises(RuntimeError, match="clone.*failed"):
        await ensure_repo(str(target), repo_url="https://invalid.example.com/no-repo.git")
