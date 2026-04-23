"""Route-level tests for dev_iteration_notes endpoints (Phase 5.5)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from routes.dev_iteration_notes import router as notes_router
from src.database import Database
from src.exceptions import BadRequestError, NotFoundError


def _now() -> str:
    return "2026-04-23T00:00:00+00:00"


@pytest.fixture
async def client(tmp_path):
    test_app = FastAPI(title="cooagents-test-iteration-notes")
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
        return JSONResponse(status_code=404, content={"message": str(exc)})

    @test_app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(status_code=400, content={"message": str(exc)})

    @test_app.exception_handler(HTTPException)
    async def _http(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})

    test_app.include_router(notes_router, prefix="/api/v1")

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        ac.ws_root = ws_root  # type: ignore[attr-defined]
        ac.db = db  # type: ignore[attr-defined]
        yield ac

    await db.close()


async def _seed_full(db: Database, ws_root: Path):
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("ws-aaa", "W", "demo", "active", str(Path.cwd()), _now(), _now()),
    )
    doc_path = ws_root / "demo" / "designs" / "DES-feat-1.0.0.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text("# d", encoding="utf-8")
    await db.execute(
        "INSERT INTO design_docs(id,workspace_id,slug,version,path,parent_version,"
        "needs_frontend_mockup,rubric_threshold,status,content_hash,byte_size,"
        "created_at,published_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("des-aaa", "ws-aaa", "feat", "1.0.0", str(doc_path), None, 0, 85,
         "published", None, 1, _now(), _now()),
    )
    await db.execute(
        "INSERT INTO dev_works(id,workspace_id,design_doc_id,repo_path,prompt,"
        "current_step,iteration_rounds,agent,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("dev-aaa", "ws-aaa", "des-aaa", str(Path.cwd()), "go",
         "STEP4_DEVELOP", 0, "claude", _now(), _now()),
    )


async def _seed_note(
    db: Database, ws_root: Path, *, note_id: str, dev_work_id: str = "dev-aaa",
    round_n: int = 1, write_file: bool = True, path_override: str | None = None,
    score_history: list[int] | None = None,
):
    target = (
        Path(path_override)
        if path_override is not None
        else ws_root / "demo" / "devworks" / dev_work_id
        / f"iteration-round-{round_n}.md"
    )
    if write_file:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# round {round_n}\n", encoding="utf-8")
    await db.execute(
        "INSERT INTO dev_iteration_notes(id,dev_work_id,round,markdown_path,"
        "score_history_json,created_at) VALUES(?,?,?,?,?,?)",
        (note_id, dev_work_id, round_n, str(target),
         json.dumps(score_history) if score_history is not None else None,
         _now()),
    )
    return target


async def test_list_returns_notes_ordered_asc(client):
    db = client.db
    ws_root = client.ws_root
    await _seed_full(db, ws_root)
    await _seed_note(db, ws_root, note_id="note-2", round_n=2)
    await _seed_note(db, ws_root, note_id="note-1", round_n=1)
    r = await client.get("/api/v1/dev-works/dev-aaa/iteration-notes")
    assert r.status_code == 200
    body = r.json()
    assert [n["id"] for n in body] == ["note-1", "note-2"]


async def test_list_unknown_devwork_404(client):
    r = await client.get("/api/v1/dev-works/dev-nope/iteration-notes")
    assert r.status_code == 404


async def test_list_empty_returns_200(client):
    db = client.db
    ws_root = client.ws_root
    await _seed_full(db, ws_root)
    r = await client.get("/api/v1/dev-works/dev-aaa/iteration-notes")
    assert r.status_code == 200
    assert r.json() == []


async def test_score_history_decoded(client):
    db = client.db
    ws_root = client.ws_root
    await _seed_full(db, ws_root)
    await _seed_note(db, ws_root, note_id="note-s", score_history=[80, 85, 90])
    r = await client.get("/api/v1/dev-works/dev-aaa/iteration-notes")
    assert r.status_code == 200
    assert r.json()[0]["score_history"] == [80, 85, 90]


async def test_content_returns_markdown(client):
    db = client.db
    ws_root = client.ws_root
    await _seed_full(db, ws_root)
    await _seed_note(db, ws_root, note_id="note-c")
    r = await client.get("/api/v1/dev-iteration-notes/note-c/content")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert b"round 1" in r.content


async def test_content_unknown_404(client):
    r = await client.get("/api/v1/dev-iteration-notes/note-nope/content")
    assert r.status_code == 404


async def test_content_path_escape_400(client):
    db = client.db
    ws_root = client.ws_root
    await _seed_full(db, ws_root)
    outside = (ws_root.parent / "outside-note.md").resolve()
    outside.write_text("# escape\n", encoding="utf-8")
    await _seed_note(
        db, ws_root, note_id="note-esc", write_file=False,
        path_override=str(outside),
    )
    r = await client.get("/api/v1/dev-iteration-notes/note-esc/content")
    assert r.status_code == 400


async def test_content_file_missing_410(client):
    db = client.db
    ws_root = client.ws_root
    await _seed_full(db, ws_root)
    missing = ws_root / "demo" / "devworks" / "dev-aaa" / "iteration-round-9.md"
    await _seed_note(
        db, ws_root, note_id="note-gone", round_n=9, write_file=False,
        path_override=str(missing),
    )
    r = await client.get("/api/v1/dev-iteration-notes/note-gone/content")
    assert r.status_code == 410
