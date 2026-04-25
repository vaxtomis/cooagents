"""Unit tests for RepoHealthLoop (Phase 2, repo-registry)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from src.database import Database
from src.repos.health_loop import RepoHealthLoop
from src.repos.registry import RepoRegistryRepo


class FakeFetcher:
    """Replays scripted fetch_or_clone outcomes per repo id.

    Implements the minimum surface the loop relies on:
    ``fetch_or_clone(repo)`` and ``bare_path(repo_id)``.
    """

    def __init__(self, script: dict[str, object]) -> None:
        self.script = script
        self.calls: list[str] = []

    def bare_path(self, repo_id: str) -> Path:
        return Path(f"/fake/bare/{repo_id}.git")

    async def fetch_or_clone(self, repo: dict[str, Any]) -> str:
        self.calls.append(repo["id"])
        v = self.script.get(repo["id"])
        if isinstance(v, BaseException):
            raise v
        return str(v) if v else "fetched"


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    registry = RepoRegistryRepo(db)
    yield {"db": db, "registry": registry}
    await db.close()


async def _seed(registry: RepoRegistryRepo, repo_id: str, name: str | None = None) -> dict:
    return await registry.upsert(
        id=repo_id,
        name=name or repo_id,
        url=f"git@example:org/{repo_id}.git",
    )


async def test_probe_once_marks_healthy_and_error(env):
    await _seed(env["registry"], "repo-ok")
    await _seed(env["registry"], "repo-bad", name="bad")
    fetcher = FakeFetcher({
        "repo-ok": "fetched",
        "repo-bad": RuntimeError("auth failed"),
    })
    loop = RepoHealthLoop(
        fetcher, env["registry"], interval_s=300, parallel=4,
    )
    await loop.probe_once()

    ok = await env["registry"].get("repo-ok")
    bad = await env["registry"].get("repo-bad")
    # Loop is the only writer — healthy on success, error on exception.
    assert ok["fetch_status"] == "healthy"
    assert ok["bare_clone_path"] is not None
    assert bad["fetch_status"] == "error"
    assert "auth failed" in (bad["last_fetch_err"] or "")


async def test_probe_once_continues_after_one_repo_raises(env):
    await _seed(env["registry"], "repo-bad", name="bad")
    await _seed(env["registry"], "repo-ok", name="ok")
    fetcher = FakeFetcher({
        "repo-bad": RuntimeError("kaboom"),
        "repo-ok": "fetched",
    })
    loop = RepoHealthLoop(
        fetcher, env["registry"], interval_s=300, parallel=4,
    )
    await loop.probe_once()
    # Both attempted; list_all() returns rows ORDER BY name.
    assert sorted(fetcher.calls) == ["repo-bad", "repo-ok"]


async def test_stale_promotion(env):
    await _seed(env["registry"], "repo-x", name="x")
    # Stamp a fresh healthy timestamp, then move it backwards beyond cutoff.
    await env["registry"].update_fetch_status("repo-x", status="healthy")
    far_past = (
        datetime.now(timezone.utc) - timedelta(seconds=3600)
    ).isoformat()
    await env["db"].execute(
        "UPDATE repos SET last_fetched_at=? WHERE id=?",
        (far_past, "repo-x"),
    )

    loop = RepoHealthLoop(
        FakeFetcher({}), env["registry"], interval_s=300, parallel=4,
    )
    rows = await env["registry"].list_all()
    await loop._mark_stale(rows)
    row = await env["registry"].get("repo-x")
    assert row["fetch_status"] == "stale"
    # Marking stale must NOT refresh ``last_fetched_at`` — staleness means
    # "the row is old"; refreshing the timestamp would contradict the
    # marker and hide a paused loop from operators.
    assert row["last_fetched_at"] == far_past


async def test_start_stop_cancels_task_cleanly(env):
    await _seed(env["registry"], "repo-a", name="a")
    fetcher = FakeFetcher({"repo-a": "fetched"})
    loop = RepoHealthLoop(
        fetcher, env["registry"], interval_s=10, parallel=2,
    )
    loop.start()
    await asyncio.sleep(0.05)
    await loop.stop()
    assert loop._task is None


async def test_parallel_cap_honoured(env):
    for i in range(5):
        await _seed(env["registry"], f"repo-{i}", name=f"r{i}")

    in_flight = 0
    high_water = 0

    class Counting(FakeFetcher):
        async def fetch_or_clone(self, repo):
            nonlocal in_flight, high_water
            in_flight += 1
            high_water = max(high_water, in_flight)
            await asyncio.sleep(0)  # yield to let others queue
            in_flight -= 1
            return "fetched"

    loop = RepoHealthLoop(
        Counting({}), env["registry"], interval_s=10, parallel=2,
    )
    await loop.probe_once()
    assert high_water <= 2
