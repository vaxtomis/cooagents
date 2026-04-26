"""Unit tests for RepoRegistryRepo + credential resolver (Phase 1)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import RepoConfig, ReposConfig
from src.database import Database
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.repos import RepoRegistryRepo, SshKeyMaterial, resolve_repo_credential


NOW = "2026-04-25T00:00:00Z"


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    yield dict(db=db, repo=RepoRegistryRepo(db))
    await db.close()


async def _seed_dev_work(
    db,
    dw_id: str = "dev-x",
    ws_id: str = "ws-x",
    design_doc_id: str = "des-x",
) -> str:
    """Seed the FK chain required for a dev_work_repos row."""
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


# ---- RepoRegistryRepo CRUD ------------------------------------------------

async def test_upsert_inserts_new_repo(env):
    row = await env["repo"].upsert(
        id="repo-aaa",
        name="frontend",
        url="git@github.com:org/frontend.git",
        default_branch="main",
        ssh_key_path="/home/u/.ssh/id_rsa",
    )
    assert row["id"] == "repo-aaa"
    assert row["name"] == "frontend"
    assert row["fetch_status"] == "unknown"
    assert row["ssh_key_path"] == "/home/u/.ssh/id_rsa"
    assert row["default_branch"] == "main"


async def test_upsert_preserves_fetch_status(env):
    repo = env["repo"]
    await repo.upsert(
        id="repo-1", name="frontend",
        url="git@github.com:org/frontend.git",
    )
    await repo.update_fetch_status("repo-1", status="healthy")
    # Re-upsert with new url must NOT reset fetch_status.
    await repo.upsert(
        id="repo-1", name="frontend",
        url="git@github.com:org/frontend-renamed.git",
    )
    row = await repo.get("repo-1")
    assert row["fetch_status"] == "healthy"
    assert row["url"] == "git@github.com:org/frontend-renamed.git"
    assert row["last_fetched_at"] is not None


async def test_upsert_rejects_empty_name(env):
    with pytest.raises(BadRequestError):
        await env["repo"].upsert(
            id="repo-x", name="", url="git@x:o/r.git",
        )


async def test_upsert_rejects_empty_url(env):
    with pytest.raises(BadRequestError):
        await env["repo"].upsert(
            id="repo-x", name="frontend", url="",
        )


async def test_update_fetch_status_rejects_invalid(env):
    repo = env["repo"]
    await repo.upsert(id="r1", name="frontend",
                      url="git@x:o/r.git")
    with pytest.raises(BadRequestError):
        await repo.update_fetch_status("r1", status="bogus")


async def test_update_fetch_status_error_does_not_stamp_last_fetched(env):
    repo = env["repo"]
    await repo.upsert(id="r1", name="frontend", url="git@x:o/r.git")
    await repo.update_fetch_status("r1", status="error", err="boom")
    row = await repo.get("r1")
    assert row["fetch_status"] == "error"
    assert row["last_fetched_at"] is None
    assert row["last_fetch_err"] == "boom"


async def test_get_by_name(env):
    repo = env["repo"]
    await repo.upsert(id="r1", name="frontend", url="git@x:o/r.git")
    row = await repo.get_by_name("frontend")
    assert row is not None
    assert row["id"] == "r1"
    assert await repo.get_by_name("nope") is None


async def test_list_all_orders_by_name(env):
    repo = env["repo"]
    await repo.upsert(id="r2", name="zeta", url="git@x:o/z.git")
    await repo.upsert(id="r1", name="alpha", url="git@x:o/a.git")
    rows = await repo.list_all()
    assert [r["name"] for r in rows] == ["alpha", "zeta"]


async def test_delete_blocked_by_fk(env):
    repo = env["repo"]
    await repo.upsert(
        id="repo-x", name="frontend",
        url="git@github.com:org/frontend.git",
    )
    dw = await _seed_dev_work(env["db"])
    await env["db"].execute(
        "INSERT INTO dev_work_repos(dev_work_id,repo_id,mount_name,base_branch,"
        "devwork_branch,push_state,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (dw, "repo-x", "frontend", "main",
         "devwork/x/abcdef123456", "pending", NOW, NOW),
    )
    with pytest.raises(ConflictError):
        await repo.delete("repo-x")


async def test_delete_missing(env):
    with pytest.raises(NotFoundError):
        await env["repo"].delete("repo-nope")


async def test_delete_succeeds_when_no_refs(env):
    repo = env["repo"]
    await repo.upsert(id="r1", name="frontend", url="git@x:o/r.git")
    await repo.delete("r1")
    assert await repo.get("r1") is None


# ---- sync_from_config ------------------------------------------------------

async def test_sync_from_config_inserts(env):
    cfg = ReposConfig(repos=[
        RepoConfig(name="frontend", url="git@github.com:org/frontend.git"),
        RepoConfig(name="backend", url="git@github.com:org/backend.git"),
    ])
    out = await env["repo"].sync_from_config(cfg)
    assert len(out["upserted"]) == 2
    rows = await env["repo"].list_all()
    assert {r["name"] for r in rows} == {"frontend", "backend"}


async def test_sync_from_config_marks_stale_unknown(env):
    repo = env["repo"]
    seeded = await repo.upsert(
        id="repo-old", name="legacy",
        url="git@github.com:org/legacy.git",
    )
    await repo.update_fetch_status("repo-old", status="healthy")
    cfg = ReposConfig(repos=[
        RepoConfig(name="frontend", url="git@github.com:org/frontend.git"),
    ])
    out = await repo.sync_from_config(cfg)
    assert seeded["id"] in out["marked_unknown"]
    assert (await repo.get(seeded["id"]))["fetch_status"] == "unknown"
    # Row is NOT deleted.
    assert (await repo.get(seeded["id"])) is not None


async def test_sync_from_config_reuses_id_for_same_name(env):
    repo = env["repo"]
    cfg1 = ReposConfig(repos=[
        RepoConfig(name="frontend", url="git@x:o/r.git"),
    ])
    out1 = await repo.sync_from_config(cfg1)
    first_id = out1["upserted"][0]
    cfg2 = ReposConfig(repos=[
        RepoConfig(name="frontend", url="git@x:o/r-renamed.git"),
    ])
    out2 = await repo.sync_from_config(cfg2)
    assert out2["upserted"] == [first_id]
    row = await repo.get(first_id)
    assert row["url"] == "git@x:o/r-renamed.git"


# ---- credentials -----------------------------------------------------------

def test_credential_resolver_path():
    p = str(Path.home() / ".ssh" / "id_rsa")
    cred = resolve_repo_credential({"ssh_key_path": p})
    assert isinstance(cred, SshKeyMaterial)
    assert cred.private_key_path == Path.home() / ".ssh" / "id_rsa"


def test_credential_resolver_empty_returns_none():
    assert resolve_repo_credential({"ssh_key_path": None}) is None
    assert resolve_repo_credential({"ssh_key_path": ""}) is None
    assert resolve_repo_credential({}) is None


def test_credential_resolver_relative_rejects():
    with pytest.raises(BadRequestError):
        resolve_repo_credential({"ssh_key_path": "relative/key"})
