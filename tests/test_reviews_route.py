"""Route-level tests for /api/v1/reviews (Phase 5.5)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from routes.reviews import router as reviews_router
from src.database import Database
from src.exceptions import BadRequestError, NotFoundError


def _now(suffix: str = "00:00:00") -> str:
    return f"2026-04-23T{suffix}+00:00"


@pytest.fixture
async def client(tmp_path):
    test_app = FastAPI(title="cooagents-test-reviews")

    db = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await db.connect()
    test_app.state.db = db
    test_app.state.start_time = time.time()

    @test_app.exception_handler(NotFoundError)
    async def _nf(request, exc):
        return JSONResponse(status_code=404, content={"message": str(exc)})

    @test_app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(status_code=400, content={"message": str(exc)})

    test_app.include_router(reviews_router, prefix="/api/v1")

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        ac.db = db  # type: ignore[attr-defined]
        yield ac

    await db.close()


async def _seed(db: Database):
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("ws-aaa", "W", "demo", "active", str(Path.cwd()), _now(), _now()),
    )
    await db.execute(
        "INSERT INTO design_docs(id,workspace_id,slug,version,path,parent_version,"
        "needs_frontend_mockup,rubric_threshold,status,content_hash,byte_size,"
        "created_at,published_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("des-aaa", "ws-aaa", "feat", "1.0.0", "/tmp/x.md", None, 0, 85,
         "published", None, 1, _now(), _now()),
    )
    await db.execute(
        "INSERT INTO dev_works(id,workspace_id,design_doc_id,prompt,"
        "current_step,iteration_rounds,agent,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("dev-aaa", "ws-aaa", "des-aaa", "go",
         "STEP4_DEVELOP", 0, "claude", _now(), _now()),
    )
    await db.execute(
        "INSERT INTO design_works(id,workspace_id,mode,current_state,loop,agent,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        ("desw-aaa", "ws-aaa", "new", "COMPLETED", 0, "claude", _now(), _now()),
    )
    # Two dev-side reviews
    await db.execute(
        "INSERT INTO reviews(id,dev_work_id,design_work_id,dev_iteration_note_id,"
        "round,score,issues_json,findings_json,problem_category,reviewer,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("rev-d1", "dev-aaa", None, None, 1, 70,
         json.dumps([{"k": "v"}]), None, None, "claude", _now("00:00:01")),
    )
    await db.execute(
        "INSERT INTO reviews(id,dev_work_id,design_work_id,dev_iteration_note_id,"
        "round,score,issues_json,findings_json,problem_category,reviewer,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("rev-d2", "dev-aaa", None, None, 1, 80,
         None, json.dumps([{"f": 1}]), None, "claude", _now("00:00:02")),
    )
    # Two design-side reviews
    await db.execute(
        "INSERT INTO reviews(id,dev_work_id,design_work_id,dev_iteration_note_id,"
        "round,score,issues_json,findings_json,problem_category,reviewer,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("rev-w1", None, "desw-aaa", None, 1, 90,
         None, None, None, "claude", _now("00:00:03")),
    )
    await db.execute(
        "INSERT INTO reviews(id,dev_work_id,design_work_id,dev_iteration_note_id,"
        "round,score,issues_json,findings_json,problem_category,reviewer,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("rev-w2", None, "desw-aaa", None, 2, 95,
         None, None, None, "claude", _now("00:00:04")),
    )


async def test_list_by_dev_work(client):
    await _seed(client.db)
    r = await client.get("/api/v1/reviews", params={"dev_work_id": "dev-aaa"})
    assert r.status_code == 200
    body = r.json()
    assert [d["id"] for d in body] == ["rev-d1", "rev-d2"]


async def test_list_by_design_work(client):
    await _seed(client.db)
    r = await client.get(
        "/api/v1/reviews", params={"design_work_id": "desw-aaa"}
    )
    assert r.status_code == 200
    body = r.json()
    assert [d["id"] for d in body] == ["rev-w1", "rev-w2"]


async def test_both_filters_400(client):
    r = await client.get(
        "/api/v1/reviews",
        params={"dev_work_id": "dev-X", "design_work_id": "desw-Y"},
    )
    assert r.status_code == 400


async def test_neither_filter_400(client):
    r = await client.get("/api/v1/reviews")
    assert r.status_code == 400


async def test_empty_dev_work_id_400(client):
    r = await client.get("/api/v1/reviews", params={"dev_work_id": ""})
    assert r.status_code == 400


async def test_issues_findings_decoded(client):
    await _seed(client.db)
    r = await client.get("/api/v1/reviews", params={"dev_work_id": "dev-aaa"})
    body = r.json()
    assert body[0]["issues"] == [{"k": "v"}]
    assert body[0]["findings"] is None
    assert body[1]["findings"] == [{"f": 1}]


async def test_empty_design_work_id_400(client):
    r = await client.get("/api/v1/reviews", params={"design_work_id": ""})
    assert r.status_code == 400


async def test_malformed_issues_json_returns_none(client):
    db = client.db
    await _seed(db)
    # Inject a row with malformed issues_json + non-list findings_json
    await db.execute(
        "INSERT INTO reviews(id,dev_work_id,design_work_id,dev_iteration_note_id,"
        "round,score,issues_json,findings_json,problem_category,reviewer,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("rev-bad", None, "desw-aaa", None, 3, 50,
         "{not json", json.dumps({"not": "list"}), None, "claude", _now("00:00:99")),
    )
    r = await client.get(
        "/api/v1/reviews", params={"design_work_id": "desw-aaa"}
    )
    body = r.json()
    bad = next(d for d in body if d["id"] == "rev-bad")
    assert bad["issues"] is None
    assert bad["findings"] is None


async def test_ordering_round_then_created_at(client):
    await _seed(client.db)
    r = await client.get(
        "/api/v1/reviews", params={"design_work_id": "desw-aaa"}
    )
    body = r.json()
    # round 1 row before round 2 row
    assert body[0]["round"] == 1
    assert body[1]["round"] == 2
