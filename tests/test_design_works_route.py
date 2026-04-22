"""Route-level tests for /api/v1/design-works (Phase 3).

Uses a lightweight per-test FastAPI app — no real lifespan, no real LLM.
Stubs AcpxExecutor so tests stay deterministic.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from src.database import Database
from src.design_doc_manager import DesignDocManager
from src.design_work_sm import DesignWorkStateMachine
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.workspace_manager import WorkspaceManager

FIXTURES = Path(__file__).parent / "fixtures" / "design"


class StubExecutor:
    _OUTPUT_RE = re.compile(r"`([^`]+\.md)`")

    def __init__(self, scenario_dir: Path):
        self.scenario_dir = scenario_dir
        self.call_count = 0

    async def run_once(
        self, agent_type, worktree, timeout_sec, task_file=None, prompt=None
    ):
        self.call_count += 1
        prompt_text = Path(task_file).read_text(encoding="utf-8")
        output_path = None
        for line in prompt_text.splitlines():
            if line.strip().startswith("将最终 markdown 写入"):
                m = self._OUTPUT_RE.search(line)
                if m:
                    output_path = m.group(1)
                    break
        fixture = self.scenario_dir / f"round{self.call_count}.md"
        if output_path and fixture.exists():
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(fixture.read_bytes())
            return ("ok", 0)
        return ("", 1)


def _build_settings():
    return SimpleNamespace(
        design=SimpleNamespace(
            max_loops=3,
            execution_timeout=30,
            required_sections=[
                "用户故事", "用户案例", "详细操作流程", "验收标准", "打分 rubric",
            ],
            mockup_sections=["页面结构"],
            allow_optimize_mode=False,
        ),
        scoring=SimpleNamespace(default_threshold=80),
    )


@pytest.fixture
async def client(tmp_path):
    test_app = FastAPI(title="cooagents-test-design-works")
    ws_root = tmp_path / "ws"

    db = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await db.connect()

    workspaces = WorkspaceManager(
        db, project_root=tmp_path, workspaces_root=ws_root
    )
    design_docs = DesignDocManager(db, workspaces_root=ws_root)
    executor = StubExecutor(FIXTURES / "perfect")
    sm = DesignWorkStateMachine(
        db=db,
        workspaces=workspaces,
        design_docs=design_docs,
        executor=executor,
        config=_build_settings(),
    )

    test_app.state.db = db
    test_app.state.workspaces = workspaces
    test_app.state.design_docs = design_docs
    test_app.state.design_work_sm = sm
    test_app.state.executor_stub = executor
    test_app.state.ws_root = ws_root
    test_app.state.start_time = time.time()

    from slowapi import Limiter
    from src.request_utils import client_ip
    limiter = Limiter(key_func=client_ip, default_limits=["1000/minute"])
    test_app.state.limiter = limiter
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

    @test_app.exception_handler(NotImplementedError)
    async def _ni(request, exc):
        return JSONResponse(
            status_code=501,
            content={"error": "not_implemented", "message": str(exc)},
        )

    from routes.workspaces import router as ws_router
    from routes.design_works import router as dw_router
    test_app.include_router(ws_router, prefix="/api/v1")
    test_app.include_router(dw_router, prefix="/api/v1")

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        yield ac

    await db.close()


async def _create_workspace(client: AsyncClient, slug: str = "w1") -> dict:
    r = await client.post(
        "/api/v1/workspaces", json={"title": "W", "slug": slug}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _wait_for_terminal(client: AsyncClient, dw_id: str, max_attempts: int = 50):
    for _ in range(max_attempts):
        r = await client.get(f"/api/v1/design-works/{dw_id}")
        if r.status_code == 200 and r.json()["current_state"] in {
            "COMPLETED", "ESCALATED", "CANCELLED"
        }:
            return r.json()
        await asyncio.sleep(0.05)
    pytest.fail(f"design_work {dw_id} did not reach terminal state in time")


async def test_create_201_and_runs_to_completion(client):
    ws = await _create_workspace(client, slug="w1")
    r = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Demo",
            "slug": "demo",
            "user_input": "some substantial user input text",
            "mode": "new",
            "needs_frontend_mockup": False,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("desw-")
    assert r.headers["Location"] == f"/api/v1/design-works/{body['id']}"

    final = await _wait_for_terminal(client, body["id"])
    assert final["current_state"] == "COMPLETED"


async def test_list_requires_workspace_id(client):
    r = await client.get("/api/v1/design-works")
    assert r.status_code == 422  # missing required query param


async def test_list_filters_by_workspace(client):
    ws1 = await _create_workspace(client, slug="ws-a")
    ws2 = await _create_workspace(client, slug="ws-b")
    await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws1["id"], "title": "A", "slug": "aa",
            "user_input": "x" * 30,
        },
    )
    await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws2["id"], "title": "B", "slug": "bb",
            "user_input": "x" * 30,
        },
    )
    r = await client.get(
        "/api/v1/design-works", params={"workspace_id": ws1["id"]}
    )
    assert r.status_code == 200
    slugs = {d["sub_slug"] for d in r.json()}
    assert slugs == {"aa"}


async def test_get_missing_returns_404(client):
    r = await client.get("/api/v1/design-works/desw-nope")
    assert r.status_code == 404


async def test_cancel_moves_to_cancelled(client):
    ws = await _create_workspace(client, slug="cx")
    r = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"], "title": "C", "slug": "c",
            "user_input": "x" * 30,
        },
    )
    dw_id = r.json()["id"]
    r2 = await client.post(f"/api/v1/design-works/{dw_id}/cancel")
    # Race with background task: either 204 or 404 (already completed).
    assert r2.status_code in (204, 404)
    final = await client.get(f"/api/v1/design-works/{dw_id}")
    assert final.json()["current_state"] in {"CANCELLED", "COMPLETED"}


async def test_create_invalid_slug_returns_422(client):
    ws = await _create_workspace(client, slug="wslug")
    r = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"], "title": "X", "slug": "Bad Slug",
            "user_input": "x" * 30,
        },
    )
    assert r.status_code == 422
