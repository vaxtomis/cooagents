"""Phase 8: /api/v1/metrics/workspaces route tests."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from routes.metrics import router as metrics_router
from src.database import Database
from src.exceptions import BadRequestError, NotFoundError


def _ts(month: int, day: int = 1, hour: int = 0) -> str:
    return f"2026-{month:02d}-{day:02d}T{hour:02d}:00:00+00:00"


async def _seed_workspace(
    db: Database,
    *,
    ws_id: str,
    slug: str,
    status: str = "active",
    created_at: str | None = None,
) -> None:
    created = created_at or _ts(4, 1)
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (ws_id, "T-" + ws_id, slug, status, str(Path.cwd()), created, created),
    )


async def _seed_design_doc(db: Database, *, dd_id: str, ws_id: str) -> None:
    await db.execute(
        "INSERT INTO design_docs(id,workspace_id,slug,version,path,"
        "needs_frontend_mockup,rubric_threshold,status,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (dd_id, ws_id, "d-" + dd_id, "1.0.0",
         str(Path.cwd() / "dummy.md"), 0, 85, "published", _ts(4, 1)),
    )


async def _seed_dev_work(
    db: Database,
    *,
    dw_id: str,
    ws_id: str,
    dd_id: str,
    current_step: str,
    iteration_rounds: int,
    first_pass_success: int | None = None,
    created_at: str | None = None,
) -> None:
    created = created_at or _ts(4, 1)
    await db.execute(
        "INSERT INTO dev_works(id,workspace_id,design_doc_id,repo_path,prompt,"
        "current_step,iteration_rounds,first_pass_success,agent,"
        "created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (dw_id, ws_id, dd_id, "/tmp/repo", "p",
         current_step, iteration_rounds, first_pass_success, "claude",
         created, created),
    )


async def _seed_event(
    db: Database,
    *,
    event_id: str,
    name: str,
    ts: str,
    workspace_id: str | None = None,
    payload: dict | None = None,
) -> None:
    await db.execute(
        "INSERT INTO workspace_events(event_id,event_name,workspace_id,"
        "correlation_id,payload_json,ts) VALUES(?,?,?,?,?,?)",
        (event_id, name, workspace_id, None,
         json.dumps(payload) if payload else None, ts),
    )


@pytest.fixture
async def client(tmp_path):
    test_app = FastAPI(title="cooagents-test-metrics")
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

    test_app.include_router(metrics_router, prefix="/api/v1")

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        ac.db = db  # type: ignore[attr-defined]
        yield ac

    await db.close()


async def test_empty_db_returns_zeros(client):
    r = await client.get("/api/v1/metrics/workspaces")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "human_intervention_per_workspace": 0.0,
        "active_workspaces": 0,
        "first_pass_success_rate": 0.0,
        "avg_iteration_rounds": 0.0,
    }


async def test_aggregate_computed_correctly(client):
    db = client.db
    # 2 active + 1 archived workspaces
    await _seed_workspace(db, ws_id="ws-a1", slug="a1", status="active")
    await _seed_workspace(db, ws_id="ws-a2", slug="a2", status="active")
    await _seed_workspace(db, ws_id="ws-z1", slug="z1", status="archived")

    # 3 human_intervention events + 2 unrelated
    await _seed_event(db, event_id="e1", name="workspace.human_intervention",
                      ts=_ts(4, 2), workspace_id="ws-a1")
    await _seed_event(db, event_id="e2", name="workspace.human_intervention",
                      ts=_ts(4, 3), workspace_id="ws-a2")
    await _seed_event(db, event_id="e3", name="workspace.human_intervention",
                      ts=_ts(4, 4), workspace_id="ws-a1")
    await _seed_event(db, event_id="e4", name="dev_work.completed",
                      ts=_ts(4, 5), workspace_id="ws-a1")
    await _seed_event(db, event_id="e5", name="design_doc.published",
                      ts=_ts(4, 6), workspace_id="ws-a2")

    # design_docs for FK satisfaction
    await _seed_design_doc(db, dd_id="dd-1", ws_id="ws-a1")
    await _seed_design_doc(db, dd_id="dd-2", ws_id="ws-a2")

    # 4 dev_works: 2 terminal fps=1, 1 terminal fps=0, 1 in-flight
    await _seed_dev_work(db, dw_id="dw-1", ws_id="ws-a1", dd_id="dd-1",
                         current_step="COMPLETED", iteration_rounds=0,
                         first_pass_success=1)
    await _seed_dev_work(db, dw_id="dw-2", ws_id="ws-a2", dd_id="dd-2",
                         current_step="COMPLETED", iteration_rounds=2,
                         first_pass_success=1)
    await _seed_dev_work(db, dw_id="dw-3", ws_id="ws-a1", dd_id="dd-1",
                         current_step="ESCALATED", iteration_rounds=4,
                         first_pass_success=0)
    await _seed_dev_work(db, dw_id="dw-4", ws_id="ws-a2", dd_id="dd-2",
                         current_step="STEP3_CONTEXT", iteration_rounds=99,
                         first_pass_success=None)

    r = await client.get("/api/v1/metrics/workspaces")
    assert r.status_code == 200
    body = r.json()
    assert body["active_workspaces"] == 2
    # 3 HI events / 3 total workspaces (active+archived)
    assert body["human_intervention_per_workspace"] == pytest.approx(1.0)
    # 2/3 of terminal dev_works were first-pass successes
    assert body["first_pass_success_rate"] == pytest.approx(2 / 3, rel=1e-3)
    # (0 + 2 + 4) / 3 — in-flight dw-4 excluded
    assert body["avg_iteration_rounds"] == pytest.approx(2.0)


async def test_since_until_window(client):
    db = client.db
    await _seed_workspace(db, ws_id="ws-a1", slug="a1")
    # Three HI events: Jan 1, Apr 1, Jul 1
    await _seed_event(db, event_id="h1", name="workspace.human_intervention",
                      ts=_ts(1, 1), workspace_id="ws-a1")
    await _seed_event(db, event_id="h2", name="workspace.human_intervention",
                      ts=_ts(4, 1), workspace_id="ws-a1")
    await _seed_event(db, event_id="h3", name="workspace.human_intervention",
                      ts=_ts(7, 1), workspace_id="ws-a1")

    # Window Mar 1 ~ May 1 — only h2 qualifies
    r = await client.get(
        "/api/v1/metrics/workspaces",
        params={"since": _ts(3, 1), "until": _ts(5, 1)},
    )
    assert r.status_code == 200
    body = r.json()
    # 1 HI event / 1 workspace
    assert body["human_intervention_per_workspace"] == pytest.approx(1.0)

    # Invalid ISO8601 → 400
    r = await client.get(
        "/api/v1/metrics/workspaces", params={"since": "bogus"}
    )
    assert r.status_code == 400


async def test_iso_normalization_matches_z_suffix_and_naive(client):
    """Client may send ``...Z`` or naive ISO; both must bind against stored
    ``+00:00`` rows (regression: string compare broke silently before)."""
    db = client.db
    await _seed_workspace(db, ws_id="ws-n1", slug="n1")
    await _seed_event(db, event_id="h1", name="workspace.human_intervention",
                      ts=_ts(4, 1), workspace_id="ws-n1")

    # Z suffix
    r = await client.get(
        "/api/v1/metrics/workspaces",
        params={"since": "2026-03-01T00:00:00Z", "until": "2026-05-01T00:00:00Z"},
    )
    assert r.status_code == 200
    assert r.json()["human_intervention_per_workspace"] == pytest.approx(1.0)

    # Naive (treated as UTC)
    r = await client.get(
        "/api/v1/metrics/workspaces",
        params={"since": "2026-03-01T00:00:00", "until": "2026-05-01T00:00:00"},
    )
    assert r.status_code == 200
    assert r.json()["human_intervention_per_workspace"] == pytest.approx(1.0)


async def test_active_workspaces_is_not_windowed(client):
    """`active_workspaces` is a current-state gauge; windowing would hide
    still-active workspaces created before `since`."""
    db = client.db
    # Active workspace created well before the query window.
    await _seed_workspace(
        db, ws_id="ws-old", slug="old",
        status="active", created_at=_ts(1, 1),
    )
    r = await client.get(
        "/api/v1/metrics/workspaces",
        params={"since": _ts(4, 1), "until": _ts(5, 1)},
    )
    assert r.status_code == 200
    assert r.json()["active_workspaces"] == 1
