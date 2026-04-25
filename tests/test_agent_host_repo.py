"""Unit tests for AgentHostRepo + AgentDispatchRepo (Phase 8a)."""
from __future__ import annotations

import pytest

from src.agent_hosts.repo import AgentDispatchRepo, AgentHostRepo
from src.config import AgentHostConfig, AgentsConfig
from src.database import Database
from src.exceptions import BadRequestError, ConflictError, NotFoundError


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    yield dict(
        db=db,
        host_repo=AgentHostRepo(db),
        dispatch_repo=AgentDispatchRepo(db),
    )
    await db.close()


async def _seed_workspace(db, ws_id: str = "ws-x") -> str:
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,"
        "updated_at) VALUES(?,?,?,?,?,?,?)",
        (ws_id, "t", ws_id, "active", f"/tmp/{ws_id}",
         "2026-04-25T00:00:00Z", "2026-04-25T00:00:00Z"),
    )
    return ws_id


# ---- AgentHostRepo ---------------------------------------------------------

async def test_upsert_inserts_new_host(env):
    row = await env["host_repo"].upsert(
        id="local", host="local", agent_type="both", max_concurrent=2,
        labels=["dev"],
    )
    assert row["id"] == "local"
    assert row["host"] == "local"
    assert row["agent_type"] == "both"
    assert row["max_concurrent"] == 2
    assert row["labels"] == ["dev"]
    assert row["health_status"] == "unknown"


async def test_upsert_preserves_health_on_update(env):
    repo = env["host_repo"]
    await repo.upsert(id="h1", host="u@h", agent_type="codex")
    await repo.update_health("h1", status="healthy")
    # Re-upsert with new max_concurrent must NOT reset health.
    await repo.upsert(id="h1", host="u@h", agent_type="codex", max_concurrent=4)
    row = await repo.get("h1")
    assert row["health_status"] == "healthy"
    assert row["max_concurrent"] == 4


async def test_upsert_rejects_invalid_agent_type(env):
    with pytest.raises(BadRequestError):
        await env["host_repo"].upsert(id="x", host="local", agent_type="bogus")


async def test_list_active_returns_only_healthy(env):
    repo = env["host_repo"]
    await repo.upsert(id="h-ok", host="u@a", agent_type="both")
    await repo.upsert(id="h-down", host="u@b", agent_type="both")
    await repo.update_health("h-ok", status="healthy")
    await repo.update_health("h-down", status="unhealthy", err="boom")
    active = await repo.list_active()
    assert [h["id"] for h in active] == ["h-ok"]


async def test_delete_local_rejected(env):
    await env["host_repo"].upsert(id="local", host="local", agent_type="both")
    with pytest.raises(BadRequestError):
        await env["host_repo"].delete("local")


async def test_delete_missing_raises_not_found(env):
    with pytest.raises(NotFoundError):
        await env["host_repo"].delete("ah-nope")


async def test_delete_blocked_by_active_dispatches(env):
    await env["host_repo"].upsert(id="h1", host="u@h", agent_type="both")
    ws = await _seed_workspace(env["db"])
    ad = await env["dispatch_repo"].start(
        host_id="h1", workspace_id=ws,
        correlation_id="dw-1", correlation_kind="dev_work",
    )
    await env["dispatch_repo"].mark_running(ad["id"])
    with pytest.raises(ConflictError):
        await env["host_repo"].delete("h1")


async def test_sync_from_config_inserts_local_when_missing(env):
    cfg = AgentsConfig(hosts=[])
    out = await env["host_repo"].sync_from_config(cfg)
    assert "local" in out["upserted"]
    row = await env["host_repo"].get("local")
    assert row is not None and row["host"] == "local"


async def test_sync_from_config_marks_stale_unknown(env):
    repo = env["host_repo"]
    # Seed a host that the next config will omit.
    await repo.upsert(id="ah-old", host="u@old", agent_type="both")
    await repo.update_health("ah-old", status="healthy")
    cfg = AgentsConfig(hosts=[
        AgentHostConfig(id="local", host="local", agent_type="both"),
    ])
    out = await repo.sync_from_config(cfg)
    assert "ah-old" in out["marked_unknown"]
    assert (await repo.get("ah-old"))["health_status"] == "unknown"


# ---- AgentDispatchRepo -----------------------------------------------------

async def test_dispatch_lifecycle(env):
    await env["host_repo"].upsert(id="local", host="local", agent_type="both")
    ws = await _seed_workspace(env["db"])
    repo = env["dispatch_repo"]
    ad = await repo.start(
        host_id="local", workspace_id=ws,
        correlation_id="dw-1", correlation_kind="dev_work",
    )
    assert ad["state"] == "queued"
    await repo.mark_running(ad["id"])
    await repo.mark_finished(ad["id"], state="succeeded", exit_code=0)
    row = await repo.get(ad["id"])
    assert row["state"] == "succeeded"
    assert row["exit_code"] == 0
    assert row["started_at"] is not None
    assert row["finished_at"] is not None


async def test_dispatch_invalid_correlation_kind(env):
    with pytest.raises(BadRequestError):
        await env["dispatch_repo"].start(
            host_id="local", workspace_id="ws-x",
            correlation_id="x", correlation_kind="bogus",
        )


async def test_dispatch_list_for_correlation(env):
    await env["host_repo"].upsert(id="local", host="local", agent_type="both")
    ws = await _seed_workspace(env["db"])
    repo = env["dispatch_repo"]
    a = await repo.start(host_id="local", workspace_id=ws,
                         correlation_id="dw-1", correlation_kind="dev_work")
    b = await repo.start(host_id="local", workspace_id=ws,
                         correlation_id="dw-1", correlation_kind="dev_work")
    rows = await repo.list_for_correlation(
        correlation_kind="dev_work", correlation_id="dw-1"
    )
    assert {r["id"] for r in rows} == {a["id"], b["id"]}
