"""Phase 8: PRD-mandated end-to-end smoke tests.

Three scenarios (正路径 / DesignWork escalated / DevWork escalated) drive the
real state machines via scripted executors, then hit /api/v1/metrics/workspaces
and assert the aggregate reflects SM reality.

Helpers are imported from the Phase 3 / Phase 4 SM test modules — do not fork.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from routes.metrics import router as metrics_router
from src.database import Database
from src.design_doc_manager import DesignDocManager
from src.design_work_sm import DesignWorkStateMachine
from src.dev_iteration_note_manager import DevIterationNoteManager
from src.dev_work_sm import DevWorkStateMachine
from src.exceptions import BadRequestError, NotFoundError
from src.git_utils import run_git
from src.models import DesignWorkMode, DevRepoRef
from src.repos.registry import RepoRegistryRepo
from src.storage import LocalFileStore
from src.storage.registry import WorkspaceFileRegistry, WorkspaceFilesRepo
from src.workspace_manager import WorkspaceManager


async def _seed_repo(
    db: Database, ws_root: Path, repo_dir: Path,
    repo_id: str = "repo-test00000001",
) -> tuple[str, list[tuple[DevRepoRef, str | None]]]:
    """Register a repo with a real bare clone so DevWork _s0_init succeeds.

    Returns ``(repo_id, repo_refs_arg)`` ready to pass to ``sm.create()``.
    """
    bare_dir = ws_root / ".coop" / "registry" / "repos" / f"{repo_id}.git"
    bare_dir.parent.mkdir(parents=True, exist_ok=True)
    await run_git("clone", "--bare", str(repo_dir), str(bare_dir))
    repo_registry = RepoRegistryRepo(db)
    await repo_registry.upsert(
        id=repo_id, name=repo_id, url=str(repo_dir),
        default_branch="main", bare_clone_path=str(bare_dir),
        role="backend",
    )
    await repo_registry.update_fetch_status(
        repo_id, status="healthy", bare_clone_path=str(bare_dir),
    )
    refs = [(
        DevRepoRef(repo_id=repo_id, base_branch="main", mount_name="backend"),
        None,
    )]
    return repo_id, refs


def _build_registry_stack(db, ws_root: Path):
    ws_root.mkdir(parents=True, exist_ok=True)
    store = LocalFileStore(workspaces_root=ws_root)
    repo = WorkspaceFilesRepo(db)
    registry = WorkspaceFileRegistry(store=store, repo=repo)
    return registry

from tests.test_design_work_sm import (
    FIXTURES as DESIGN_FIXTURES,
    StubExecutor as DesignStubExecutor,
    _build_config as _build_design_config,
)
from tests.test_dev_work_sm import (
    DESIGN_FIXTURE,
    ScriptedExecutor,
    _build_config as _build_dev_config,
    _step5_writer,
    step2_append_h2,
    step3_write_ctx,
    step4_write_findings,
)


async def _make_metrics_client(db: Database) -> AsyncClient:
    app = FastAPI(title="cooagents-test-smoke-metrics")
    app.state.db = db

    @app.exception_handler(NotFoundError)
    async def _nf(request, exc):
        return JSONResponse(status_code=404, content={"message": str(exc)})

    @app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(status_code=400, content={"message": str(exc)})

    app.include_router(metrics_router, prefix="/api/v1")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    await run_git("init", cwd=str(path))
    await run_git("config", "user.email", "t@x", cwd=str(path))
    await run_git("config", "user.name", "T", cwd=str(path))
    await run_git("checkout", "-b", "main", cwd=str(path), check=False)
    (path / "README.md").write_text("# demo\n")
    await run_git("add", "README.md", cwd=str(path))
    await run_git("commit", "-m", "init", cwd=str(path))


async def test_smoke_happy_path(tmp_path):
    """Scenario A: Workspace → DesignDoc → DevWork first-pass → metrics."""
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

        # Seed published DesignDoc directly (faster than driving DesignWork SM here)
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

        repo_dir = tmp_path / "repo"
        await _init_repo(repo_dir)
        _, refs = await _seed_repo(db, ws_root, repo_dir)

        executor = ScriptedExecutor([
            step2_append_h2,
            step3_write_ctx,
            step4_write_findings,
            _step5_writer({"score": 90, "issues": [], "problem_category": None}),
        ])
        sm = DevWorkStateMachine(
            db=db, workspaces=wm, design_docs=ddm, iteration_notes=ini,
            executor=executor, config=_build_dev_config(), registry=registry,
        )
        sm.workspaces_root = ws_root.resolve()
        dw = await sm.create(
            workspace_id=ws["id"], design_doc_id=dd["id"],
            repo_refs=refs, prompt="build login",
        )
        final = await asyncio.wait_for(sm.run_to_completion(dw["id"]), timeout=15)
        assert final["current_step"] == "COMPLETED"
        assert final["first_pass_success"] == 1

        async with await _make_metrics_client(db) as client:
            r = await client.get("/api/v1/metrics/workspaces")
        assert r.status_code == 200
        body = r.json()
        assert body["active_workspaces"] == 1
        assert body["first_pass_success_rate"] == pytest.approx(1.0)
        assert body["avg_iteration_rounds"] == pytest.approx(0.0)
    finally:
        await db.close()


async def test_smoke_design_escalated(tmp_path):
    """Scenario B: DesignWork hits max_loops with always-missing fixture."""
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

        stub = DesignStubExecutor(DESIGN_FIXTURES / "always_missing")
        sm = DesignWorkStateMachine(
            db=db, workspaces=wm, design_docs=ddm, executor=stub,
            config=_build_design_config(max_loops=3), registry=registry,
        )
        dw = await sm.create(
            workspace_id=ws["id"], title="T", sub_slug="demo",
            user_input="x" * 50, mode=DesignWorkMode.new,
            parent_version=None, needs_frontend_mockup=False, agent="claude",
        )
        final = await asyncio.wait_for(sm.run_to_completion(dw["id"]), timeout=15)
        assert final["current_state"] == "ESCALATED"

        escalated = await db.fetchone(
            "SELECT * FROM workspace_events "
            "WHERE event_name='design_work.escalated' AND correlation_id=?",
            (dw["id"],),
        )
        assert escalated is not None

        async with await _make_metrics_client(db) as client:
            r = await client.get("/api/v1/metrics/workspaces")
        assert r.status_code == 200
        body = r.json()
        # DesignWork escalation does not archive the Workspace.
        assert body["active_workspaces"] == 1
        # No DevWork ever created.
        assert body["first_pass_success_rate"] == pytest.approx(0.0)
        assert body["avg_iteration_rounds"] == pytest.approx(0.0)
    finally:
        await db.close()


async def test_smoke_devwork_escalated(tmp_path):
    """Scenario C: DevWork hits max_rounds=1 with persistent req_gap."""
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

        repo_dir = tmp_path / "repo"
        await _init_repo(repo_dir)
        _, refs = await _seed_repo(db, ws_root, repo_dir)

        executor = ScriptedExecutor([
            step2_append_h2,
            step3_write_ctx,
            step4_write_findings,
            _step5_writer({"score": 10, "issues": [], "problem_category": "req_gap"}),
        ])
        sm = DevWorkStateMachine(
            db=db, workspaces=wm, design_docs=ddm, iteration_notes=ini,
            executor=executor, config=_build_dev_config(max_rounds=1),
            registry=registry,
        )
        sm.workspaces_root = ws_root.resolve()
        dw = await sm.create(
            workspace_id=ws["id"], design_doc_id=dd["id"],
            repo_refs=refs, prompt="build login",
        )
        final = await asyncio.wait_for(sm.run_to_completion(dw["id"]), timeout=15)
        assert final["current_step"] == "ESCALATED"

        escalated = await db.fetchone(
            "SELECT * FROM workspace_events "
            "WHERE event_name='dev_work.escalated' AND correlation_id=?",
            (dw["id"],),
        )
        assert escalated is not None
        hi = await db.fetchone(
            "SELECT * FROM workspace_events "
            "WHERE event_name='workspace.human_intervention' AND correlation_id=?",
            (dw["id"],),
        )
        assert hi is not None

        async with await _make_metrics_client(db) as client:
            r = await client.get("/api/v1/metrics/workspaces")
        assert r.status_code == 200
        body = r.json()
        assert body["first_pass_success_rate"] == pytest.approx(0.0)
        assert body["avg_iteration_rounds"] >= 1.0
        assert body["human_intervention_per_workspace"] >= 1.0
    finally:
        await db.close()
