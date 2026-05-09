"""Tests for src/git_utils.py using real temporary git repositories."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.git_utils import ensure_worktree


async def _git(*args, cwd) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {stderr.decode().strip()}"
        )
    return stdout.decode().strip()


async def _make_repo(path: Path) -> None:
    await _git("init", cwd=path)
    await _git("config", "user.email", "test@example.com", cwd=path)
    await _git("config", "user.name", "Test User", cwd=path)
    await _git("checkout", "-b", "main", cwd=path)
    (path / "README.md").write_text("# initial\n")
    await _git("add", "README.md", cwd=path)
    await _git("commit", "-m", "init", cwd=path)


@pytest.fixture()
async def repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    await _make_repo(repo_dir)
    return repo_dir


def _wt(tmp_path: Path, name: str) -> str:
    return str(tmp_path / ".worktrees" / name)


async def test_ensure_worktree_creates_new(repo: Path, tmp_path: Path) -> None:
    wt_path_in = _wt(tmp_path, "feat-T-1-design")
    branch, wt_path = await ensure_worktree(
        str(repo), "feat/T-1-design", wt_path_in
    )

    assert branch == "feat/T-1-design"
    assert wt_path == wt_path_in
    assert Path(wt_path).is_dir()

    branches_out = await _git("branch", cwd=repo)
    assert "feat/T-1-design" in branches_out


async def test_ensure_worktree_creates_branch_from_start_point(
    repo: Path, tmp_path: Path
) -> None:
    await _git("checkout", "-b", "release/2026.05", cwd=repo)
    (repo / "release.txt").write_text("release\n", encoding="utf-8")
    await _git("add", "release.txt", cwd=repo)
    await _git("commit", "-m", "release commit", cwd=repo)
    release_sha = await _git("rev-parse", "HEAD", cwd=repo)

    await _git("checkout", "main", cwd=repo)
    main_sha = await _git("rev-parse", "HEAD", cwd=repo)
    assert release_sha != main_sha

    wt_path_in = _wt(tmp_path, "devwork-from-release")
    branch, wt = await ensure_worktree(
        str(repo),
        "devwork/demo-from-release",
        wt_path_in,
        start_point="release/2026.05",
    )

    assert branch == "devwork/demo-from-release"
    assert await _git("rev-parse", "HEAD", cwd=wt) == release_sha
    assert (Path(wt) / "release.txt").read_text(encoding="utf-8") == "release\n"


async def test_ensure_worktree_rejects_existing_branch_not_from_start_point(
    repo: Path, tmp_path: Path
) -> None:
    main_sha = await _git("rev-parse", "main", cwd=repo)
    await _git("checkout", "--orphan", "devwork/bad-base", cwd=repo)
    await _git("rm", "-rf", ".", cwd=repo)
    (repo / "orphan.txt").write_text("orphan\n", encoding="utf-8")
    await _git("add", "orphan.txt", cwd=repo)
    await _git("commit", "-m", "orphan commit", cwd=repo)
    await _git("checkout", "main", cwd=repo)

    with pytest.raises(RuntimeError, match="not based on start_point"):
        await ensure_worktree(
            str(repo),
            "devwork/bad-base",
            _wt(tmp_path, "bad-base"),
            start_point=main_sha,
        )


async def test_ensure_worktree_reuses_existing(
    repo: Path, tmp_path: Path
) -> None:
    wt_path_in = _wt(tmp_path, "feat-T-2-dev")
    branch1, wt1 = await ensure_worktree(
        str(repo), "feat/T-2-dev", wt_path_in
    )
    branch2, wt2 = await ensure_worktree(
        str(repo), "feat/T-2-dev", wt_path_in
    )

    assert branch1 == branch2 == "feat/T-2-dev"
    assert wt1 == wt2
    assert Path(wt1).is_dir()


async def test_ensure_worktree_custom_branch_name(
    repo: Path, tmp_path: Path
) -> None:
    """Branch names with slashes (e.g. devwork/ws-abc) must be accepted."""
    wt_path_in = _wt(tmp_path, "devwork-demo-abc")
    branch, wt = await ensure_worktree(
        str(repo), "devwork/demo-abc", wt_path_in
    )
    assert branch == "devwork/demo-abc"
    out = await _git("rev-parse", "--verify", "devwork/demo-abc", cwd=repo)
    assert len(out) == 40
    assert Path(wt).is_dir()
