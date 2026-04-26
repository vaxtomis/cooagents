"""Unit tests for :class:`DevWorkRepoStateRepo` (Phase 5).

Mirrors the fixture pattern in ``test_repo_registry_repo.py`` — direct
``:memory:`` Database, raw SQL seed for the FK chain, and direct repo
class instantiation.
"""
from __future__ import annotations

import pytest

from src.database import Database
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.repos import DevWorkRepoStateRepo, RepoRegistryRepo
from src.repos.dev_work_repo_state import (
    _MAX_PUSH_ERR_LEN,
    _sanitize_push_err,
)


NOW = "2026-04-25T00:00:00Z"


async def _seed_dev_work(
    db,
    *,
    dw_id: str = "dev-x",
    ws_id: str = "ws-x",
    design_doc_id: str = "des-x",
) -> str:
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,"
        "updated_at) VALUES(?,?,?,?,?,?,?)",
        (ws_id, "t", ws_id, "active", f"/tmp/{ws_id}", NOW, NOW),
    )
    await db.execute(
        "INSERT INTO design_docs(id,workspace_id,slug,version,path,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (design_doc_id, ws_id, "s", "1.0.0", "designs/x.md", NOW),
    )
    await db.execute(
        "INSERT INTO dev_works(id,workspace_id,design_doc_id,prompt,"
        "current_step,iteration_rounds,agent,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (dw_id, ws_id, design_doc_id, "p", "INIT", 0,
         "claude", NOW, NOW),
    )
    return dw_id


async def _seed_repo(registry: RepoRegistryRepo, *, id: str, name: str) -> str:
    await registry.upsert(
        id=id,
        name=name,
        url=f"git@gh:org/{name}.git",
        default_branch="main",
        ssh_key_path=f"/home/agent/.ssh/{name}_id",
        role="backend",
    )
    return id


async def _seed_dev_work_repo(
    db,
    *,
    dw_id: str,
    repo_id: str,
    mount_name: str,
    push_state: str = "pending",
    is_primary: bool = False,
) -> None:
    await db.execute(
        "INSERT INTO dev_work_repos(dev_work_id,repo_id,mount_name,"
        "base_branch,base_rev,devwork_branch,push_state,push_err,"
        "is_primary,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            dw_id, repo_id, mount_name, "main", None,
            f"devwork/{dw_id}/{mount_name}", push_state, None,
            1 if is_primary else 0, NOW, NOW,
        ),
    )


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    registry = RepoRegistryRepo(db)
    state = DevWorkRepoStateRepo(db)
    yield dict(db=db, registry=registry, state=state)
    await db.close()


# --- update_push_state happy paths -----------------------------------------

async def test_update_push_state_pending_to_pushed(env):
    db, registry, state = env["db"], env["registry"], env["state"]
    await _seed_dev_work(db)
    await _seed_repo(registry, id="repo-1", name="be")
    await _seed_dev_work_repo(
        db, dw_id="dev-x", repo_id="repo-1", mount_name="backend",
    )

    row = await state.update_push_state(
        "dev-x", "backend", push_state="pushed",
    )
    assert row["push_state"] == "pushed"
    assert row["push_err"] is None


async def test_update_push_state_pending_to_failed_with_msg(env):
    db, registry, state = env["db"], env["registry"], env["state"]
    await _seed_dev_work(db)
    await _seed_repo(registry, id="repo-1", name="be")
    await _seed_dev_work_repo(
        db, dw_id="dev-x", repo_id="repo-1", mount_name="backend",
    )

    row = await state.update_push_state(
        "dev-x", "backend", push_state="failed", error_msg="boom",
    )
    assert row["push_state"] == "failed"
    assert row["push_err"] == "boom"


async def test_update_push_state_idempotent_pushed_to_pushed(env):
    db, registry, state = env["db"], env["registry"], env["state"]
    await _seed_dev_work(db)
    await _seed_repo(registry, id="repo-1", name="be")
    await _seed_dev_work_repo(
        db, dw_id="dev-x", repo_id="repo-1", mount_name="backend",
        push_state="pushed",
    )

    row = await state.update_push_state(
        "dev-x", "backend", push_state="pushed",
    )
    assert row["push_state"] == "pushed"
    # push_err remains None (was already None) — clearing on success is
    # the explicit contract.
    assert row["push_err"] is None


async def test_update_push_state_failed_to_pushed_clears_err(env):
    db, registry, state = env["db"], env["registry"], env["state"]
    await _seed_dev_work(db)
    await _seed_repo(registry, id="repo-1", name="be")
    await _seed_dev_work_repo(
        db, dw_id="dev-x", repo_id="repo-1", mount_name="backend",
    )
    # First report fail, then success on retry.
    await state.update_push_state(
        "dev-x", "backend", push_state="failed", error_msg="transient",
    )
    row = await state.update_push_state(
        "dev-x", "backend", push_state="pushed",
    )
    assert row["push_state"] == "pushed"
    assert row["push_err"] is None


# --- update_push_state error paths ------------------------------------------

async def test_update_push_state_pushed_to_failed_conflict(env):
    db, registry, state = env["db"], env["registry"], env["state"]
    await _seed_dev_work(db)
    await _seed_repo(registry, id="repo-1", name="be")
    await _seed_dev_work_repo(
        db, dw_id="dev-x", repo_id="repo-1", mount_name="backend",
        push_state="pushed",
    )

    with pytest.raises(ConflictError) as exc_info:
        await state.update_push_state(
            "dev-x", "backend", push_state="failed", error_msg="late",
        )
    assert exc_info.value.current_stage == "pushed"


async def test_update_push_state_unknown_mount_404(env):
    db, registry, state = env["db"], env["registry"], env["state"]
    await _seed_dev_work(db)
    await _seed_repo(registry, id="repo-1", name="be")

    with pytest.raises(NotFoundError):
        await state.update_push_state(
            "dev-x", "no-such-mount", push_state="pushed",
        )


async def test_update_push_state_rejects_pending(env):
    db, registry, state = env["db"], env["registry"], env["state"]
    await _seed_dev_work(db)
    await _seed_repo(registry, id="repo-1", name="be")
    await _seed_dev_work_repo(
        db, dw_id="dev-x", repo_id="repo-1", mount_name="backend",
    )

    with pytest.raises(BadRequestError):
        await state.update_push_state(
            "dev-x", "backend", push_state="pending",
        )


# --- list_for_dev_work / batch ---------------------------------------------

async def test_list_for_dev_work_joins_url_and_ssh_key(env):
    db, registry, state = env["db"], env["registry"], env["state"]
    await _seed_dev_work(db)
    await _seed_repo(registry, id="repo-1", name="be")
    await _seed_repo(registry, id="repo-2", name="fe")
    await _seed_dev_work_repo(
        db, dw_id="dev-x", repo_id="repo-1", mount_name="backend",
        is_primary=True,
    )
    await _seed_dev_work_repo(
        db, dw_id="dev-x", repo_id="repo-2", mount_name="frontend",
    )

    rows = await state.list_for_dev_work("dev-x")
    assert len(rows) == 2
    # ORDER BY mount_name lex → "backend" before "frontend"
    assert [r["mount_name"] for r in rows] == ["backend", "frontend"]
    assert rows[0]["url"] == "git@gh:org/be.git"
    assert rows[0]["ssh_key_path"] == "/home/agent/.ssh/be_id"
    assert rows[0]["is_primary"] == 1
    assert rows[1]["url"] == "git@gh:org/fe.git"


async def test_list_for_dev_works_batch_groups_correctly(env):
    db, registry, state = env["db"], env["registry"], env["state"]
    await _seed_dev_work(db, dw_id="dev-a", ws_id="ws-a", design_doc_id="des-a")
    await _seed_dev_work(db, dw_id="dev-b", ws_id="ws-b", design_doc_id="des-b")
    await _seed_repo(registry, id="repo-1", name="be")
    await _seed_dev_work_repo(
        db, dw_id="dev-a", repo_id="repo-1", mount_name="backend",
    )
    await _seed_dev_work_repo(
        db, dw_id="dev-b", repo_id="repo-1", mount_name="backend",
    )

    grouped = await state.list_for_dev_works_batch(["dev-a", "dev-b"])
    assert set(grouped.keys()) == {"dev-a", "dev-b"}
    assert len(grouped["dev-a"]) == 1
    assert len(grouped["dev-b"]) == 1


async def test_list_for_dev_works_batch_empty_input(env):
    state = env["state"]
    grouped = await state.list_for_dev_works_batch([])
    assert grouped == {}


async def test_list_for_dev_works_batch_includes_empty_for_missing(env):
    """Caller's keys round-trip even when no rows exist for a given id."""
    db, registry, state = env["db"], env["registry"], env["state"]
    await _seed_dev_work(db)
    grouped = await state.list_for_dev_works_batch(["dev-x", "dev-nope"])
    assert grouped["dev-x"] == []
    assert grouped["dev-nope"] == []


# --- _sanitize_push_err -----------------------------------------------------

def test_sanitize_push_err_none_passthrough():
    assert _sanitize_push_err(None) is None


def test_sanitize_push_err_empty_returns_none():
    assert _sanitize_push_err("   ") is None


def test_sanitize_push_err_strips_control_bytes():
    assert _sanitize_push_err("a\x00b\x07c") == "abc"


def test_sanitize_push_err_truncates_at_max():
    long = "x" * (_MAX_PUSH_ERR_LEN + 100)
    out = _sanitize_push_err(long)
    assert out is not None
    assert len(out) == _MAX_PUSH_ERR_LEN
    assert out.endswith("…")


async def test_update_push_state_failed_persists_truncated_err(env):
    db, registry, state = env["db"], env["registry"], env["state"]
    await _seed_dev_work(db)
    await _seed_repo(registry, id="repo-1", name="be")
    await _seed_dev_work_repo(
        db, dw_id="dev-x", repo_id="repo-1", mount_name="backend",
    )
    long = "y" * 1000
    row = await state.update_push_state(
        "dev-x", "backend", push_state="failed", error_msg=long,
    )
    assert row["push_state"] == "failed"
    assert row["push_err"] is not None
    assert len(row["push_err"]) <= _MAX_PUSH_ERR_LEN
