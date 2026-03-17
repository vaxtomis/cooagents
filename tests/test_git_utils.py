"""Tests for src/git_utils.py using real temporary git repositories."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from src.git_utils import (
    check_conflicts,
    ensure_worktree,
    get_commit_log,
    get_diff_stat,
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


async def test_ensure_worktree_creates_new(repo: Path) -> None:
    """ensure_worktree should create a new worktree directory and branch."""
    branch, wt_path = await ensure_worktree(
        str(repo), ticket="T-1", phase="design"
    )

    assert branch == "feat/T-1-design"
    assert Path(wt_path).is_dir(), "Worktree directory was not created"

    # The branch must exist in the repo
    branches_out = await _git("branch", cwd=repo)
    assert "feat/T-1-design" in branches_out


async def test_ensure_worktree_reuses_existing(repo: Path) -> None:
    """Calling ensure_worktree twice must not raise and must return same paths."""
    branch1, wt1 = await ensure_worktree(str(repo), ticket="T-2", phase="dev")
    branch2, wt2 = await ensure_worktree(str(repo), ticket="T-2", phase="dev")

    assert branch1 == branch2
    assert wt1 == wt2
    assert Path(wt1).is_dir()


async def test_ensure_worktree_with_suffix(repo: Path) -> None:
    """run_suffix should be appended to the branch name."""
    branch, _ = await ensure_worktree(
        str(repo), ticket="T-3", phase="dev", run_suffix="r2"
    )
    assert branch == "feat/T-3-dev-r2"


async def test_get_diff_stat(repo: Path) -> None:
    """get_diff_stat should return non-empty output after commits beyond base."""
    _, wt_path = await ensure_worktree(str(repo), ticket="T-4", phase="design")

    base = await _git("rev-parse", "HEAD", cwd=wt_path)

    # Make a commit in the worktree
    (Path(wt_path) / "new_file.txt").write_text("hello\n")
    await _git("add", "new_file.txt", cwd=wt_path)
    await _git("commit", "-m", "add new_file", cwd=wt_path)

    stat = await get_diff_stat(wt_path, base)
    assert "new_file.txt" in stat


async def test_get_commit_log(repo: Path) -> None:
    """get_commit_log should return one entry per commit made after base."""
    _, wt_path = await ensure_worktree(str(repo), ticket="T-5", phase="design")

    base = await _git("rev-parse", "HEAD", cwd=wt_path)

    for i in range(3):
        (Path(wt_path) / f"f{i}.txt").write_text(f"content {i}\n")
        await _git("add", f"f{i}.txt", cwd=wt_path)
        await _git("commit", "-m", f"commit {i}", cwd=wt_path)

    log = await get_commit_log(wt_path, base)

    assert len(log) == 3
    for entry in log:
        assert len(entry["hash"]) == 40
        assert "commit" in entry["message"]
        assert entry["files_changed"] >= 1
        assert entry["insertions"] >= 1


async def test_check_conflicts_no_conflict(repo: Path) -> None:
    """Two branches modifying different files should produce no conflicts."""
    # Branch A: modifies file_a.txt
    _, wt_a = await ensure_worktree(str(repo), ticket="T-6", phase="design")
    (Path(wt_a) / "file_a.txt").write_text("branch A content\n")
    await _git("add", "file_a.txt", cwd=wt_a)
    await _git("commit", "-m", "branch A change", cwd=wt_a)

    # Back on main: add file_b.txt so there is something to merge against
    (repo / "file_b.txt").write_text("main content\n")
    await _git("add", "file_b.txt", cwd=repo)
    await _git("commit", "-m", "main adds file_b", cwd=repo)

    conflicts = await check_conflicts(wt_a, target_branch="main")
    assert conflicts == [], f"Expected no conflicts, got: {conflicts}"


async def test_check_conflicts_with_conflict(repo: Path) -> None:
    """Two branches modifying the same line of the same file → conflict."""
    # Add a shared file on main first
    (repo / "shared.txt").write_text("original line\n")
    await _git("add", "shared.txt", cwd=repo)
    await _git("commit", "-m", "add shared file", cwd=repo)

    # Feature branch: modify shared.txt
    _, wt = await ensure_worktree(str(repo), ticket="T-7", phase="dev")
    (Path(wt) / "shared.txt").write_text("feature branch line\n")
    await _git("add", "shared.txt", cwd=wt)
    await _git("commit", "-m", "feature changes shared", cwd=wt)

    # Now change shared.txt on main as well (diverge)
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
