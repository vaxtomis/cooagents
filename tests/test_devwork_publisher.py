from __future__ import annotations

from pathlib import Path

import pytest

from src.database import Database
from src.git_utils import run_git
from src.repos import DevWorkPublisher, DevWorkRepoStateRepo, RepoRegistryRepo

NOW = "2026-05-01T00:00:00Z"
DEV_ID = "dev-abcdef123456"
BRANCH = "devwork/w1/abcdef123456"


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    state_repo = DevWorkRepoStateRepo(db)
    registry = RepoRegistryRepo(db)
    publisher = DevWorkPublisher(state_repo, timeout_s=30)
    try:
        yield {
            "db": db,
            "state_repo": state_repo,
            "registry": registry,
            "publisher": publisher,
            "tmp": tmp_path,
        }
    finally:
        await db.close()


async def _make_origin_and_worktree(tmp_path: Path) -> tuple[Path, Path]:
    origin_src = tmp_path / "origin-src"
    origin_src.mkdir()
    await run_git("init", cwd=str(origin_src))
    await run_git("config", "user.email", "test@example.com", cwd=str(origin_src))
    await run_git("config", "user.name", "Test", cwd=str(origin_src))
    await run_git("checkout", "-b", "main", cwd=str(origin_src), check=False)
    (origin_src / "README.md").write_text("# demo\n", encoding="utf-8")
    await run_git("add", "README.md", cwd=str(origin_src))
    await run_git("commit", "-m", "init", cwd=str(origin_src))

    origin_bare = tmp_path / "origin.git"
    await run_git("clone", "--bare", str(origin_src), str(origin_bare))

    worktree = tmp_path / "worktree"
    await run_git("clone", str(origin_bare), str(worktree))
    await run_git("checkout", "-b", BRANCH, cwd=str(worktree))
    return origin_bare, worktree


async def _seed(
    env,
    *,
    origin_bare: Path,
    worktree: Path,
    repo_id: str = "repo-pub",
    mount_name: str = "backend",
    push_state: str = "pending",
) -> None:
    db = env["db"]
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,"
        "updated_at) VALUES(?,?,?,?,?,?,?)",
        ("ws-pub", "T", "w1", "active", str(env["tmp"]), NOW, NOW),
    )
    await db.execute(
        "INSERT INTO design_docs(id,workspace_id,slug,version,path,created_at) "
        "VALUES(?,?,?,?,?,?)",
        ("des-pub", "ws-pub", "demo", "1.0.0", "designs/demo.md", NOW),
    )
    await db.execute(
        "INSERT INTO dev_works(id,workspace_id,design_doc_id,prompt,"
        "current_step,iteration_rounds,agent,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (DEV_ID, "ws-pub", "des-pub", "p", "COMPLETED", 0, "claude", NOW, NOW),
    )
    await env["registry"].upsert(
        id=repo_id,
        name=mount_name,
        url=str(origin_bare),
        default_branch="main",
        role="backend",
    )
    await db.execute(
        "INSERT INTO dev_work_repos(dev_work_id,repo_id,mount_name,"
        "base_branch,devwork_branch,push_state,worktree_path,created_at,"
        "updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            DEV_ID,
            repo_id,
            mount_name,
            "main",
            BRANCH,
            push_state,
            str(worktree),
            NOW,
            NOW,
        ),
    )


async def _push_row(db: Database) -> dict:
    row = await db.fetchone(
        "SELECT push_state, push_err FROM dev_work_repos "
        "WHERE dev_work_id=? AND mount_name=?",
        (DEV_ID, "backend"),
    )
    assert row is not None
    return dict(row)


async def test_publisher_dirty_worktree_commits_and_pushes(env):
    origin_bare, worktree = await _make_origin_and_worktree(env["tmp"])
    await _seed(env, origin_bare=origin_bare, worktree=worktree)
    (worktree / "feature.txt").write_text("hello\n", encoding="utf-8")

    report = await env["publisher"].publish(DEV_ID, 1)

    assert report.results[0].status == "pushed"
    row = await _push_row(env["db"])
    assert row["push_state"] == "pushed"
    assert row["push_err"] is None
    out, _, _ = await run_git(
        "--git-dir",
        str(origin_bare),
        "show",
        f"{BRANCH}:feature.txt",
    )
    assert out == "hello"
    subject, _, _ = await run_git(
        "--git-dir",
        str(origin_bare),
        "log",
        "-1",
        "--pretty=%s",
        BRANCH,
    )
    assert subject == "[devwork/w1/abcdef123456] round 1: completed"


async def test_publisher_clean_worktree_pushes_branch(env):
    origin_bare, worktree = await _make_origin_and_worktree(env["tmp"])
    await _seed(env, origin_bare=origin_bare, worktree=worktree)

    report = await env["publisher"].publish(DEV_ID, 1)

    assert report.results[0].status == "pushed"
    await run_git(
        "--git-dir",
        str(origin_bare),
        "rev-parse",
        "--verify",
        f"refs/heads/{BRANCH}",
    )
    assert (await _push_row(env["db"]))["push_state"] == "pushed"


async def test_publisher_failed_push_records_error(env):
    origin_bare, worktree = await _make_origin_and_worktree(env["tmp"])
    await _seed(env, origin_bare=origin_bare, worktree=worktree)
    await run_git(
        "remote",
        "set-url",
        "origin",
        str(env["tmp"] / "missing.git"),
        cwd=str(worktree),
    )
    (worktree / "feature.txt").write_text("hello\n", encoding="utf-8")

    report = await env["publisher"].publish(DEV_ID, 1)

    assert report.results[0].status == "failed"
    row = await _push_row(env["db"])
    assert row["push_state"] == "failed"
    assert row["push_err"]
    assert "git push failed" in row["push_err"]


async def test_publisher_retry_failed_to_pushed(env):
    origin_bare, worktree = await _make_origin_and_worktree(env["tmp"])
    await _seed(env, origin_bare=origin_bare, worktree=worktree)
    await run_git(
        "remote",
        "set-url",
        "origin",
        str(env["tmp"] / "missing.git"),
        cwd=str(worktree),
    )
    (worktree / "feature.txt").write_text("hello\n", encoding="utf-8")
    first = await env["publisher"].publish(DEV_ID, 1)
    assert first.results[0].status == "failed"
    assert (await _push_row(env["db"]))["push_state"] == "failed"

    await run_git(
        "remote",
        "set-url",
        "origin",
        str(origin_bare),
        cwd=str(worktree),
    )
    retry = await env["publisher"].publish(DEV_ID, 2)

    assert retry.results[0].status == "pushed"
    row = await _push_row(env["db"])
    assert row["push_state"] == "pushed"
    assert row["push_err"] is None
    out, _, _ = await run_git(
        "--git-dir",
        str(origin_bare),
        "show",
        f"{BRANCH}:feature.txt",
    )
    assert out == "hello"
