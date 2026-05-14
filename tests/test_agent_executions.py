"""Agent execution lease repo and internal route coverage."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from routes.agent_executions import router as agent_executions_router
from src.agent_hosts.execution_repo import AgentExecutionRepo
from src.agent_hosts.repo import AgentHostRepo
from src.database import Database
from src.exceptions import BadRequestError


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    host_repo = AgentHostRepo(db)
    await host_repo.upsert(id="local", host="local", agent_type="both")
    yield {"db": db, "repo": AgentExecutionRepo(db, lease_ttl_s=60)}
    await db.close()


async def test_execution_repo_lifecycle(env):
    repo = env["repo"]
    row = await repo.create_starting(
        dispatch_id=None,
        host_id="local",
        agent="codex",
        execution_mode="local",
        correlation_kind="dev_work",
        correlation_id="dev-1",
        cwd="/tmp/ws",
        session_name="s1",
    )
    assert row["state"] == "starting"
    assert row["run_token"]

    await repo.mark_process_started(
        row["id"],
        pid=123,
        pgid=123,
        pid_starttime="456",
        cwd="/tmp/ws",
    )
    running = await repo.get(row["id"])
    assert running is not None
    assert running["state"] == "running"
    assert running["pid"] == 123

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    expired = await repo.list_expired_for_host("local", now=future)
    assert [r["id"] for r in expired] == [row["id"]]

    await repo.heartbeat(row["id"])
    await repo.mark_cleanup_started(row["id"], reason="lease expired")
    cancelling = await repo.get(row["id"])
    assert cancelling is not None
    assert cancelling["state"] == "cancelling"
    assert cancelling["cleanup_attempts"] == 1

    await repo.mark_exited(row["id"], exit_code=0)
    done = await repo.get(row["id"])
    assert done is not None
    assert done["state"] == "exited"
    assert done["finished_at"] is not None


async def test_execution_repo_rejects_invalid_agent(env):
    with pytest.raises(BadRequestError):
        await env["repo"].create_starting(
            dispatch_id=None,
            host_id="local",
            agent="bad",
            execution_mode="local",
            correlation_kind="dev_work",
            correlation_id="dev-1",
            cwd="/tmp/ws",
        )


async def test_agent_execution_routes(env):
    app = FastAPI()
    app.state.agent_execution_repo = env["repo"]
    app.include_router(agent_executions_router, prefix="/api/v1")
    row = await env["repo"].create_starting(
        dispatch_id=None,
        host_id="local",
        agent="codex",
        execution_mode="local",
        correlation_kind="dev_work",
        correlation_id="dev-1",
        cwd="/tmp/ws",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as client:
        r = await client.post(
            f"/api/v1/internal/agent-executions/{row['id']}/started",
            json={
                "pid": 123,
                "pgid": 123,
                "pid_starttime": "456",
                "cwd": "/tmp/ws",
            },
        )
        assert r.status_code == 200
        assert r.json()["state"] == "running"

        r = await client.post(
            f"/api/v1/internal/agent-executions/{row['id']}/heartbeat",
        )
        assert r.status_code == 200

        r = await client.post(
            f"/api/v1/internal/agent-executions/{row['id']}/cleanup-result",
            json={"state": "killed", "cleanup_reason": "SIGKILL"},
        )
        assert r.status_code == 200
        assert r.json()["state"] == "killed"
