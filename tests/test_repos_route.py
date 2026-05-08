"""Integration tests for /api/v1/repos/* (Phase 1 + Phase 2 + Phase 3)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from src.config import RepoConfig, ReposConfig
from src.database import Database
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.models import (
    RepoBlob,
    RepoBranches,
    RepoLog,
    RepoLogEntry,
    RepoTree,
    RepoTreeEntry,
)
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


class _FakeInspector:
    """Scriptable peer to RepoInspector for route tests."""

    def __init__(self) -> None:
        self.branches_result: object = RepoBranches(
            default_branch="main", branches=["main", "dev"],
        )
        self.tree_result: object = RepoTree(
            ref="main", path="",
            entries=[RepoTreeEntry(
                path="README.md", type="blob", mode="100644", size=12,
            )],
            truncated=False,
        )
        self.blob_result: object = RepoBlob(
            ref="main", path="README.md", size=12, binary=False,
            content="hello world\n",
        )
        self.log_result: object = RepoLog(
            ref="main", path=None,
            entries=[RepoLogEntry(
                sha="a" * 40, author="t", email="t@example.com",
                committed_at="2026-04-26T00:00:00+00:00", subject="init",
            )],
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def _maybe(self, name: str, value: object, **kwargs: Any) -> object:
        self.calls.append((name, kwargs))
        if isinstance(value, BaseException):
            raise value
        return value

    async def branches(self, repo_id: str):
        return await self._maybe("branches", self.branches_result, repo_id=repo_id)

    async def tree(self, repo_id: str, **kwargs: Any):
        return await self._maybe("tree", self.tree_result, repo_id=repo_id, **kwargs)

    async def blob(self, repo_id: str, **kwargs: Any):
        return await self._maybe("blob", self.blob_result, repo_id=repo_id, **kwargs)

    async def log(self, repo_id: str, **kwargs: Any):
        return await self._maybe("log", self.log_result, repo_id=repo_id, **kwargs)

    async def log_count(self, repo_id: str, **kwargs: Any):
        self.calls.append(("log_count", {"repo_id": repo_id, **kwargs}))
        if isinstance(self.log_result, BaseException):
            raise self.log_result
        return len(self.log_result.entries)


async def _build_app(
    tmp_path: Path,
    fetcher: _FakeFetcher,
    inspector: _FakeInspector | None = None,
    repos_config: ReposConfig | None = None,
) -> tuple[FastAPI, Database]:
    test_app = FastAPI(title="repos-route-test")
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()

    test_app.state.db = db
    test_app.state.repo_registry_repo = RepoRegistryRepo(db)
    test_app.state.repo_fetcher = fetcher
    test_app.state.repo_inspector = inspector or _FakeInspector()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    # Settings shim: repo CRUD reads security.workspace_root; sync reads repos.
    test_app.state.settings = type(
        "S",
        (),
        {
            "repos": repos_config or ReposConfig(),
            "security": type(
                "Sec",
                (),
                {"resolved_workspace_root": lambda self: workspace_root},
            )(),
        },
    )()

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
    inspector = _FakeInspector()
    app, db = await _build_app(tmp_path, fetcher, inspector=inspector)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac, app, fetcher, inspector
    await db.close()


# Phase 2: fetch endpoint -----------------------------------------------------

async def test_fetch_endpoint_returns_outcome(fetched_client):
    client, app, fetcher, _ = fetched_client
    await _seed(app)
    resp = await client.post("/api/v1/repos/repo-aaa/fetch")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"] == "fetched"
    assert body["fetch_status"] == "healthy"
    assert fetcher.calls == ["repo-aaa"]


async def test_fetch_endpoint_404_for_missing(fetched_client):
    client, _, _, _ = fetched_client
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
        row = await app.state.repo_registry_repo.get("repo-aaa")
        assert row["fetch_status"] == "error"
        assert "auth failed" in (row["last_fetch_err"] or "")
    finally:
        await db.close()


# Phase 3: CRUD ---------------------------------------------------------------

async def test_list_repos_empty_then_one(fetched_client):
    client, app, _, _ = fetched_client
    resp = await client.get("/api/v1/repos")
    assert resp.status_code == 200
    assert resp.json() == []
    await _seed(app)
    resp = await client.get("/api/v1/repos")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "repo-aaa"


async def test_list_repos_paginated_envelope(fetched_client):
    client, app, _, _ = fetched_client
    await _seed(app)
    await app.state.repo_registry_repo.upsert(
        id="repo-bbb",
        name="backend",
        url="git@github.com:org/backend.git",
        role="backend",
    )
    resp = await client.get(
        "/api/v1/repos",
        params={"paginate": True, "limit": 1, "offset": 0, "sort": "name_asc"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"] == {
        "limit": 1,
        "offset": 0,
        "total": 2,
        "has_more": True,
    }
    assert [row["name"] for row in body["items"]] == ["backend"]


async def test_get_repo_404(fetched_client):
    client, _, _, _ = fetched_client
    resp = await client.get("/api/v1/repos/repo-nope")
    assert resp.status_code == 404


async def test_get_repo_returns_row(fetched_client):
    client, app, _, _ = fetched_client
    await _seed(app)
    resp = await client.get("/api/v1/repos/repo-aaa")
    assert resp.status_code == 200
    assert resp.json()["name"] == "frontend"


async def test_create_repo_returns_location_header(fetched_client):
    client, _, _, _ = fetched_client
    resp = await client.post(
        "/api/v1/repos",
        json={"name": "smoke", "url": "git@github.com:org/smoke.git"},
    )
    assert resp.status_code == 201, resp.text
    location = resp.headers.get("location")
    assert location and location.startswith("/api/v1/repos/repo-")
    body = resp.json()
    assert body["name"] == "smoke"
    assert body["fetch_status"] == "unknown"


async def test_create_repo_accepts_local_path_metadata(fetched_client):
    client, app, _, _ = fetched_client
    root = app.state.settings.security.resolved_workspace_root()
    local_path = root / "repos" / "frontend"
    resp = await client.post(
        "/api/v1/repos",
        json={
            "name": "frontend-local",
            "url": "git@github.com:org/frontend.git",
            "local_path": str(local_path),
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["local_path"] == str(local_path.resolve())
    assert not local_path.exists()


async def test_create_repo_rejects_duplicate_local_path(fetched_client):
    client, app, _, _ = fetched_client
    root = app.state.settings.security.resolved_workspace_root()
    local_path = root / "repos" / "shared"
    first = await client.post(
        "/api/v1/repos",
        json={
            "name": "frontend-local",
            "url": "git@github.com:org/frontend.git",
            "local_path": str(local_path),
        },
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        "/api/v1/repos",
        json={
            "name": "backend-local",
            "url": "git@github.com:org/backend.git",
            "local_path": str(local_path),
        },
    )
    assert second.status_code == 400
    assert "local_path" in second.json()["message"]


async def test_create_repo_rejects_local_path_outside_workspace(fetched_client):
    client, app, _, _ = fetched_client
    root = app.state.settings.security.resolved_workspace_root()
    outside = root.parent / "outside"
    resp = await client.post(
        "/api/v1/repos",
        json={
            "name": "outside-local",
            "url": "git@github.com:org/outside.git",
            "local_path": str(outside),
        },
    )
    assert resp.status_code == 400
    assert "workspace_root" in resp.json()["message"]


async def test_create_repo_with_explicit_id(fetched_client):
    client, _, _, _ = fetched_client
    resp = await client.post(
        "/api/v1/repos",
        json={"id": "repo-fixed", "name": "smoke2", "url": "git@github.com:org/smoke2.git"},
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == "repo-fixed"


async def test_create_repo_rejects_duplicate_id(fetched_client):
    client, app, _, _ = fetched_client
    await _seed(app)
    resp = await client.post(
        "/api/v1/repos",
        json={"id": "repo-aaa", "name": "other", "url": "git@github.com:org/other.git"},
    )
    assert resp.status_code == 400
    assert "id" in resp.json()["message"].lower()


async def test_create_repo_rejects_duplicate_name(fetched_client):
    client, app, _, _ = fetched_client
    await _seed(app)
    resp = await client.post(
        "/api/v1/repos",
        json={"name": "frontend", "url": "git@github.com:org/dup.git"},
    )
    assert resp.status_code == 400
    assert "name" in resp.json()["message"].lower()


async def test_create_repo_rejects_invalid_name(fetched_client):
    client, _, _, _ = fetched_client
    resp = await client.post(
        "/api/v1/repos",
        json={"name": "../evil", "url": "git@github.com:org/evil.git"},
    )
    assert resp.status_code == 422  # pydantic


async def test_patch_repo_partial(fetched_client):
    client, app, _, _ = fetched_client
    await _seed(app)
    resp = await client.patch(
        "/api/v1/repos/repo-aaa",
        json={"default_branch": "develop"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_branch"] == "develop"
    assert body["name"] == "frontend"  # untouched


async def test_patch_repo_replaces_local_path(fetched_client):
    client, app, _, _ = fetched_client
    await _seed(app)
    root = app.state.settings.security.resolved_workspace_root()
    local_path = root / "repos" / "frontend"
    resp = await client.patch(
        "/api/v1/repos/repo-aaa",
        json={"local_path": str(local_path)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["local_path"] == str(local_path.resolve())


async def test_patch_repo_allows_clearing_local_path(fetched_client):
    client, app, _, _ = fetched_client
    root = app.state.settings.security.resolved_workspace_root()
    local_path = root / "repos" / "frontend"
    await app.state.repo_registry_repo.upsert(
        id="repo-aaa",
        name="frontend",
        url="git@github.com:org/frontend.git",
        local_path=str(local_path.resolve()),
    )

    resp = await client.patch(
        "/api/v1/repos/repo-aaa",
        json={"local_path": None},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["local_path"] is None


async def test_patch_repo_rejects_local_path_clash(fetched_client):
    client, app, _, _ = fetched_client
    root = app.state.settings.security.resolved_workspace_root()
    local_path = root / "repos" / "shared"
    await app.state.repo_registry_repo.upsert(
        id="repo-aaa",
        name="frontend",
        url="git@github.com:org/frontend.git",
        local_path=str(local_path.resolve()),
    )
    await app.state.repo_registry_repo.upsert(
        id="repo-bbb",
        name="backend",
        url="git@github.com:org/backend.git",
    )
    resp = await client.patch(
        "/api/v1/repos/repo-bbb",
        json={"local_path": str(local_path)},
    )
    assert resp.status_code == 400
    assert "local_path" in resp.json()["message"]


async def test_patch_repo_404(fetched_client):
    client, _, _, _ = fetched_client
    resp = await client.patch(
        "/api/v1/repos/repo-nope", json={"url": "git@github.com:org/x.git"},
    )
    assert resp.status_code == 404


async def test_patch_repo_rejects_name_clash(fetched_client):
    client, app, _, _ = fetched_client
    await _seed(app, repo_id="repo-aaa")
    await app.state.repo_registry_repo.upsert(
        id="repo-bbb", name="backend",
        url="git@github.com:org/backend.git",
    )
    resp = await client.patch(
        "/api/v1/repos/repo-bbb",
        json={"name": "frontend"},  # clashes with repo-aaa
    )
    assert resp.status_code == 400


async def test_delete_repo_204(fetched_client):
    client, app, _, _ = fetched_client
    await _seed(app)
    resp = await client.delete("/api/v1/repos/repo-aaa")
    assert resp.status_code == 204
    assert await app.state.repo_registry_repo.get("repo-aaa") is None


async def test_delete_repo_404(fetched_client):
    client, _, _, _ = fetched_client
    resp = await client.delete("/api/v1/repos/repo-nope")
    assert resp.status_code == 404


async def test_delete_repo_409_when_referenced(fetched_client):
    client, app, _, _ = fetched_client
    await _seed(app)
    # Hand-seed a dev_work_repos row referencing the repo. The registry
    # delete() runs a defensive COUNT(*) check before issuing the DDL,
    # so we only need a row to be visible. Suspend FK enforcement so
    # we don't have to materialise an entire dev_works/workspaces graph.
    db = app.state.db
    await db.execute("PRAGMA foreign_keys = OFF")
    try:
        await db.execute(
            "INSERT INTO dev_work_repos("
            "dev_work_id, repo_id, mount_name, base_branch, devwork_branch, "
            "created_at, updated_at"
            ") VALUES(?, ?, ?, ?, ?, ?, ?)",
            ("dw-fake", "repo-aaa", "mount", "main", "feat/x",
             "2026-04-26", "2026-04-26"),
        )
        resp = await client.delete("/api/v1/repos/repo-aaa")
    finally:
        await db.execute("PRAGMA foreign_keys = ON")
    assert resp.status_code == 409


async def test_sync_calls_registry(tmp_path):
    fetcher = _FakeFetcher("fetched")
    repos_config = ReposConfig(
        repos=[RepoConfig(name="cfg", url="git@example:org/cfg.git")],
    )
    app, db = await _build_app(tmp_path, fetcher, repos_config=repos_config)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/v1/repos/sync")
        assert resp.status_code == 200
        body = resp.json()
        assert "upserted" in body and "marked_unknown" in body
        assert len(body["upserted"]) == 1
    finally:
        await db.close()


# Phase 3: inspector ----------------------------------------------------------

async def test_branches_route(fetched_client):
    client, _, _, inspector = fetched_client
    resp = await client.get("/api/v1/repos/repo-aaa/branches")
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_branch"] == "main"
    assert body["branches"] == ["main", "dev"]
    assert inspector.calls[0][0] == "branches"


async def test_branches_route_404(fetched_client):
    client, _, _, inspector = fetched_client
    inspector.branches_result = NotFoundError("repo not found: 'x'")
    resp = await client.get("/api/v1/repos/repo-x/branches")
    assert resp.status_code == 404


async def test_branches_route_409_when_no_bare(fetched_client):
    client, _, _, inspector = fetched_client
    inspector.branches_result = ConflictError("no bare clone")
    resp = await client.get("/api/v1/repos/repo-aaa/branches")
    assert resp.status_code == 409


async def test_tree_route(fetched_client):
    client, _, _, inspector = fetched_client
    resp = await client.get(
        "/api/v1/repos/repo-aaa/tree", params={"ref": "main", "path": ""},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ref"] == "main"
    assert body["entries"][0]["path"] == "README.md"
    # Default depth/max_entries should NOT be passed when client omits them.
    call_kwargs = inspector.calls[0][1]
    assert "depth" not in call_kwargs
    assert "max_entries" not in call_kwargs


async def test_tree_route_invalid_depth_passes_through(fetched_client):
    """depth=0 reaches the inspector; the inspector clamps. The route must
    not pre-validate."""
    client, _, _, inspector = fetched_client
    resp = await client.get(
        "/api/v1/repos/repo-aaa/tree",
        params={"ref": "main", "path": "", "depth": 0},
    )
    assert resp.status_code == 200
    assert inspector.calls[0][1]["depth"] == 0


async def test_blob_route(fetched_client):
    client, _, _, _ = fetched_client
    resp = await client.get(
        "/api/v1/repos/repo-aaa/blob",
        params={"ref": "main", "path": "README.md"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["binary"] is False
    assert body["content"] == "hello world\n"


async def test_blob_route_oversize_returns_400(fetched_client):
    client, _, _, inspector = fetched_client
    inspector.blob_result = BadRequestError(
        "blob exceeds 1048576 byte cap"
    )
    resp = await client.get(
        "/api/v1/repos/repo-aaa/blob",
        params={"ref": "main", "path": "BIG.bin"},
    )
    assert resp.status_code == 400
    assert "cap" in resp.json()["message"]


async def test_log_route(fetched_client):
    client, _, _, inspector = fetched_client
    resp = await client.get(
        "/api/v1/repos/repo-aaa/log",
        params={"ref": "main", "limit": 2},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entries"]) == 1  # whatever fake returned
    assert inspector.calls[0][1]["limit"] == 2


async def test_log_route_paginated_envelope(fetched_client):
    client, _, _, inspector = fetched_client
    resp = await client.get(
        "/api/v1/repos/repo-aaa/log",
        params={"ref": "main", "limit": 1, "offset": 0, "paginate": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["limit"] == 1
    assert body["pagination"]["offset"] == 0
    assert body["pagination"]["total"] == 1
    assert body["pagination"]["has_more"] is False
    assert len(body["items"]) == 1


async def test_inspector_404_when_repo_unknown_route(fetched_client):
    client, _, _, inspector = fetched_client
    inspector.tree_result = NotFoundError("repo not found: 'repo-x'")
    resp = await client.get(
        "/api/v1/repos/repo-x/tree", params={"ref": "main"},
    )
    assert resp.status_code == 404
