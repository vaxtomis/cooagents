"""Phase 8a: app lifespan invariants for the agent host registry."""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def configured_env(tmp_path, monkeypatch):
    # Required auth env (from src/auth.py contract).
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    # Pre-generated argon2 hash of "test-pw" (any valid argon2 string works).
    from argon2 import PasswordHasher
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", PasswordHasher().hash("test-pw"))
    monkeypatch.setenv("JWT_SECRET", "test-secret-min-32-characters-okay-for-tests")
    agent_token = "ag-" + ("x" * 32)
    monkeypatch.setenv("AGENT_API_TOKEN", agent_token)
    # Use tmp DB so we don't clobber dev .coop/state.db
    monkeypatch.setenv("COOAGENTS_TEST_TMP", str(tmp_path))
    yield agent_token


async def test_local_host_always_present(configured_env, monkeypatch, tmp_path):
    """Lifespan boot must leave the 'local' agent_host_id row in the DB."""
    # Patch settings to use tmp_path for DB + workspaces.
    from src.config import load_settings
    real = load_settings()
    real.database.path = str(tmp_path / "state.db")
    real.security.workspace_root = str(tmp_path / "ws")
    monkeypatch.setattr("src.app.load_settings", lambda: real)
    # Disable health probe sleep so we don't actually try to ssh anywhere.
    real.health_check.interval = 36000

    from src.app import app
    from src.repos import RepoFetcher, RepoHealthLoop, RepoRegistryRepo

    # Manually drive the lifespan since ASGITransport doesn't run it.
    async with app.router.lifespan_context(app):
        # repo-registry Phase 1: registry repo must be wired on app.state.
        assert isinstance(app.state.repo_registry_repo, RepoRegistryRepo)
        # repo-registry Phase 2: fetcher + health loop wired on app.state.
        assert isinstance(app.state.repo_fetcher, RepoFetcher)
        assert isinstance(app.state.repo_health_loop, RepoHealthLoop)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get(
                "/api/v1/agent-hosts",
                headers={"X-Agent-Token": configured_env},
            )
            assert r.status_code == 200, r.text
            ids = [h["id"] for h in r.json()]
            assert "local" in ids


async def test_lifespan_runs_orphan_sweep_at_boot(
    configured_env, monkeypatch, tmp_path,
):
    """Phase 9: lifespan reaps orphan acpx sessions before any SM dispatches."""
    from src.config import load_settings
    real = load_settings()
    real.database.path = str(tmp_path / "state.db")
    real.security.workspace_root = str(tmp_path / "ws")
    real.health_check.interval = 36000
    monkeypatch.setattr("src.app.load_settings", lambda: real)

    captured: dict = {}

    async def _fake_sweep(self, *, name_prefixes):
        captured["prefixes"] = name_prefixes
        return []

    monkeypatch.setattr(
        "src.llm_runner.LLMRunner.orphan_sweep_at_boot", _fake_sweep,
    )

    from src.app import app

    async with app.router.lifespan_context(app):
        pass

    assert captured.get("prefixes") == ("dw-", "design-")
