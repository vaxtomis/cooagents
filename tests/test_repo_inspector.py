"""Unit tests for RepoInspector (Phase 3, repo-registry).

Tests run against a real bare clone built in ``tmp_path``. The fixture
constructs a tiny seed repo with two branches and a subdirectory so the
tree / log / blob walks have something interesting to chew on.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from src.database import Database
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.repos.fetcher import RepoFetcher
from src.repos.inspector import (
    BLOB_SIZE_CAP_BYTES,
    DEFAULT_LOG_LIMIT,
    DEFAULT_TREE_DEPTH,
    RepoInspector,
)
from src.repos.registry import RepoRegistryRepo


REPO_ID = "repo-aaa"


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> None:
    """Synchronous git driver for fixture setup; the inspector tests
    themselves still go through the async surface."""
    proc_env = dict(os.environ)
    proc_env.setdefault("GIT_AUTHOR_NAME", "Test")
    proc_env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    proc_env.setdefault("GIT_COMMITTER_NAME", "Test")
    proc_env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    if env:
        proc_env.update(env)
    subprocess.run(
        ["git", *args], cwd=str(cwd), env=proc_env,
        check=True, capture_output=True,
    )


@pytest.fixture
def bare_repo(tmp_path):
    """Build a bare clone with branches ``main`` + ``dev`` and an ``app/`` dir."""
    src = tmp_path / "src"
    src.mkdir()
    # Force ``main`` even on Windows where init.defaultBranch may be ``master``.
    _git(src, "-c", "init.defaultBranch=main", "init")
    _git(src, "config", "user.email", "test@example.com")
    _git(src, "config", "user.name", "Test")
    (src / "README.md").write_text("hello world\n")
    _git(src, "add", "README.md")
    _git(src, "commit", "-m", "init")
    # Subdirectory + second commit on main.
    (src / "app").mkdir()
    (src / "app" / "index.py").write_text("print('hi')\n")
    _git(src, "add", "app/index.py")
    _git(src, "commit", "-m", "add app")
    # Second branch.
    _git(src, "checkout", "-b", "dev")
    (src / "DEV.md").write_text("dev only\n")
    _git(src, "add", "DEV.md")
    _git(src, "commit", "-m", "dev branch commit")
    _git(src, "checkout", "main")

    bare = tmp_path / f"{REPO_ID}.git"
    subprocess.run(
        ["git", "clone", "--bare", str(src), str(bare)],
        check=True, capture_output=True,
    )
    return bare


@pytest.fixture
async def env(tmp_path, bare_repo):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    registry = RepoRegistryRepo(db)
    await registry.upsert(
        id=REPO_ID,
        name="frontend",
        url="git@example:org/frontend.git",
        default_branch="main",
        bare_clone_path=str(bare_repo),
    )
    # Mark healthy so the inspector's fetch_status check is satisfied.
    await registry.update_fetch_status(
        REPO_ID, status="healthy", err=None,
        bare_clone_path=str(bare_repo),
    )
    fetcher = RepoFetcher(workspaces_root=tmp_path)
    inspector = RepoInspector(
        fetcher=fetcher, registry=registry, timeout_s=30,
    )
    yield {"db": db, "registry": registry, "inspector": inspector,
           "bare": bare_repo, "tmp": tmp_path}
    await db.close()


# Branches --------------------------------------------------------------------

async def test_branches_returns_default_first(env):
    result = await env["inspector"].branches(REPO_ID)
    assert result.default_branch == "main"
    assert "main" in result.branches
    assert "dev" in result.branches
    assert result.branches[0] == "main"


# rev_parse -------------------------------------------------------------------

async def test_rev_parse_existing_returns_sha(env):
    sha = await env["inspector"].rev_parse(REPO_ID, "main")
    assert sha is not None
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


async def test_rev_parse_missing_returns_none(env):
    sha = await env["inspector"].rev_parse(REPO_ID, "nonexistent")
    assert sha is None


async def test_rev_parse_rejects_dangerous_ref(env):
    with pytest.raises(BadRequestError):
        await env["inspector"].rev_parse(REPO_ID, "--upload-pack=evil")


async def test_rev_parse_rejects_leading_dash(env):
    with pytest.raises(BadRequestError):
        await env["inspector"].rev_parse(REPO_ID, "-main")


# tree ------------------------------------------------------------------------

async def test_tree_root_lists_entries(env):
    tree = await env["inspector"].tree(REPO_ID, ref="main", path="", depth=1)
    paths = {e.path for e in tree.entries}
    assert "README.md" in paths
    assert "app" in paths
    # README is a blob with a size column.
    readme = next(e for e in tree.entries if e.path == "README.md")
    assert readme.type == "blob"
    assert readme.size is not None
    app_entry = next(e for e in tree.entries if e.path == "app")
    assert app_entry.type == "tree"
    assert app_entry.size is None
    assert tree.truncated is False


async def test_tree_depth_2_recurses(env):
    tree = await env["inspector"].tree(REPO_ID, ref="main", path="", depth=2)
    paths = {e.path for e in tree.entries}
    assert "app/index.py" in paths


async def test_tree_truncates_at_max_entries(env):
    tree = await env["inspector"].tree(
        REPO_ID, ref="main", path="", depth=1, max_entries=1,
    )
    assert tree.truncated is True
    assert len(tree.entries) == 1


async def test_tree_rejects_path_traversal(env):
    with pytest.raises(BadRequestError):
        await env["inspector"].tree(REPO_ID, ref="main", path="../etc")


async def test_tree_rejects_leading_slash(env):
    with pytest.raises(BadRequestError):
        await env["inspector"].tree(REPO_ID, ref="main", path="/etc")


async def test_tree_rejects_leading_dash(env):
    """Leading '-' would let a path masquerade as a git option flag."""
    with pytest.raises(BadRequestError):
        await env["inspector"].tree(REPO_ID, ref="main", path="-rf")


async def test_validate_path_accepts_realistic_segments():
    """Real-world repos contain segments like @types, (deprecated), +page.

    The deny-list validator must allow these — the regex allowlist used
    in earlier drafts rejected them and made the inspector unusable on
    typical TS / Svelte / monorepo trees.
    """
    from src.repos.inspector import _validate_path
    # None of these should raise.
    _validate_path("@types/foo.d.ts")
    _validate_path("packages/(deprecated)/old.go")
    _validate_path("src/+page.svelte")
    _validate_path("a b/c d.txt")  # spaces in segments
    _validate_path("中文/文件.md")  # non-ASCII


async def test_tree_invalid_depth_falls_back_to_default(env):
    tree = await env["inspector"].tree(
        REPO_ID, ref="main", path="", depth=0,
    )
    # depth=0 is silently bumped to DEFAULT_TREE_DEPTH; doesn't error.
    assert isinstance(tree.entries, list)
    _ = DEFAULT_TREE_DEPTH  # constant exported


async def test_tree_missing_ref_returns_400(env):
    with pytest.raises(BadRequestError):
        await env["inspector"].tree(REPO_ID, ref="nonexistent", path="")


# blob ------------------------------------------------------------------------

async def test_blob_returns_text_content(env):
    blob = await env["inspector"].blob(REPO_ID, ref="main", path="README.md")
    assert blob.binary is False
    assert blob.content == "hello world\n"
    assert blob.size == len("hello world\n")


async def test_blob_oversize_rejected(env, bare_repo, tmp_path):
    # Add a large blob to the seed source repo, re-clone bare.
    src = tmp_path / "src"
    big = src / "BIG.bin"
    big.write_bytes(b"A" * (BLOB_SIZE_CAP_BYTES + 16))
    _git(src, "add", "BIG.bin")
    _git(src, "commit", "-m", "big blob")
    # Push the new commit into the existing bare clone (non-fast-forward not
    # an issue — we are the only writer in the test).
    subprocess.run(
        ["git", "push", str(bare_repo), "main"],
        cwd=str(src), check=True, capture_output=True,
    )
    with pytest.raises(BadRequestError, match="cap"):
        await env["inspector"].blob(REPO_ID, ref="main", path="BIG.bin")


async def test_blob_path_is_tree_rejected(env):
    with pytest.raises(BadRequestError, match="not a blob"):
        await env["inspector"].blob(REPO_ID, ref="main", path="app")


async def test_blob_missing_path_400(env):
    with pytest.raises(BadRequestError):
        await env["inspector"].blob(REPO_ID, ref="main", path="nope.txt")


async def test_blob_rejects_empty_path(env):
    with pytest.raises(BadRequestError):
        await env["inspector"].blob(REPO_ID, ref="main", path="")


# log -------------------------------------------------------------------------

async def test_log_default_limit(env):
    log = await env["inspector"].log(REPO_ID, ref="main")
    # init + add app => at least 2 entries
    assert len(log.entries) >= 2
    e0 = log.entries[0]
    assert len(e0.sha) == 40
    assert e0.subject  # non-empty subject
    assert "@" in e0.email


async def test_log_path_filter(env):
    log = await env["inspector"].log(
        REPO_ID, ref="main", path="app/index.py",
    )
    # Only commits touching app/index.py — exactly one (the "add app" commit).
    assert len(log.entries) == 1
    assert log.entries[0].subject == "add app"


async def test_log_limit_invalid_falls_back_to_default(env):
    log = await env["inspector"].log(REPO_ID, ref="main", limit=0)
    assert len(log.entries) <= DEFAULT_LOG_LIMIT


async def test_log_limit_explicit(env):
    log = await env["inspector"].log(REPO_ID, ref="main", limit=1)
    assert len(log.entries) == 1


async def test_log_offset_skips_latest_entry(env):
    first_page = await env["inspector"].log(REPO_ID, ref="main", limit=1, offset=0)
    second_page = await env["inspector"].log(REPO_ID, ref="main", limit=1, offset=1)
    assert len(first_page.entries) == 1
    assert len(second_page.entries) == 1
    assert first_page.entries[0].sha != second_page.entries[0].sha


async def test_log_count_matches_commit_volume(env):
    total = await env["inspector"].log_count(REPO_ID, ref="main")
    log = await env["inspector"].log(REPO_ID, ref="main", limit=10)
    assert total >= len(log.entries) >= 2


# 404 / 409 paths -------------------------------------------------------------

async def test_inspector_404_when_repo_unknown(env):
    with pytest.raises(NotFoundError):
        await env["inspector"].branches("repo-nope")


async def test_inspector_409_when_no_bare_clone(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    try:
        registry = RepoRegistryRepo(db)
        # Insert without a bare_clone_path — fetch_status defaults to 'unknown'.
        await registry.upsert(
            id="repo-empty",
            name="empty",
            url="git@example:org/empty.git",
        )
        fetcher = RepoFetcher(workspaces_root=tmp_path)
        inspector = RepoInspector(
            fetcher=fetcher, registry=registry, timeout_s=30,
        )
        with pytest.raises(ConflictError):
            await inspector.branches("repo-empty")
    finally:
        await db.close()
