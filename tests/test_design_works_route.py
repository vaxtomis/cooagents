"""Route-level tests for /api/v1/design-works (Phase 3).

Uses a lightweight per-test FastAPI app — no real lifespan, no real LLM.
Stubs AcpxExecutor so tests stay deterministic.
"""
from __future__ import annotations

import asyncio
import json
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
        self, agent_type, worktree, timeout_sec, task_file=None, prompt=None,
        **_kwargs,
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
                "问题与目标", "用户故事", "场景案例", "范围与非目标",
                "详细操作流程", "验收标准", "技术约束与集成边界",
                "交付切片", "决策记录", "打分 rubric",
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

    ws_root.mkdir(exist_ok=True)
    from src.storage import LocalFileStore
    from src.storage.registry import WorkspaceFileRegistry, WorkspaceFilesRepo
    store = LocalFileStore(workspaces_root=ws_root)
    repo = WorkspaceFilesRepo(db)
    registry = WorkspaceFileRegistry(store=store, repo=repo)
    workspaces = WorkspaceManager(
        db, project_root=tmp_path, workspaces_root=ws_root, registry=registry,
    )
    design_docs = DesignDocManager(db, registry=registry)
    executor = StubExecutor(FIXTURES / "perfect")
    sm = DesignWorkStateMachine(
        db=db,
        workspaces=workspaces,
        design_docs=design_docs,
        executor=executor,
        config=_build_settings(),
        registry=registry,
    )

    test_app.state.db = db
    test_app.state.workspaces = workspaces
    test_app.state.design_docs = design_docs
    test_app.state.design_work_sm = sm
    test_app.state.executor_stub = executor
    test_app.state.ws_root = ws_root
    test_app.state.registry = registry
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
    from routes.design_works import limiter as dw_limiter
    from routes.design_works import router as dw_router
    dw_limiter.enabled = False
    test_app.include_router(ws_router, prefix="/api/v1")
    test_app.include_router(dw_router, prefix="/api/v1")

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        ac._app = test_app
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
    assert body["max_loops"] == 3
    assert r.headers["Location"] == f"/api/v1/design-works/{body['id']}"

    final = await _wait_for_terminal(client, body["id"])
    assert final["current_state"] == "COMPLETED"
    assert final["max_loops"] == 3


async def test_create_projects_max_loops_override(client):
    ws = await _create_workspace(client, slug="loop-override")
    r = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Loop",
            "slug": "loop",
            "user_input": "some substantial user input text",
            "mode": "new",
            "needs_frontend_mockup": False,
            "max_loops": 1,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["max_loops"] == 1

    projected = await client.get(f"/api/v1/design-works/{body['id']}")
    assert projected.status_code == 200
    assert projected.json()["max_loops"] == 1


async def test_create_accepts_uploaded_attachment_paths(client):
    ws = await _create_workspace(client, slug="attach-route")
    upload = await client.post(
        f"/api/v1/workspaces/{ws['id']}/attachments",
        files={"file": ("brief.md", b"# Brief\n\nDetails", "text/markdown")},
    )
    assert upload.status_code == 201, upload.text
    attachment_path = upload.json()["markdown_path"]

    r = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Attach",
            "slug": "attach",
            "user_input": "some substantial user input text",
            "attachment_paths": [attachment_path],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["attachment_paths"] == [attachment_path]

    projected = await client.get(f"/api/v1/design-works/{body['id']}")
    assert projected.status_code == 200
    assert projected.json()["attachment_paths"] == [attachment_path]


async def test_create_accepts_workspace_file_refs_and_prompts_them(client):
    ws = await _create_workspace(client, slug="file-ref-route")
    upload = await client.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "notes/brief.md", "kind": "other"},
        files={"file": ("brief.md", b"# Brief\n\nSpecific detail", "text/markdown")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    assert upload.status_code == 201, upload.text

    r = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "File Ref",
            "slug": "file-ref",
            "user_input": "some substantial user input text",
            "workspace_file_refs": ["notes/brief.md"],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["workspace_file_refs"] == ["notes/brief.md"]
    assert body["attachment_paths"] == []

    rows = await client._app.state.db.fetchall(
        "SELECT * FROM workspace_file_refs WHERE referrer_id=?",
        (body["id"],),
    )
    assert [row["relative_path"] for row in rows] == ["notes/brief.md"]

    final = await _wait_for_terminal(client, body["id"])
    assert final["workspace_file_refs"] == ["notes/brief.md"]
    prompt = await client._app.state.registry.read_text(
        workspace_slug=ws["slug"],
        relative_path=f"designs/.drafts/{body['id']}-prompt-loop0.md",
    )
    assert "Workspace-relative path: `notes/brief.md`" in prompt
    assert "Specific detail" in prompt


async def test_create_rejects_protected_workspace_file_ref(client):
    ws = await _create_workspace(client, slug="file-ref-protected")
    r = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Protected",
            "slug": "protected",
            "user_input": "some substantial user input text",
            "workspace_file_refs": ["workspace.md"],
        },
    )
    assert r.status_code == 400, r.text
    assert "selectable workspace files" in r.json()["message"]


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


async def test_list_paginated_envelope(client):
    ws = await _create_workspace(client, slug="ws-page")
    await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Alpha",
            "slug": "alpha",
            "user_input": "x" * 30,
        },
    )
    await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Beta",
            "slug": "beta",
            "user_input": "x" * 30,
        },
    )

    r = await client.get(
        "/api/v1/design-works",
        params={
          "workspace_id": ws["id"],
          "paginate": True,
          "limit": 1,
          "offset": 0,
          "sort": "created_desc",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["pagination"]["limit"] == 1
    assert body["pagination"]["offset"] == 0
    assert body["pagination"]["total"] == 2
    assert body["pagination"]["has_more"] is True
    assert len(body["items"]) == 1


async def test_get_missing_returns_404(client):
    r = await client.get("/api/v1/design-works/desw-nope")
    assert r.status_code == 404


async def test_get_projects_running_state_and_tick_rejects_live_driver(client):
    ws = await _create_workspace(client, slug="running")
    create = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Running",
            "slug": "running",
            "user_input": "x" * 30,
        },
    )
    assert create.status_code == 201, create.text
    dw_id = create.json()["id"]
    final = await _wait_for_terminal(client, dw_id)
    assert final["is_running"] is False

    task = asyncio.create_task(asyncio.sleep(60))
    client._app.state.design_work_sm._running[dw_id] = task
    try:
        projected = await client.get(f"/api/v1/design-works/{dw_id}")
        assert projected.status_code == 200
        assert projected.json()["is_running"] is True

        blocked = await client.post(f"/api/v1/design-works/{dw_id}/tick")
        assert blocked.status_code == 409
        assert blocked.json()["current_stage"] == final["current_state"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        client._app.state.design_work_sm._running.pop(dw_id, None)

    projected = await client.get(f"/api/v1/design-works/{dw_id}")
    assert projected.status_code == 200
    assert projected.json()["is_running"] is False

    resumed = await client.post(f"/api/v1/design-works/{dw_id}/tick")
    assert resumed.status_code == 200


async def test_get_exposes_escalation_reason(client):
    client._app.state.executor_stub.scenario_dir = FIXTURES / "always_missing"
    ws = await _create_workspace(client, slug="reason")
    create = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Reason",
            "slug": "reason",
            "user_input": "x" * 30,
        },
    )
    assert create.status_code == 201, create.text
    final = await _wait_for_terminal(client, create.json()["id"])
    assert final["current_state"] == "ESCALATED"
    assert final["escalation_reason"] == "post-validate failed"


async def test_retry_escalated_design_work_creates_new_row(client):
    client._app.state.executor_stub.scenario_dir = FIXTURES / "always_missing"
    ws = await _create_workspace(client, slug="retry")
    create = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Retry",
            "slug": "retry",
            "user_input": "x" * 30,
        },
    )
    assert create.status_code == 201, create.text
    source_id = create.json()["id"]
    source = await _wait_for_terminal(client, source_id)
    assert source["current_state"] == "ESCALATED"

    retry = await client.post(f"/api/v1/design-works/{source_id}/retry")
    assert retry.status_code == 201, retry.text
    body = retry.json()
    assert body["id"] != source_id
    assert retry.headers["Location"] == f"/api/v1/design-works/{body['id']}"

    source_after = await client.get(f"/api/v1/design-works/{source_id}")
    assert source_after.status_code == 200
    assert source_after.json()["current_state"] == "ESCALATED"


async def test_retry_source_returns_editable_source_payload(client):
    client._app.state.executor_stub.scenario_dir = FIXTURES / "always_missing"
    ws = await _create_workspace(client, slug="retry-source")
    create = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Retry Source",
            "slug": "retry-source",
            "user_input": "source text for retry form",
            "needs_frontend_mockup": True,
            "agent": "codex",
        },
    )
    assert create.status_code == 201, create.text
    source = await _wait_for_terminal(client, create.json()["id"])
    assert source["current_state"] == "ESCALATED"

    r = await client.get(f"/api/v1/design-works/{source['id']}/retry-source")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Retry Source"
    assert body["slug"] == "retry-source"
    assert body["user_input"] == "source text for retry form"
    assert body["needs_frontend_mockup"] is True
    assert body["agent"] == "codex"
    assert body["repo_refs"] == []


async def test_retry_with_overrides_creates_new_row_from_edited_values(client):
    client._app.state.executor_stub.scenario_dir = FIXTURES / "always_missing"
    ws = await _create_workspace(client, slug="retry-override")
    create = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Retry Override",
            "slug": "retry-override",
            "user_input": "original text for retry",
            "agent": "claude",
        },
    )
    assert create.status_code == 201, create.text
    source = await _wait_for_terminal(client, create.json()["id"])
    assert source["current_state"] == "ESCALATED"

    retry = await client.post(
        f"/api/v1/design-works/{source['id']}/retry",
        json={
            "title": "Edited Retry",
            "slug": "edited-retry",
            "user_input": "edited text for retry",
            "needs_frontend_mockup": True,
            "agent": None,
            "repo_refs": [],
        },
    )
    assert retry.status_code == 201, retry.text
    body = retry.json()
    assert body["title"] == "Edited Retry"
    assert body["sub_slug"] == "edited-retry"
    created_row = await client._app.state.db.fetchone(
        "SELECT * FROM design_works WHERE id=?", (body["id"],)
    )
    assert created_row["needs_frontend_mockup"] == 1
    assert created_row["agent"] == "codex"
    saved_input = await client._app.state.registry.read_text(
        workspace_slug=ws["slug"],
        relative_path=created_row["user_input_path"],
    )
    assert saved_input == "edited text for retry"


async def test_retry_rejects_null_user_input_override(client):
    client._app.state.executor_stub.scenario_dir = FIXTURES / "always_missing"
    ws = await _create_workspace(client, slug="retry-null")
    create = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Retry null",
            "slug": "retry-null",
            "user_input": "original text for retry",
        },
    )
    assert create.status_code == 201, create.text
    source = await _wait_for_terminal(client, create.json()["id"])
    assert source["current_state"] == "ESCALATED"

    retry = await client.post(
        f"/api/v1/design-works/{source['id']}/retry",
        json={"user_input": None},
    )
    assert retry.status_code == 422


async def test_retry_non_escalated_design_work_returns_409(client):
    ws = await _create_workspace(client, slug="retry-completed")
    create = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Retry completed",
            "slug": "retry-completed",
            "user_input": "x" * 30,
        },
    )
    assert create.status_code == 201, create.text
    final = await _wait_for_terminal(client, create.json()["id"])
    assert final["current_state"] == "COMPLETED"

    retry = await client.post(f"/api/v1/design-works/{final['id']}/retry")
    assert retry.status_code == 409
    assert retry.json()["current_stage"] == "COMPLETED"


async def test_retry_missing_source_input_returns_404(client):
    client._app.state.executor_stub.scenario_dir = FIXTURES / "always_missing"
    ws = await _create_workspace(client, slug="retry-missing")
    create = await client.post(
        "/api/v1/design-works",
        json={
            "workspace_id": ws["id"],
            "title": "Retry missing",
            "slug": "retry-missing",
            "user_input": "x" * 30,
        },
    )
    assert create.status_code == 201, create.text
    source = await _wait_for_terminal(client, create.json()["id"])
    assert source["current_state"] == "ESCALATED"
    await client._app.state.db.execute(
        "UPDATE design_works SET user_input_path=? WHERE id=?",
        ("designs/.drafts/missing-input.md", source["id"]),
    )

    retry = await client.post(f"/api/v1/design-works/{source['id']}/retry")
    assert retry.status_code == 404


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


async def test_rerun_cancelled_design_work_resumes_driver(client):
    ws = await _create_workspace(client, slug="rerun-cancelled")
    app = client._app
    dw_id = "desw-rerun0001"
    input_rel = f"designs/.drafts/{dw_id}-input.md"
    await app.state.registry.put_markdown(
        workspace_row=ws,
        relative_path=input_rel,
        text="substantial requirement text for rerun",
        kind="design_input",
    )
    await app.state.db.execute(
        """INSERT INTO design_works
           (id, workspace_id, mode, needs_frontend_mockup, current_state,
            loop, agent, user_input_path, title, sub_slug, version,
            output_path, gates_json, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            dw_id,
            ws["id"],
            "new",
            0,
            "CANCELLED",
            0,
            "codex",
            input_rel,
            "Rerun",
            "rerun",
            "1.0.0",
            "designs/DES-rerun-1.0.0.md",
            json.dumps({"cancelled_from_state": "PRE_VALIDATE"}),
            "2026-04-23T00:00:00Z",
            "2026-04-23T00:00:00Z",
        ),
    )

    r = await client.post(f"/api/v1/design-works/{dw_id}/rerun")

    assert r.status_code == 200, r.text
    assert r.json()["current_state"] != "CANCELLED"
    final = await _wait_for_terminal(client, dw_id)
    assert final["current_state"] in {"COMPLETED", "ESCALATED"}


async def test_rerun_legacy_cancelled_design_work_without_resume_metadata(client):
    ws = await _create_workspace(client, slug="rerun-legacy-design")
    app = client._app
    dw_id = "desw-legacy0001"
    input_rel = f"designs/.drafts/{dw_id}-input.md"
    await app.state.registry.put_markdown(
        workspace_row=ws,
        relative_path=input_rel,
        text="substantial requirement text for legacy rerun",
        kind="design_input",
    )
    await app.state.db.execute(
        """INSERT INTO design_works
           (id, workspace_id, mode, needs_frontend_mockup, current_state,
            loop, agent, user_input_path, title, sub_slug, version,
            output_path, gates_json, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            dw_id,
            ws["id"],
            "new",
            0,
            "CANCELLED",
            0,
            "codex",
            input_rel,
            "Legacy",
            "legacy",
            "1.0.0",
            "designs/DES-legacy-1.0.0.md",
            None,
            "2026-04-23T00:00:00Z",
            "2026-04-23T00:00:00Z",
        ),
    )

    r = await client.post(f"/api/v1/design-works/{dw_id}/rerun")

    assert r.status_code == 200, r.text
    assert r.json()["current_state"] != "CANCELLED"


async def test_delete_cancelled_design_work_cleans_files_and_rows(client):
    ws = await _create_workspace(client, slug="delete-cancelled")
    app = client._app
    dw_id = "desw-delete0001"
    input_rel = f"designs/.drafts/{dw_id}-input.md"
    prompt_rel = f"designs/.drafts/{dw_id}-prompt-loop0.md"
    output_rel = "designs/DES-delete-me-1.0.0.md"
    await app.state.registry.put_markdown(
        workspace_row=ws,
        relative_path=input_rel,
        text="input",
        kind="design_input",
    )
    await app.state.registry.put_markdown(
        workspace_row=ws,
        relative_path=prompt_rel,
        text="prompt",
        kind="prompt",
    )
    await app.state.registry.store.put_bytes(
        f"{ws['slug']}/{output_rel}",
        b"unregistered draft output",
    )
    await app.state.db.execute(
        """INSERT INTO design_works
           (id, workspace_id, mode, needs_frontend_mockup, current_state,
            loop, agent, user_input_path, title, sub_slug, version,
            output_path, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            dw_id,
            ws["id"],
            "new",
            0,
            "CANCELLED",
            0,
            "codex",
            input_rel,
            "Delete me",
            "delete-me",
            "1.0.0",
            output_rel,
            "2026-04-23T00:00:00Z",
            "2026-04-23T00:00:00Z",
        ),
    )
    await app.state.db.execute(
        "INSERT INTO reviews(id, design_work_id, round, created_at) "
        "VALUES(?,?,?,?)",
        ("rev-delete-design", dw_id, 1, "2026-04-23T00:00:01Z"),
    )
    await app.state.db.execute(
        "INSERT INTO workspace_events(event_id, event_name, workspace_id, "
        "correlation_id, ts) VALUES(?,?,?,?,?)",
        ("evt-delete-design", "design_work.cancelled", ws["id"], dw_id,
         "2026-04-23T00:00:02Z"),
    )
    await app.state.db.execute(
        "INSERT INTO agent_dispatches(id, host_id, workspace_id, correlation_id, "
        "correlation_kind, state, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            "ad-delete-design",
            "local",
            ws["id"],
            dw_id,
            "design_work",
            "succeeded",
            "2026-04-23T00:00:03Z",
            "2026-04-23T00:00:03Z",
        ),
    )
    await app.state.db.execute(
        "INSERT INTO agent_executions(id, dispatch_id, host_id, agent, "
        "execution_mode, correlation_kind, correlation_id, run_token, cwd, "
        "state, lease_expires_at, started_at, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "aex-delete-design",
            "ad-delete-design",
            "local",
            "codex",
            "local",
            "design_work",
            dw_id,
            "rt-delete-design",
            str(Path.cwd()),
            "exited",
            "2026-04-23T00:05:00Z",
            "2026-04-23T00:00:03Z",
            "2026-04-23T00:00:03Z",
            "2026-04-23T00:00:03Z",
        ),
    )

    r = await client.delete(f"/api/v1/design-works/{dw_id}")

    assert r.status_code == 204, r.text
    assert (await client.get(f"/api/v1/design-works/{dw_id}")).status_code == 404
    assert await app.state.registry.stat(
        workspace_slug=ws["slug"], relative_path=input_rel,
    ) is None
    assert await app.state.registry.stat(
        workspace_slug=ws["slug"], relative_path=prompt_rel,
    ) is None
    assert await app.state.registry.stat(
        workspace_slug=ws["slug"], relative_path=output_rel,
    ) is None
    assert await app.state.db.fetchone(
        "SELECT id FROM reviews WHERE id='rev-delete-design'",
    ) is None
    assert await app.state.db.fetchone(
        "SELECT id FROM workspace_events WHERE event_id='evt-delete-design'",
    ) is None
    assert await app.state.db.fetchone(
        "SELECT id FROM agent_executions WHERE id='aex-delete-design'",
    ) is None
    assert await app.state.db.fetchone(
        "SELECT id FROM agent_dispatches WHERE id='ad-delete-design'",
    ) is None


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
