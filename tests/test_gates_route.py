"""Phase 8 regression: gate approve/reject must write to workspace_events.

Before Phase 8, the gate route called ``webhooks.deliver(...)`` directly,
so `workspace.human_intervention` events never landed in the
`workspace_events` table — the PRD human-intervention metric undercounted
every gate action. This test locks in the fix.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from routes.gates import router as gates_router
from src.auth import get_current_user
from src.database import Database
from src.exceptions import BadRequestError, ConflictError, NotFoundError


def _ts() -> str:
    return "2026-04-23T00:00:00+00:00"


async def _seed_workspace(db: Database, ws_id: str = "ws-g1") -> None:
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (ws_id, "T", "gates", "active", str(Path.cwd()), _ts(), _ts()),
    )


async def _seed_dev_work_with_gate(
    db: Database,
    *,
    dw_id: str = "dev-g1",
    ws_id: str = "ws-g1",
) -> None:
    await db.execute(
        "INSERT INTO design_docs(id,workspace_id,slug,version,path,"
        "needs_frontend_mockup,rubric_threshold,status,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("dd-g1", ws_id, "g1", "1.0.0",
         str(Path.cwd() / "dummy.md"), 0, 85, "published", _ts()),
    )
    gates_json = json.dumps({"exit": {"status": "waiting"}})
    await db.execute(
        "INSERT INTO dev_works(id,workspace_id,design_doc_id,repo_path,"
        "prompt,current_step,iteration_rounds,agent,gates_json,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (dw_id, ws_id, "dd-g1", "/tmp/repo", "p",
         "STEP5_REVIEW", 0, "claude", gates_json, _ts(), _ts()),
    )


@pytest.fixture
async def client(tmp_path):
    test_app = FastAPI(title="cooagents-test-gates")
    db = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await db.connect()
    test_app.state.db = db
    test_app.state.webhooks = None  # emit_and_deliver tolerates None
    test_app.state.dev_work_sm = None
    test_app.state.design_work_sm = None
    test_app.state.start_time = time.time()

    # Needed by @limiter.limit on the route decorator.
    from slowapi import Limiter
    from src.request_utils import client_ip
    limiter = Limiter(key_func=client_ip, default_limits=["1000/minute"])
    test_app.state.limiter = limiter
    limiter.enabled = False

    @test_app.exception_handler(NotFoundError)
    async def _nf(request, exc):
        return JSONResponse(status_code=404, content={"message": str(exc)})

    @test_app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(status_code=400, content={"message": str(exc)})

    @test_app.exception_handler(ConflictError)
    async def _cf(request, exc):
        return JSONResponse(status_code=409, content={"message": str(exc)})

    test_app.include_router(gates_router, prefix="/api/v1")
    test_app.dependency_overrides[get_current_user] = lambda: "test-user"

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        ac.db = db  # type: ignore[attr-defined]
        yield ac

    await db.close()


async def test_approve_writes_workspace_event(client):
    await _seed_workspace(client.db)
    await _seed_dev_work_with_gate(client.db)

    r = await client.post(
        "/api/v1/gates/dev:dev-g1:exit/approve",
        json={"note": "looks good"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"

    # Defect regression: the workspace_events row must exist.
    ev = await client.db.fetchone(
        "SELECT * FROM workspace_events "
        "WHERE event_name='workspace.human_intervention' AND correlation_id=?",
        ("dev-g1",),
    )
    assert ev is not None, (
        "Phase 8 regression: gate approve must write to workspace_events "
        "(the metric relies on this)."
    )
    payload = json.loads(ev["payload_json"])
    assert payload == {
        "actor": "test-user",
        "action": "approve",
        "target": "dev:dev-g1:exit",
        "note": "looks good",
    }


async def test_reject_writes_workspace_event(client):
    await _seed_workspace(client.db)
    await _seed_dev_work_with_gate(client.db)

    r = await client.post(
        "/api/v1/gates/dev:dev-g1:exit/reject",
        json={"note": "not yet"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"

    rows = await client.db.fetchall(
        "SELECT * FROM workspace_events "
        "WHERE event_name='workspace.human_intervention' AND correlation_id=?",
        ("dev-g1",),
    )
    assert len(rows) == 1
    assert json.loads(rows[0]["payload_json"])["action"] == "reject"
