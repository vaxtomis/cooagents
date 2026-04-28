"""Phase 9: PRD-mandated end-to-end smoke for the repo-registry MVP.

Two scenarios:

* ``test_smoke_multi_repo_happy_path`` — register two repos via the registry,
  drive the DevWork SM through a 2-mount happy path, then assert
  ``/api/v1/metrics/repos`` reports ``multi_repo_dev_work_share == 1.0`` and
  ``healthy_repos_share == 1.0``.
* ``test_smoke_fetch_error_visible`` — flip one repo's ``fetch_status`` to
  ``error`` directly through the registry and assert the metric drops to
  ``0.5`` (no DevWorks needed for this projection).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from routes.metrics_repos import router as metrics_repos_router
from src.database import Database
from src.design_doc_manager import DesignDocManager
from src.dev_iteration_note_manager import DevIterationNoteManager
from src.dev_work_sm import DevWorkStateMachine
from src.exceptions import BadRequestError, NotFoundError
from src.models import DevRepoRef
from src.repos.registry import RepoRegistryRepo
from src.workspace_manager import WorkspaceManager
from tests.test_dev_work_sm import (
    DESIGN_FIXTURE,
    ScriptedExecutor,
    _build_config as _build_dev_config,
    _step5_writer,
    step2_append_h2,
    step3_write_ctx,
    step4_write_findings,
)
from tests.test_smoke_e2e import (
    _build_registry_stack,
    _init_repo,
    _seed_repo,
)


async def _make_metrics_repos_client(db: Database) -> AsyncClient:
    app = FastAPI(title="cooagents-test-smoke-metrics-repos")
    app.state.db = db

    @app.exception_handler(NotFoundError)
    async def _nf(request, exc):
        return JSONResponse(status_code=404, content={"message": str(exc)})

    @app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(status_code=400, content={"message": str(exc)})

    app.include_router(metrics_repos_router, prefix="/api/v1")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_smoke_multi_repo_happy_path(tmp_path):
    """Two healthy repos → multi-repo DevWork → COMPLETED → metrics confirm."""
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    try:
        ws_root = tmp_path / "ws"
        registry = _build_registry_stack(db, ws_root)
        wm = WorkspaceManager(
            db, project_root=tmp_path, workspaces_root=ws_root,
            registry=registry,
        )
        ws = await wm.create_with_scaffold(title="T", slug="t")
        ddm = DesignDocManager(db, registry=registry)
        ini = DevIterationNoteManager(db)

        design_text = DESIGN_FIXTURE.read_text(encoding="utf-8")
        dd = await ddm.persist(
            workspace_row=ws, slug="demo", version="1.0.0",
            markdown=design_text, parent_version=None,
            needs_frontend_mockup=False, rubric_threshold=85,
        )
        await db.execute(
            "UPDATE design_docs SET status='published', published_at=? WHERE id=?",
            ("2026-04-23", dd["id"]),
        )

        repo_fe_dir = tmp_path / "repo_fe"
        repo_be_dir = tmp_path / "repo_be"
        await _init_repo(repo_fe_dir)
        await _init_repo(repo_be_dir)

        fe_id = "repo-fe000000001"
        be_id = "repo-be000000002"
        await _seed_repo(db, ws_root, repo_fe_dir, repo_id=fe_id)
        await _seed_repo(db, ws_root, repo_be_dir, repo_id=be_id)

        refs = [
            (
                DevRepoRef(repo_id=fe_id, base_branch="main",
                           mount_name="frontend"),
                None,
            ),
            (
                DevRepoRef(repo_id=be_id, base_branch="main",
                           mount_name="backend", is_primary=True),
                None,
            ),
        ]

        executor = ScriptedExecutor([
            step2_append_h2,
            step3_write_ctx,
            step4_write_findings,
            _step5_writer({"score": 90, "issues": [], "problem_category": None}),
        ])
        from tests.conftest import make_test_llm_runner
        sm = DevWorkStateMachine(
            db=db, workspaces=wm, design_docs=ddm, iteration_notes=ini,
            executor=executor, config=_build_dev_config(), registry=registry,
            llm_runner=make_test_llm_runner(executor),
        )
        sm.workspaces_root = ws_root.resolve()
        dw = await sm.create(
            workspace_id=ws["id"], design_doc_id=dd["id"],
            repo_refs=refs, prompt="build login multi-repo",
        )
        final = await asyncio.wait_for(
            sm.run_to_completion(dw["id"]), timeout=20,
        )
        assert final["current_step"] == "COMPLETED"

        # Two dev_work_repos rows for this DevWork (Phase 5 fan-out).
        rows = await db.fetchall(
            "SELECT mount_name, push_state FROM dev_work_repos "
            "WHERE dev_work_id=? ORDER BY mount_name",
            (dw["id"],),
        )
        assert len(rows) == 2
        for row in rows:
            assert row["push_state"] in {"pending", "pushed"}

        async with await _make_metrics_repos_client(db) as client:
            r = await client.get("/api/v1/metrics/repos")
        assert r.status_code == 200
        body = r.json()
        assert body["multi_repo_dev_work_share"] == pytest.approx(1.0)
        assert body["healthy_repos_share"] == pytest.approx(1.0)
    finally:
        await db.close()


async def test_smoke_fetch_error_visible(tmp_path):
    """One healthy + one error → ``healthy_repos_share == 0.5``."""
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    try:
        repo_registry = RepoRegistryRepo(db)
        # Healthy repo.
        await repo_registry.upsert(
            id="repo-healthy0001", name="repo-healthy0001",
            url="git@example.com:healthy.git",
            default_branch="main", bare_clone_path=None, role="backend",
        )
        await repo_registry.update_fetch_status(
            "repo-healthy0001", status="healthy",
            bare_clone_path="/tmp/healthy.git",
        )
        # Error repo.
        await repo_registry.upsert(
            id="repo-broken0001", name="repo-broken0001",
            url="git@example.com:broken.git",
            default_branch="main", bare_clone_path=None, role="frontend",
        )
        await repo_registry.update_fetch_status(
            "repo-broken0001", status="error",
            err="simulated network failure",
        )

        async with await _make_metrics_repos_client(db) as client:
            r = await client.get("/api/v1/metrics/repos")
        assert r.status_code == 200
        body = r.json()
        assert body["healthy_repos_share"] == pytest.approx(0.5)
        # No DevWorks created.
        assert body["multi_repo_dev_work_share"] == 0.0

        broken = await db.fetchone(
            "SELECT fetch_status FROM repos WHERE id=?",
            ("repo-broken0001",),
        )
        assert broken is not None
        assert broken["fetch_status"] == "error"
    finally:
        await db.close()
