"""HTTP-level smoke tests for /api/v1/agent-hosts (Phase 8a)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from src.agent_hosts.repo import AgentDispatchRepo, AgentHostRepo
from src.config import AgentsConfig
from src.database import Database
from src.exceptions import BadRequestError, ConflictError, NotFoundError


class StubDispatcher:
    """Deterministic healthcheck for route tests."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def healthcheck(self, host_id: str) -> dict:
        self.calls.append(host_id)
        if host_id == "ah-bad":
            return {"health_status": "unhealthy", "last_health_err": "ssh"}
        return {"health_status": "healthy", "last_health_err": None}


@pytest.fixture
async def client(tmp_path):
    test_app = FastAPI(title="agent-hosts-route-test")
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()

    test_app.state.db = db
    test_app.state.agent_host_repo = AgentHostRepo(db)
    test_app.state.agent_dispatch_repo = AgentDispatchRepo(db)
    test_app.state.ssh_dispatcher = StubDispatcher()
    test_app.state.settings = type(
        "S", (), {"agents": AgentsConfig(hosts=[])}
    )()

    @test_app.exception_handler(NotFoundError)
    async def _nf(request, exc):
        return JSONResponse(status_code=404, content={"error": "not_found", "message": str(exc)})

    @test_app.exception_handler(ConflictError)
    async def _cf(request, exc):
        return JSONResponse(status_code=409, content={"error": "conflict", "message": str(exc)})

    @test_app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(status_code=400, content={"error": "bad_request", "message": str(exc)})

    from routes.agent_hosts import router as agent_hosts_router
    test_app.include_router(agent_hosts_router, prefix="/api/v1")

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        yield ac
    await db.close()


async def test_list_starts_with_local_only(client):
    r = await client.get("/api/v1/agent-hosts")
    assert r.status_code == 200
    data = r.json()
    assert [h["id"] for h in data] == ["local"]


async def test_create_remote_host_returns_location(client):
    r = await client.post("/api/v1/agent-hosts", json={
        "host": "dev@10.0.0.5", "agent_type": "codex",
        "max_concurrent": 2,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["host"] == "dev@10.0.0.5"
    assert body["health_status"] == "unknown"
    assert r.headers["Location"].startswith("/api/v1/agent-hosts/ah-")


async def test_create_rejects_reserved_local_id(client):
    r = await client.post("/api/v1/agent-hosts", json={
        "id": "local", "host": "u@h",
    })
    assert r.status_code == 400


async def test_get_missing_returns_404(client):
    r = await client.get("/api/v1/agent-hosts/ah-nope")
    assert r.status_code == 404


async def test_delete_local_returns_400(client):
    r = await client.delete("/api/v1/agent-hosts/local")
    assert r.status_code == 400


async def test_delete_remote_then_404(client):
    r = await client.post("/api/v1/agent-hosts", json={
        "host": "u@h", "agent_type": "both",
    })
    host_id = r.json()["id"]
    assert (await client.delete(f"/api/v1/agent-hosts/{host_id}")).status_code == 204
    assert (await client.get(f"/api/v1/agent-hosts/{host_id}")).status_code == 404


async def test_patch_updates_partial(client):
    r = await client.post("/api/v1/agent-hosts", json={
        "host": "u@h", "agent_type": "claude", "max_concurrent": 1,
    })
    host_id = r.json()["id"]
    r2 = await client.patch(f"/api/v1/agent-hosts/{host_id}", json={
        "max_concurrent": 8,
    })
    assert r2.status_code == 200
    assert r2.json()["max_concurrent"] == 8
    assert r2.json()["agent_type"] == "claude"  # unchanged


async def test_healthcheck_returns_status(client):
    r = await client.post("/api/v1/agent-hosts/local/healthcheck")
    assert r.status_code == 200
    body = r.json()
    assert body["health_status"] == "healthy"


async def test_healthcheck_records_unhealthy(client):
    # Manually create the bad host
    r = await client.post("/api/v1/agent-hosts", json={
        "id": "ah-bad", "host": "u@bad",
    })
    assert r.status_code == 201
    r2 = await client.post("/api/v1/agent-hosts/ah-bad/healthcheck")
    assert r2.status_code == 200
    assert r2.json()["health_status"] == "unhealthy"
    assert r2.json()["last_health_err"] == "ssh"


async def test_sync_endpoint_invokes_repo(client):
    r = await client.post("/api/v1/agent-hosts/sync")
    assert r.status_code == 200
    assert "upserted" in r.json()
