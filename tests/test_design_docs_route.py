"""Route-level tests for /api/v1/design-docs (Phase 5.5)."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from routes.design_docs import router as docs_router
from src.database import Database
from src.exceptions import BadRequestError, NotFoundError


def _now() -> str:
    return "2026-04-23T00:00:00+00:00"


@pytest.fixture
async def client(tmp_path):
    test_app = FastAPI(title="cooagents-test-design-docs")
    ws_root = (tmp_path / "ws").resolve()
    ws_root.mkdir(parents=True, exist_ok=True)

    db = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await db.connect()

    test_app.state.db = db
    test_app.state.settings = SimpleNamespace(
        security=SimpleNamespace(resolved_workspace_root=lambda: ws_root)
    )
    test_app.state.start_time = time.time()

    @test_app.exception_handler(NotFoundError)
    async def _nf(request, exc):
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": str(exc)},
        )

    @test_app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "message": str(exc)},
        )

    @test_app.exception_handler(HTTPException)
    async def _http(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": "http_error", "message": exc.detail},
        )

    test_app.include_router(docs_router, prefix="/api/v1")

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        ac.ws_root = ws_root  # type: ignore[attr-defined]
        ac.db = db  # type: ignore[attr-defined]
        yield ac

    await db.close()


async def _seed_workspace(db: Database, ws_id: str = "ws-aaa", slug: str = "demo"):
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (ws_id, "Demo", slug, "active", str(Path.cwd()), _now(), _now()),
    )


async def _seed_doc(
    db: Database,
    ws_root: Path,
    *,
    doc_id: str,
    workspace_id: str = "ws-aaa",
    workspace_slug: str = "demo",
    slug: str = "feat",
    version: str = "1.0.0",
    status: str = "draft",
    write_file: bool = True,
    path_override: str | None = None,
    body: str = "# Hello\n",
):
    target = (
        Path(path_override)
        if path_override is not None
        else ws_root / workspace_slug / "designs" / f"DES-{slug}-{version}.md"
    )
    if write_file:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    await db.execute(
        "INSERT INTO design_docs(id,workspace_id,slug,version,path,parent_version,"
        "needs_frontend_mockup,rubric_threshold,status,content_hash,byte_size,"
        "created_at,published_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            doc_id,
            workspace_id,
            slug,
            version,
            str(target),
            None,
            0,
            85,
            status,
            None,
            len(body.encode("utf-8")),
            _now(),
            _now() if status == "published" else None,
        ),
    )
    return target


async def test_list_requires_workspace_id(client):
    r = await client.get("/api/v1/design-docs")
    assert r.status_code == 422


async def test_list_returns_rows_ordered_desc(client):
    db = client.db
    ws_root = client.ws_root
    await _seed_workspace(db)
    await _seed_doc(db, ws_root, doc_id="des-001", slug="a", version="1.0.0")
    # Slight delay so created_at differs (use raw INSERT with explicit ts)
    await db.execute(
        "UPDATE design_docs SET created_at=? WHERE id=?",
        ("2026-04-23T00:00:01+00:00", "des-001"),
    )
    await _seed_doc(
        db, ws_root, doc_id="des-002", slug="b", version="1.0.0",
        status="published",
    )
    await db.execute(
        "UPDATE design_docs SET created_at=? WHERE id=?",
        ("2026-04-23T00:00:02+00:00", "des-002"),
    )
    r = await client.get(
        "/api/v1/design-docs", params={"workspace_id": "ws-aaa"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [d["id"] for d in body] == ["des-002", "des-001"]


async def test_list_status_filter(client):
    db = client.db
    ws_root = client.ws_root
    await _seed_workspace(db)
    await _seed_doc(db, ws_root, doc_id="des-d", slug="d", version="1.0.0",
                    status="draft")
    await _seed_doc(db, ws_root, doc_id="des-p", slug="p", version="1.0.0",
                    status="published")
    r = await client.get(
        "/api/v1/design-docs",
        params={"workspace_id": "ws-aaa", "status": "published"},
    )
    assert r.status_code == 200
    body = r.json()
    assert [d["id"] for d in body] == ["des-p"]


async def test_list_status_invalid(client):
    db = client.db
    await _seed_workspace(db)
    r = await client.get(
        "/api/v1/design-docs",
        params={"workspace_id": "ws-aaa", "status": "invalid"},
    )
    assert r.status_code == 400


async def test_get_one_returns_row(client):
    db = client.db
    ws_root = client.ws_root
    await _seed_workspace(db)
    await _seed_doc(db, ws_root, doc_id="des-x")
    r = await client.get("/api/v1/design-docs/des-x")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "des-x"
    assert body["needs_frontend_mockup"] is False


async def test_get_one_unknown_404(client):
    r = await client.get("/api/v1/design-docs/des-missing")
    assert r.status_code == 404


async def test_content_returns_markdown(client):
    db = client.db
    ws_root = client.ws_root
    await _seed_workspace(db)
    await _seed_doc(db, ws_root, doc_id="des-c", body="# Title\n\nbody")
    r = await client.get("/api/v1/design-docs/des-c/content")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert b"# Title" in r.content


async def test_content_path_escape_400(client):
    db = client.db
    ws_root = client.ws_root
    await _seed_workspace(db)
    # Path that resolves outside workspaces_root
    outside = (ws_root.parent / "outside.md").resolve()
    outside.write_text("# escape\n", encoding="utf-8")
    await _seed_doc(
        db, ws_root, doc_id="des-esc",
        write_file=False, path_override=str(outside),
    )
    r = await client.get("/api/v1/design-docs/des-esc/content")
    assert r.status_code == 400


async def test_content_file_missing_410(client):
    db = client.db
    ws_root = client.ws_root
    await _seed_workspace(db)
    missing = ws_root / "demo" / "designs" / "DES-gone-1.0.0.md"
    await _seed_doc(
        db, ws_root, doc_id="des-gone", slug="gone", write_file=False,
        path_override=str(missing),
    )
    r = await client.get("/api/v1/design-docs/des-gone/content")
    assert r.status_code == 410


async def test_content_unknown_404(client):
    r = await client.get("/api/v1/design-docs/des-nope/content")
    assert r.status_code == 404
