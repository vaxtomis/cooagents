"""Unit tests for dispatch_decider.choose_host (Phase 8a)."""
from __future__ import annotations

import pytest

from src.agent_hosts.dispatch_decider import (
    choose_configured_host,
    choose_host,
    reset_counters,
    resolve_configured_agent,
)
from src.agent_hosts.repo import AgentHostRepo
from src.database import Database


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    repo = AgentHostRepo(db)
    reset_counters()
    yield repo
    reset_counters()
    await db.close()


async def test_no_hosts_returns_local_string(env):
    assert await choose_host(env, "claude") == "local"


async def test_only_unhealthy_returns_local(env):
    await env.upsert(id="h1", host="u@a", agent_type="both")
    # health stays 'unknown' — must NOT be picked
    assert await choose_host(env, "codex") == "local"


async def test_choose_configured_host_uses_configured_unhealthy_match(env):
    await env.upsert(id="h1", host="u@a", agent_type="codex")
    assert await choose_configured_host(env, "codex") == "h1"


async def test_choose_configured_host_does_not_use_incompatible_healthy_host(env):
    await env.upsert(id="h-claude", host="u@a", agent_type="claude")
    await env.update_health("h-claude", status="healthy")
    await env.upsert(id="h-codex", host="u@b", agent_type="codex")
    assert await choose_configured_host(env, "codex") == "h-codex"


async def test_picks_healthy_host(env):
    await env.upsert(id="h1", host="u@a", agent_type="codex")
    await env.update_health("h1", status="healthy")
    assert await choose_host(env, "codex") == "h1"


def test_resolve_configured_agent_falls_back_when_requested_unavailable():
    hosts = [{"agent_type": "codex", "health_status": "healthy", "labels": []}]
    assert resolve_configured_agent(hosts, "claude") == "codex"


def test_resolve_configured_agent_keeps_requested_when_configured():
    hosts = [{"agent_type": "both", "health_status": "unknown", "labels": []}]
    assert resolve_configured_agent(hosts, "claude") == "claude"


async def test_round_robin_across_two_hosts(env):
    for i in (1, 2):
        await env.upsert(id=f"h{i}", host=f"u@h{i}", agent_type="both")
        await env.update_health(f"h{i}", status="healthy")
    seen = [await choose_host(env, "claude") for _ in range(4)]
    # both hosts get hit twice in 4 picks
    assert sorted(seen) == ["h1", "h1", "h2", "h2"]


async def test_agent_type_filter(env):
    await env.upsert(id="h-claude", host="u@a", agent_type="claude")
    await env.upsert(id="h-codex", host="u@b", agent_type="codex")
    for hid in ("h-claude", "h-codex"):
        await env.update_health(hid, status="healthy")
    # Asking for codex never returns h-claude
    seen = {await choose_host(env, "codex") for _ in range(5)}
    assert seen == {"h-codex"}


async def test_both_type_matches_any(env):
    await env.upsert(id="h-both", host="u@a", agent_type="both")
    await env.update_health("h-both", status="healthy")
    assert await choose_host(env, "claude") == "h-both"
    assert await choose_host(env, "codex") == "h-both"


async def test_label_filter_subset(env):
    await env.upsert(id="h-fast", host="u@a", agent_type="both",
                     labels=["fast", "gpu"])
    await env.upsert(id="h-plain", host="u@b", agent_type="both", labels=[])
    for hid in ("h-fast", "h-plain"):
        await env.update_health(hid, status="healthy")
    # Required label "fast" excludes the plain host
    seen = {await choose_host(env, "claude", labels=["fast"]) for _ in range(5)}
    assert seen == {"h-fast"}
