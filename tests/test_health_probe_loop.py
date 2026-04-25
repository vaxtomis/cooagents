"""Unit tests for HealthProbeLoop (Phase 8a)."""
from __future__ import annotations

import asyncio

import pytest

from src.agent_hosts.health_probe import HealthProbeLoop
from src.agent_hosts.repo import AgentHostRepo
from src.database import Database


class FakeDispatcher:
    """Replays scripted healthcheck results per host id."""

    def __init__(self, script: dict[str, object]) -> None:
        # value can be a dict (returned) or an Exception (raised)
        self.script = script
        self.calls: list[str] = []

    async def healthcheck(self, host_id: str):
        self.calls.append(host_id)
        v = self.script.get(host_id)
        if isinstance(v, BaseException):
            raise v
        return v or {"health_status": "unknown", "last_health_err": None}


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    repo = AgentHostRepo(db)
    yield dict(db=db, repo=repo)
    await db.close()


async def test_probe_once_marks_healthy_and_unhealthy(env):
    repo = env["repo"]
    await repo.upsert(id="h-ok", host="u@a", agent_type="both")
    await repo.upsert(id="h-down", host="u@b", agent_type="both")
    dispatcher = FakeDispatcher({
        "h-ok": {"health_status": "healthy", "last_health_err": None},
        "h-down": {"health_status": "unhealthy", "last_health_err": "ssh"},
    })
    loop = HealthProbeLoop(dispatcher, repo, interval_s=60)
    await loop.probe_once()
    assert (await repo.get("h-ok"))["health_status"] == "healthy"
    assert (await repo.get("h-down"))["health_status"] == "unhealthy"


async def test_probe_once_continues_after_one_host_raises(env):
    repo = env["repo"]
    await repo.upsert(id="h-broken", host="u@a", agent_type="both")
    await repo.upsert(id="h-ok", host="u@b", agent_type="both")
    dispatcher = FakeDispatcher({
        "h-broken": RuntimeError("kaboom"),
        "h-ok": {"health_status": "healthy", "last_health_err": None},
    })
    loop = HealthProbeLoop(dispatcher, repo, interval_s=60)
    await loop.probe_once()
    # h-broken got recorded as unhealthy, h-ok still got probed and healthy.
    assert (await repo.get("h-broken"))["health_status"] == "unhealthy"
    assert (await repo.get("h-ok"))["health_status"] == "healthy"
    # Probes happen in id order; the schema also seeds 'local' so account for it.
    assert dispatcher.calls == ["h-broken", "h-ok", "local"]


async def test_start_stop_cancels_task_cleanly(env):
    repo = env["repo"]
    await repo.upsert(id="h", host="u@a", agent_type="both")
    dispatcher = FakeDispatcher({
        "h": {"health_status": "healthy", "last_health_err": None}
    })
    loop = HealthProbeLoop(dispatcher, repo, interval_s=10)
    loop.start()
    await asyncio.sleep(0.05)  # give the loop a chance to run probe_once once
    await loop.stop()
    assert loop._task is None
    # At least one probe occurred while the loop was alive.
    assert (await repo.get("h"))["health_status"] == "healthy"
