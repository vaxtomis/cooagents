"""Integration tests for POST /api/v1/repos/{id}/fetch (Phase 2)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from src.database import Database
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.repos.registry import RepoRegistryRepo


class _FakeFetcher:
    def __init__(self, behaviour: object = "fetched") -> None:
        self.behaviour = behaviour
        self.calls: list[str] = []

    def bare_path(self, repo_id: str) -> Path:
        return Path(f"/fake/bare/{repo_id}.git")

    async def fetch_or_clone(self, repo: dict[str, Any]) -> str:
        self.calls.append(repo["id"])
        if isinstance(self.behaviour, BaseException):
            raise self.behaviour
        return str(self.behaviour)


async def _build_app(
    tmp_path: Path, fetcher: _FakeFetcher,
) -> tuple[FastAPI, Database]:
    test_app = FastAPI(title="repos-route-test")
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()

    test_app.state.db = db
    test_app.state.repo_registry_repo = RepoRegistryRepo(db)
    test_app.state.repo_fetcher = fetcher

    @test_app.exception_handler(NotFoundError)
    async def _nf(request, exc):
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": str(exc)},
        )

    @test_app.exception_handler(ConflictError)
    async def _cf(request, exc):
        return JSONResponse(
            status_code=409,
            content={"error": "conflict", "message": str(exc)},
        )

    @test_app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "message": str(exc)},
        )

    from routes.repos import router as repos_router
    test_app.include_router(repos_router, prefix="/api/v1")
    return test_app, db


async def _seed(app: FastAPI, repo_id: str = "repo-aaa") -> None:
    await app.state.repo_registry_repo.upsert(
        id=repo_id,
        name="frontend",
        url="git@github.com:org/frontend.git",
    )


@pytest.fixture
async def fetched_client(tmp_path):
    fetcher = _FakeFetcher("fetched")
    app, db = await _build_app(tmp_path, fetcher)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac, app, fetcher
    await db.close()


async def test_fetch_endpoint_returns_outcome(fetched_client):
    client, app, fetcher = fetched_client
    await _seed(app)
    resp = await client.post("/api/v1/repos/repo-aaa/fetch")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "fetched"
    assert body["fetch_status"] == "healthy"
    assert fetcher.calls == ["repo-aaa"]


async def test_fetch_endpoint_404_for_missing(fetched_client):
    client, _, _ = fetched_client
    resp = await client.post("/api/v1/repos/repo-nope/fetch")
    assert resp.status_code == 404


async def test_fetch_endpoint_502_on_fetcher_exception(tmp_path):
    fetcher = _FakeFetcher(RuntimeError("auth failed"))
    app, db = await _build_app(tmp_path, fetcher)
    try:
        await _seed(app)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/v1/repos/repo-aaa/fetch")
            assert resp.status_code == 502
            assert "auth failed" in resp.json()["detail"]
        # Error was also recorded in the registry.
        row = await app.state.repo_registry_repo.get("repo-aaa")
        assert row["fetch_status"] == "error"
        assert "auth failed" in (row["last_fetch_err"] or "")
    finally:
        await db.close()
