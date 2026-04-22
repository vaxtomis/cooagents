"""Tests for src/git_utils.py using real temporary git repositories."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from src.git_utils import (
    check_conflicts,
    ensure_worktree,
    get_head_commit,
    run_git,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _git(*args, cwd) -> str:
    """Run a git command in *cwd*, return stdout (stripped)."""
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
    """Initialise a bare-minimum git repo with one initial commit."""
    await _git("init", cwd=path)
    await _git("config", "user.email", "test@example.com", cwd=path)
    await _git("config", "user.name", "Test User", cwd=path)
    # Ensure we always work on 'main'
    await _git("checkout", "-b", "main", cwd=path)
    (path / "README.md").write_text("# initial\n")
    await _git("add", "README.md", cwd=path)
    await _git("commit", "-m", "init", cwd=path)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
async def repo(tmp_path: Path) -> Path:
    """Return a path to a freshly initialised git repo."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    await _make_repo(repo_dir)
    return repo_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _wt(tmp_path: Path, name: str) -> str:
    """Deterministic worktree path under a temp-root ``.worktrees`` dir."""
    return str(tmp_path / ".worktrees" / name)


async def test_ensure_worktree_creates_new(repo: Path, tmp_path: Path) -> None:
    """ensure_worktree should create a new worktree directory and branch."""
    wt_path_in = _wt(tmp_path, "feat-T-1-design")
    branch, wt_path = await ensure_worktree(
        str(repo), "feat/T-1-design", wt_path_in
    )

    assert branch == "feat/T-1-design"
    assert wt_path == wt_path_in
    assert Path(wt_path).is_dir(), "Worktree directory was not created"

    # The branch must exist in the repo
    branches_out = await _git("branch", cwd=repo)
    assert "feat/T-1-design" in branches_out


async def test_ensure_worktree_reuses_existing(
    repo: Path, tmp_path: Path
) -> None:
    """Calling ensure_worktree twice must not raise and must return same paths."""
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
    # git rev-parse confirms the branch
    out = await _git("rev-parse", "--verify", "devwork/demo-abc", cwd=repo)
    assert len(out) == 40
    assert Path(wt).is_dir()


async def test_check_conflicts_no_conflict(repo: Path, tmp_path: Path) -> None:
    """Two branches modifying different files should produce no conflicts."""
    _, wt_a = await ensure_worktree(
        str(repo), "feat/T-6-design", _wt(tmp_path, "feat-T-6-design")
    )
    (Path(wt_a) / "file_a.txt").write_text("branch A content\n")
    await _git("add", "file_a.txt", cwd=wt_a)
    await _git("commit", "-m", "branch A change", cwd=wt_a)

    # Back on main: add file_b.txt so there is something to merge against
    (repo / "file_b.txt").write_text("main content\n")
    await _git("add", "file_b.txt", cwd=repo)
    await _git("commit", "-m", "main adds file_b", cwd=repo)

    conflicts = await check_conflicts(wt_a, target_branch="main")
    assert conflicts == [], f"Expected no conflicts, got: {conflicts}"


async def test_check_conflicts_with_conflict(
    repo: Path, tmp_path: Path
) -> None:
    """Two branches modifying the same line of the same file → conflict."""
    (repo / "shared.txt").write_text("original line\n")
    await _git("add", "shared.txt", cwd=repo)
    await _git("commit", "-m", "add shared file", cwd=repo)

    _, wt = await ensure_worktree(
        str(repo), "feat/T-7-dev", _wt(tmp_path, "feat-T-7-dev")
    )
    (Path(wt) / "shared.txt").write_text("feature branch line\n")
    await _git("add", "shared.txt", cwd=wt)
    await _git("commit", "-m", "feature changes shared", cwd=wt)

    (repo / "shared.txt").write_text("main branch line\n")
    await _git("add", "shared.txt", cwd=repo)
    await _git("commit", "-m", "main changes shared", cwd=repo)

    conflicts = await check_conflicts(wt, target_branch="main")
    assert "shared.txt" in conflicts


async def test_get_head_commit(repo: Path) -> None:
    """get_head_commit should return a 40-character hex SHA."""
    head = await get_head_commit(str(repo))
    assert len(head) == 40, f"Expected 40-char hash, got {head!r}"
    assert re.fullmatch(r"[0-9a-f]{40}", head), f"Not a valid SHA: {head!r}"
