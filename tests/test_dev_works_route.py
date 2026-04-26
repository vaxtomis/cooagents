"""Phase 4: /api/v1/dev-works route tests."""
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
from src.dev_iteration_note_manager import DevIterationNoteManager
from src.dev_work_sm import DevWorkStateMachine
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.git_utils import run_git
from src.repos.registry import RepoRegistryRepo
from src.workspace_manager import WorkspaceManager

DESIGN_FIXTURE = Path(__file__).parent / "fixtures" / "design" / "perfect" / "round1.md"


def _build_settings(workspace_root: Path | None = None):
    """Build a minimal Settings stand-in."""
    root = (workspace_root or Path(".")).resolve()
    return SimpleNamespace(
        design=SimpleNamespace(
            required_sections=[
                "用户故事", "用户案例", "详细操作流程", "验收标准", "打分 rubric",
            ],
            mockup_sections=["页面结构"],
            allow_optimize_mode=False,
        ),
        scoring=SimpleNamespace(default_threshold=80),
        devwork=SimpleNamespace(
            max_rounds=5, step2_timeout=10, step3_timeout=10,
            step4_timeout=10, step5_timeout=10,
            require_human_exit_confirm=False,
        ),
        security=SimpleNamespace(
            resolved_workspace_root=lambda: root,
        ),
    )


class ScriptedExecutor:
    """Writes success outputs for every step so the happy path completes."""

    async def run_once(self, agent_type, worktree, timeout_sec,
                       task_file=None, prompt=None, **_kwargs):
        text = Path(task_file).read_text(encoding="utf-8") if task_file else (prompt or "")
        m = re.search(r"在 `([^`]+\.md)` 现有文件末尾", text)
        if m:
            p = Path(m.group(1))
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(
                    "\n## 本轮目标\n\nX\n"
                    "\n## 开发计划\n\n1. a\n"
                    "\n## 用例清单\n\n| u | i | e | s |\n|---|---|---|---|\n| a | b | c | d |\n"
                )
            return ("ok", 0)
        m = re.search(r"在 `([^`]+\.md)` 写入", text)
        if m:
            p = Path(m.group(1))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("## 浓缩上下文\n- x\n\n## 疑点与风险\n- y\n", encoding="utf-8")
            return ("ok", 0)
        m = re.search(r"将自审结果写入 `([^`]+\.json)`", text)
        if m:
            p = Path(m.group(1))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"pass": True, "findings": []}), encoding="utf-8")
            return ("ok", 0)
        m = re.search(r"将结果写入 `([^`]+\.json)`", text)
        if m:
            p = Path(m.group(1))
            p.parent.mkdir(parents=True, exist_ok=True)
            payload = {"score": 90, "issues": [], "problem_category": None}
            p.write_text(json.dumps(payload), encoding="utf-8")
            return (f"```json\n{json.dumps(payload)}\n```", 0)
        return ("", 1)


async def _make_bare_clone(bare_dir: Path) -> None:
    """Create an initialised bare repo with one commit on ``main``.

    ``_s0_init`` runs ``git worktree add`` against this bare clone, so we
    need at least one commit to check out.
    """
    bare_dir.parent.mkdir(parents=True, exist_ok=True)
    src = bare_dir.with_suffix(".src")
    src.mkdir(parents=True, exist_ok=True)
    await run_git("init", cwd=str(src))
    await run_git("config", "user.email", "t@x", cwd=str(src))
    await run_git("config", "user.name", "T", cwd=str(src))
    await run_git("checkout", "-b", "main", cwd=str(src), check=False)
    (src / "README.md").write_text("# demo\n")
    await run_git("add", "README.md", cwd=str(src))
    await run_git("commit", "-m", "init", cwd=str(src))
    await run_git(
        "clone", "--bare", str(src), str(bare_dir),
    )


class _FakeInspector:
    """rev_parse against the actual bare clones so the route validator works."""

    def __init__(self, registry: RepoRegistryRepo) -> None:
        self._registry = registry

    async def rev_parse(
        self, repo_id: str, ref: str, *, _row=None,
    ) -> str | None:
        row = _row if _row is not None else await self._registry.get(repo_id)
        if row is None:
            return None
        bare = row.get("bare_clone_path")
        if not bare or not Path(bare).exists():
            return None
        try:
            out, _, rc = await run_git(
                "--git-dir", bare, "rev-parse", "--verify",
                f"{ref}^{{commit}}", check=False,
            )
        except Exception:
            return None
        if rc != 0:
            return None
        sha = out.strip()
        return sha or None


@pytest.fixture
async def client(tmp_path):
    test_app = FastAPI(title="cooagents-test-dev-works")
    ws_root = tmp_path / "ws"

    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
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
    iteration_notes = DevIterationNoteManager(db)
    executor = ScriptedExecutor()
    settings = _build_settings(workspace_root=ws_root)
    sm = DevWorkStateMachine(
        db=db, workspaces=workspaces, design_docs=design_docs,
        iteration_notes=iteration_notes, executor=executor,
        config=settings, registry=registry,
    )
    # Override workspaces_root so _s0_init's bare-clone lookup matches the
    # registry row written below.
    sm.workspaces_root = ws_root.resolve()

    repo_registry = RepoRegistryRepo(db)
    inspector = _FakeInspector(repo_registry)

    test_app.state.db = db
    test_app.state.workspaces = workspaces
    test_app.state.design_docs = design_docs
    test_app.state.iteration_notes = iteration_notes
    test_app.state.dev_work_sm = sm
    test_app.state.settings = settings
    test_app.state.repo_registry_repo = repo_registry
    test_app.state.repo_inspector = inspector
    test_app.state.start_time = time.time()

    from slowapi import Limiter
    from src.request_utils import client_ip
    limiter = Limiter(key_func=client_ip, default_limits=["1000/minute"])
    test_app.state.limiter = limiter
    limiter.enabled = False

    @test_app.exception_handler(NotFoundError)
    async def _nf(request, exc):
        return JSONResponse(status_code=404, content={"error": "not_found", "message": str(exc)})

    @test_app.exception_handler(ConflictError)
    async def _cf(request, exc):
        return JSONResponse(
            status_code=409,
            content={"error": "conflict", "message": str(exc),
                     "current_stage": exc.current_stage},
        )

    @test_app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(status_code=400, content={"error": "bad_request", "message": str(exc)})

    from routes.workspaces import router as ws_router
    from routes.dev_works import router as dev_router
    test_app.include_router(ws_router, prefix="/api/v1")
    test_app.include_router(dev_router, prefix="/api/v1")

    # Scaffolding — create workspace, persist+publish a design_doc, register repo.
    ws = await workspaces.create_with_scaffold(title="T", slug="w1")
    dd = await design_docs.persist(
        workspace_row=ws, slug="demo", version="1.0.0",
        markdown=DESIGN_FIXTURE.read_text(encoding="utf-8"),
        parent_version=None, needs_frontend_mockup=False, rubric_threshold=85,
    )
    await db.execute(
        "UPDATE design_docs SET status='published', published_at=? WHERE id=?",
        ("t", dd["id"]),
    )
    bare_dir = ws_root / ".coop" / "registry" / "repos" / "repo-test00000001.git"
    await _make_bare_clone(bare_dir)
    await repo_registry.upsert(
        id="repo-test00000001",
        name="testrepo",
        url=str(bare_dir.with_suffix(".src")),
        default_branch="main",
        bare_clone_path=str(bare_dir),
        role="backend",
    )
    await repo_registry.update_fetch_status(
        "repo-test00000001", status="healthy",
        bare_clone_path=str(bare_dir),
    )

    test_app.state._ws = ws
    test_app.state._dd = dd
    test_app.state._repo_id = "repo-test00000001"

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        ac._app = test_app
        yield ac

    await db.close()


def _payload(app, **overrides) -> dict:
    body = {
        "workspace_id": app.state._ws["id"],
        "design_doc_id": app.state._dd["id"],
        "prompt": "build login form",
        "repo_refs": [
            {
                "repo_id": app.state._repo_id,
                "mount_name": "backend",
                "base_branch": "main",
            }
        ],
    }
    body.update(overrides)
    return body


async def _wait_for_terminal(client: AsyncClient, dev_id: str, max_attempts: int = 200):
    for _ in range(max_attempts):
        r = await client.get(f"/api/v1/dev-works/{dev_id}")
        if r.status_code == 200 and r.json()["current_step"] in {
            "COMPLETED", "ESCALATED", "CANCELLED"
        }:
            return r.json()
        await asyncio.sleep(0.05)
    pytest.fail(f"dev_work {dev_id} did not reach terminal state in time")


async def test_create_201_returns_repo_refs(client):
    app = client._app
    r = await client.post("/api/v1/dev-works", json=_payload(app))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("dev-")
    assert r.headers["Location"] == f"/api/v1/dev-works/{body['id']}"
    refs = body["repo_refs"]
    assert len(refs) == 1
    assert refs[0]["repo_id"] == app.state._repo_id
    assert refs[0]["mount_name"] == "backend"
    assert refs[0]["devwork_branch"].startswith("devwork/")


async def test_list_requires_workspace_id(client):
    r = await client.get("/api/v1/dev-works")
    assert r.status_code == 422


async def test_get_missing_returns_404(client):
    r = await client.get("/api/v1/dev-works/dev-nope")
    assert r.status_code == 404


async def test_unknown_repo_id_returns_400(client):
    app = client._app
    r = await client.post("/api/v1/dev-works", json=_payload(app, repo_refs=[
        {"repo_id": "repo-doesnotexist", "mount_name": "backend",
         "base_branch": "main"},
    ]))
    assert r.status_code == 400
    assert "not registered" in r.json()["message"]


async def test_unhealthy_repo_returns_400(client):
    app = client._app
    # Force the repo into 'error' state.
    await app.state.repo_registry_repo.update_fetch_status(
        app.state._repo_id, status="error", err="boom",
    )
    r = await client.post("/api/v1/dev-works", json=_payload(app))
    assert r.status_code == 400
    assert "not healthy" in r.json()["message"]


async def test_missing_branch_returns_400(client):
    app = client._app
    r = await client.post("/api/v1/dev-works", json=_payload(app, repo_refs=[
        {"repo_id": app.state._repo_id, "mount_name": "backend",
         "base_branch": "no-such-branch"},
    ]))
    assert r.status_code == 400
    assert "not found" in r.json()["message"]


async def test_duplicate_mount_name_returns_422(client):
    """The pydantic boundary validator on CreateDevWorkRequest catches it."""
    app = client._app
    r = await client.post("/api/v1/dev-works", json=_payload(app, repo_refs=[
        {"repo_id": app.state._repo_id, "mount_name": "backend",
         "base_branch": "main"},
        {"repo_id": app.state._repo_id, "mount_name": "backend",
         "base_branch": "main"},
    ]))
    assert r.status_code == 422


async def test_duplicate_active_devwork_returns_409(client):
    app = client._app
    r1 = await client.post("/api/v1/dev-works", json=_payload(app))
    assert r1.status_code == 201
    r2 = await client.post("/api/v1/dev-works", json=_payload(app))
    if r2.status_code != 409:
        final = await _wait_for_terminal(client, r1.json()["id"])
        assert final["current_step"] in {"COMPLETED", "ESCALATED", "CANCELLED"}


async def test_missing_design_doc_returns_404(client):
    app = client._app
    r = await client.post("/api/v1/dev-works", json=_payload(
        app, design_doc_id="des-nope",
    ))
    assert r.status_code == 404


async def test_post_repos_ensure_404(client):
    """Phase 4 deleted POST /repos/ensure entirely."""
    r = await client.post(
        "/api/v1/repos/ensure", json={"repo_path": "/x"},
    )
    assert r.status_code == 404
