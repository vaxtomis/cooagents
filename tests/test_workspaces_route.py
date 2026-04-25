"""Route-level tests for /api/v1/workspaces (Phase 2).

Uses a lightweight per-test FastAPI app (same pattern as tests/test_api.py)
instead of driving the full production lifespan. Keeps tests fast and
deterministic.
"""
import shutil
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from src.database import Database
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.storage import LocalFileStore
from src.storage.registry import WorkspaceFileRegistry, WorkspaceFilesRepo
from src.workspace_manager import WorkspaceManager


@pytest.fixture
async def client(tmp_path):
    test_app = FastAPI(title="cooagents-test-workspaces")
    ws_root = tmp_path / "ws"

    db = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await db.connect()

    ws_root.mkdir(exist_ok=True)
    store = LocalFileStore(workspaces_root=ws_root)
    repo = WorkspaceFilesRepo(db)
    registry = WorkspaceFileRegistry(store=store, repo=repo)
    workspaces = WorkspaceManager(
        db, project_root=tmp_path, workspaces_root=ws_root, registry=registry,
    )
    test_app.state.db = db
    test_app.state.workspaces = workspaces
    test_app.state.start_time = time.time()

    # slowapi limiter requires app.state.limiter when @limiter.limit is used.
    from slowapi import Limiter
    from src.request_utils import client_ip
    limiter = Limiter(key_func=client_ip, default_limits=["1000/minute"])
    test_app.state.limiter = limiter
    # Disable to avoid 429 in tight test loops.
    limiter.enabled = False

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
            content={
                "error": "conflict",
                "message": str(exc),
                "current_stage": exc.current_stage,
            },
        )

    @test_app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "message": str(exc)},
        )

    from routes.workspaces import router as workspaces_router
    test_app.include_router(workspaces_router, prefix="/api/v1")

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        ac._ws_root = ws_root  # stash for tests
        yield ac

    await db.close()


async def test_create_and_get(client, tmp_path):
    r = await client.post(
        "/api/v1/workspaces", json={"title": "First", "slug": "first"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "first"
    assert body["id"].startswith("ws-")
    assert (tmp_path / "ws" / "first" / "workspace.md").exists()

    r2 = await client.get(f"/api/v1/workspaces/{body['id']}")
    assert r2.status_code == 200
    assert r2.json()["title"] == "First"


async def test_create_invalid_slug_returns_422(client):
    r = await client.post(
        "/api/v1/workspaces", json={"title": "X", "slug": "Bad Slug"}
    )
    # Pydantic field_validator -> 422 Unprocessable Entity
    assert r.status_code in (400, 422)


async def test_create_duplicate_slug_returns_409(client):
    r1 = await client.post(
        "/api/v1/workspaces", json={"title": "A", "slug": "dup"}
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/api/v1/workspaces", json={"title": "B", "slug": "dup"}
    )
    assert r2.status_code == 409


async def test_list_filters_by_status(client):
    await client.post(
        "/api/v1/workspaces", json={"title": "A", "slug": "aa"}
    )
    r = await client.post(
        "/api/v1/workspaces", json={"title": "B", "slug": "bb"}
    )
    bid = r.json()["id"]
    await client.delete(f"/api/v1/workspaces/{bid}")

    active = await client.get("/api/v1/workspaces?status=active")
    archived = await client.get("/api/v1/workspaces?status=archived")
    assert {w["slug"] for w in active.json()} == {"aa"}
    assert {w["slug"] for w in archived.json()} == {"bb"}


async def test_list_invalid_status_returns_400(client):
    r = await client.get("/api/v1/workspaces?status=garbage")
    assert r.status_code == 400


async def test_delete_archives_and_is_idempotent(client, tmp_path):
    r = await client.post(
        "/api/v1/workspaces", json={"title": "Z", "slug": "z"}
    )
    wid = r.json()["id"]

    r1 = await client.delete(f"/api/v1/workspaces/{wid}")
    assert r1.status_code == 204

    md = (tmp_path / "ws" / "z" / "workspace.md").read_text(encoding="utf-8")
    assert "status: archived" in md

    # Second delete is idempotent (archive_with_scaffold returns False; 204)
    r2 = await client.delete(f"/api/v1/workspaces/{wid}")
    assert r2.status_code == 204


async def test_get_missing_returns_404(client):
    r = await client.get("/api/v1/workspaces/ws-nope")
    assert r.status_code == 404


async def test_delete_missing_returns_404(client):
    r = await client.delete("/api/v1/workspaces/ws-nope")
    assert r.status_code == 404


async def test_materialize_route_is_deleted(client):
    r = await client.post(
        "/api/v1/workspaces", json={"title": "M", "slug": "mat"}
    )
    wid = r.json()["id"]
    resp = await client.post(f"/api/v1/workspaces/{wid}/materialize")
    assert resp.status_code == 404


async def test_regenerate_index_route_is_deleted(client):
    r = await client.post(
        "/api/v1/workspaces", json={"title": "G", "slug": "regen"}
    )
    wid = r.json()["id"]
    resp = await client.post(f"/api/v1/workspaces/{wid}/regenerate-index")
    assert resp.status_code == 404


async def test_sync_reports_drift(client, tmp_path):
    await client.post(
        "/api/v1/workspaces", json={"title": "A", "slug": "sa"}
    )
    await client.post(
        "/api/v1/workspaces", json={"title": "B", "slug": "sb"}
    )
    shutil.rmtree(tmp_path / "ws" / "sb")
    r = await client.post("/api/v1/workspaces/sync")
    assert r.status_code == 200
    report = r.json()
    # The drifted workspace ('sb') must appear in db_only
    assert len(report["db_only"]) >= 1
