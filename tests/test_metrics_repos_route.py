"""Phase 9: /api/v1/metrics/repos route tests.

Mirrors tests/test_metrics_route.py shape — per-test FastAPI app, direct
SELECT seeding, three-case structure (empty / aggregate / window).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from routes.metrics_repos import router as metrics_repos_router
from src.database import Database
from src.exceptions import BadRequestError, NotFoundError


def _ts(month: int, day: int = 1, hour: int = 0) -> str:
    return f"2026-{month:02d}-{day:02d}T{hour:02d}:00:00+00:00"


async def _seed_repo_row(
    db: Database,
    *,
    id: str,
    name: str,
    fetch_status: str,
    created_at: str | None = None,
) -> None:
    created = created_at or _ts(4, 1)
    await db.execute(
        "INSERT INTO repos(id,name,url,default_branch,role,fetch_status,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (id, name, "git@example.com:test.git", "main", "backend",
         fetch_status, created, created),
    )


async def _seed_workspace(db: Database, *, ws_id: str, slug: str) -> None:
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (ws_id, "T-" + ws_id, slug, "active", str(Path.cwd()),
         _ts(4, 1), _ts(4, 1)),
    )


async def _seed_design_doc(db: Database, *, dd_id: str, ws_id: str) -> None:
    await db.execute(
        "INSERT INTO design_docs(id,workspace_id,slug,version,path,"
        "needs_frontend_mockup,rubric_threshold,status,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (dd_id, ws_id, "d-" + dd_id, "1.0.0",
         str(Path.cwd() / "dummy.md"), 0, 85, "published", _ts(4, 1)),
    )


async def _seed_dev_work_row(
    db: Database,
    *,
    dw_id: str,
    ws_id: str,
    dd_id: str,
    created_at: str | None = None,
) -> None:
    created = created_at or _ts(4, 1)
    await db.execute(
        "INSERT INTO dev_works(id,workspace_id,design_doc_id,prompt,"
        "current_step,iteration_rounds,agent,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (dw_id, ws_id, dd_id, "p", "INIT", 0, "claude", created, created),
    )


async def _seed_dev_work_repo(
    db: Database,
    *,
    dw_id: str,
    repo_id: str,
    mount_name: str,
    base_branch: str = "main",
    devwork_branch: str = "feature/test",
    is_primary: int = 0,
) -> None:
    created = _ts(4, 1)
    await db.execute(
        "INSERT INTO dev_work_repos(dev_work_id,repo_id,mount_name,"
        "base_branch,devwork_branch,is_primary,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (dw_id, repo_id, mount_name, base_branch, devwork_branch,
         is_primary, created, created),
    )


@pytest.fixture
async def client(tmp_path):
    test_app = FastAPI(title="cooagents-test-metrics-repos")
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

    test_app.include_router(metrics_repos_router, prefix="/api/v1")

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        ac.db = db  # type: ignore[attr-defined]
        yield ac

    await db.close()


async def test_empty_db_returns_zeros(client):
    r = await client.get("/api/v1/metrics/repos")
    assert r.status_code == 200
    assert r.json() == {
        "multi_repo_dev_work_share": 0.0,
        "healthy_repos_share": 0.0,
    }


async def test_aggregate_computed_correctly(client):
    db = client.db
    # 3 repos: 2 healthy + 1 error.
    await _seed_repo_row(db, id="repo-h1", name="h1", fetch_status="healthy")
    await _seed_repo_row(db, id="repo-h2", name="h2", fetch_status="healthy")
    await _seed_repo_row(db, id="repo-e1", name="e1", fetch_status="error")

    # Workspace + per-DevWork design docs (dev_works.design_doc_id is UNIQUE).
    await _seed_workspace(db, ws_id="ws-1", slug="ws1")
    for dw_id in ("dw-1", "dw-2", "dw-3", "dw-4"):
        await _seed_design_doc(db, dd_id=f"dd-{dw_id}", ws_id="ws-1")
        await _seed_dev_work_row(
            db, dw_id=dw_id, ws_id="ws-1", dd_id=f"dd-{dw_id}",
        )

    # dw-1 multi: h1 + h2
    await _seed_dev_work_repo(db, dw_id="dw-1", repo_id="repo-h1", mount_name="frontend")
    await _seed_dev_work_repo(db, dw_id="dw-1", repo_id="repo-h2", mount_name="backend")
    # dw-2 multi: h1 + e1
    await _seed_dev_work_repo(db, dw_id="dw-2", repo_id="repo-h1", mount_name="frontend")
    await _seed_dev_work_repo(db, dw_id="dw-2", repo_id="repo-e1", mount_name="backend")
    # dw-3 single: h1
    await _seed_dev_work_repo(db, dw_id="dw-3", repo_id="repo-h1", mount_name="backend")
    # dw-4 single: h2
    await _seed_dev_work_repo(db, dw_id="dw-4", repo_id="repo-h2", mount_name="backend")

    r = await client.get("/api/v1/metrics/repos")
    assert r.status_code == 200
    body = r.json()
    assert body["healthy_repos_share"] == pytest.approx(2 / 3, rel=1e-3)
    assert body["multi_repo_dev_work_share"] == pytest.approx(0.5)


async def test_since_until_window(client):
    db = client.db
    # One healthy repo seeded back in January — must still be counted because
    # healthy_repos_share is window-independent.
    await _seed_repo_row(
        db, id="repo-old", name="old", fetch_status="healthy",
        created_at=_ts(1, 1),
    )
    await _seed_workspace(db, ws_id="ws-1", slug="ws1")

    # Three multi-repo DevWorks across Jan / Apr / Jul. dev_works.design_doc_id
    # is UNIQUE, so seed a fresh design_doc per dev_work.
    for dw_id, created in (
        ("dw-jan", _ts(1, 1)),
        ("dw-apr", _ts(4, 1)),
        ("dw-jul", _ts(7, 1)),
    ):
        await _seed_design_doc(db, dd_id=f"dd-{dw_id}", ws_id="ws-1")
        await _seed_dev_work_row(
            db, dw_id=dw_id, ws_id="ws-1", dd_id=f"dd-{dw_id}",
            created_at=created,
        )
        await _seed_dev_work_repo(
            db, dw_id=dw_id, repo_id="repo-old",
            mount_name="frontend", devwork_branch=f"f-{dw_id}",
        )
        # second repo per dw to make it multi-repo. Reuse repo-old by seeding
        # a sibling row.
        sibling_id = f"repo-{dw_id}"
        await _seed_repo_row(
            db, id=sibling_id, name=sibling_id, fetch_status="healthy",
            created_at=created,
        )
        await _seed_dev_work_repo(
            db, dw_id=dw_id, repo_id=sibling_id,
            mount_name="backend", devwork_branch=f"b-{dw_id}",
        )

    # Window Mar 1 ~ May 1 — only dw-apr falls inside.
    r = await client.get(
        "/api/v1/metrics/repos",
        params={"since": _ts(3, 1), "until": _ts(5, 1)},
    )
    assert r.status_code == 200
    body = r.json()
    # Numerator and denominator both narrow to the single Apr DevWork.
    assert body["multi_repo_dev_work_share"] == pytest.approx(1.0)
    # Window-independent snapshot: 4 healthy repos / 4 total.
    assert body["healthy_repos_share"] == pytest.approx(1.0)

    # Healthy share independent of window: same value with a non-overlapping
    # window that excludes every dev_work.
    r2 = await client.get(
        "/api/v1/metrics/repos",
        params={"since": _ts(11, 1), "until": _ts(12, 1)},
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["healthy_repos_share"] == pytest.approx(1.0)
    # Empty window for dev_works — share defaults to 0.0.
    assert body2["multi_repo_dev_work_share"] == 0.0

    # Invalid ISO8601 → 400.
    r_bad = await client.get(
        "/api/v1/metrics/repos", params={"since": "bogus"}
    )
    assert r_bad.status_code == 400
