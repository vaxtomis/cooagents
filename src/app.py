import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from src.acpx_executor import AcpxExecutor
from src.auth import AuthError, AuthSettings, get_current_user
from src.request_utils import client_ip
from src.config import load_settings
from src.database import Database
from src.design_doc_manager import DesignDocManager
from src.design_work_sm import DesignWorkStateMachine
from src.dev_iteration_note_manager import DevIterationNoteManager
from src.dev_work_sm import DevWorkStateMachine
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.skill_deployer import deploy_skills
from src.storage import (
    WorkspaceFileRegistry,
    WorkspaceFilesRepo,
    build_file_store,
)
from src.webhook_notifier import WebhookNotifier
from src.workspace_manager import WorkspaceManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    project_root = Path(__file__).resolve().parents[1]
    coop_dir = project_root / ".coop"
    settings = load_settings()
    app.state.auth = AuthSettings.from_env()
    await deploy_skills(settings)

    db = Database(db_path=settings.database.path, schema_path="db/schema.sql")
    await db.connect()

    webhooks = WebhookNotifier(db, settings=settings)
    await webhooks.bootstrap_builtin_subscriptions(settings)

    workspaces_root = settings.security.resolved_workspace_root()
    store = build_file_store(settings, workspaces_root)
    files_repo = WorkspaceFilesRepo(db)
    registry = WorkspaceFileRegistry(store=store, repo=files_repo)
    workspaces = WorkspaceManager(
        db,
        project_root=project_root,
        workspaces_root=workspaces_root,
        webhooks=webhooks,
        registry=registry,
    )
    try:
        report = await workspaces.reconcile()
        if report["fs_only"] or report["db_only"]:
            logging.getLogger(__name__).warning(
                "workspace reconcile: fs_only=%s db_only=%s",
                report["fs_only"],
                report["db_only"],
            )
    except Exception:
        logging.getLogger(__name__).exception("workspace reconcile failed; continuing startup")

    # Phase 8a: agent host registry, dispatcher, and probe loop. Construct
    # before the executor / SMs so they can be injected.
    from src.agent_hosts import (
        AgentDispatchRepo, AgentHostRepo, HealthProbeLoop, SshDispatcher,
    )

    agent_host_repo = AgentHostRepo(db)
    agent_dispatch_repo = AgentDispatchRepo(db)
    try:
        sync_report = await agent_host_repo.sync_from_config(settings.agents)
        logging.getLogger(__name__).info(
            "agent_hosts sync: upserted=%s marked_unknown=%s",
            sync_report["upserted"], sync_report["marked_unknown"],
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "agent_hosts sync_from_config failed; continuing startup"
        )
    ssh_dispatcher = SshDispatcher(
        agent_host_repo,
        ssh_timeout_s=settings.health_check.ssh_timeout,
        strict_host_key=settings.agents.ssh_strict_host_key,
        known_hosts_path=settings.agents.ssh_known_hosts_path,
        workspaces_root=str(workspaces_root),
    )
    health_probe_loop = HealthProbeLoop(
        ssh_dispatcher, agent_host_repo,
        interval_s=settings.health_check.interval,
    )

    executor = AcpxExecutor(
        db,
        webhooks,
        config=settings,
        coop_dir=coop_dir,
        project_root=project_root,
        ssh_dispatcher=ssh_dispatcher,
    )
    design_docs = DesignDocManager(db, registry=registry)
    design_work_sm = DesignWorkStateMachine(
        db=db,
        workspaces=workspaces,
        design_docs=design_docs,
        executor=executor,
        config=settings,
        registry=registry,
        webhooks=webhooks,
        agent_host_repo=agent_host_repo,
        agent_dispatch_repo=agent_dispatch_repo,
    )
    iteration_notes = DevIterationNoteManager(db)
    dev_work_sm = DevWorkStateMachine(
        db=db,
        workspaces=workspaces,
        design_docs=design_docs,
        iteration_notes=iteration_notes,
        executor=executor,
        config=settings,
        registry=registry,
        webhooks=webhooks,
        agent_host_repo=agent_host_repo,
        agent_dispatch_repo=agent_dispatch_repo,
    )

    app.state.db = db
    app.state.workspaces = workspaces
    app.state.design_docs = design_docs
    app.state.design_work_sm = design_work_sm
    app.state.iteration_notes = iteration_notes
    app.state.dev_work_sm = dev_work_sm
    app.state.executor = executor
    app.state.webhooks = webhooks
    app.state.registry = registry
    app.state.settings = settings
    app.state.agent_host_repo = agent_host_repo
    app.state.agent_dispatch_repo = agent_dispatch_repo
    app.state.ssh_dispatcher = ssh_dispatcher
    app.state.health_probe_loop = health_probe_loop
    app.state.start_time = time.time()

    health_probe_loop.start()

    yield

    await health_probe_loop.stop()
    await webhooks.close()
    if hasattr(store, "close"):
        try:
            await store.close()
        except Exception:
            logging.getLogger(__name__).exception(
                "FileStore close failed on shutdown",
            )
    await db.close()


def mount_dashboard_spa(app: FastAPI, project_root: Path | None = None) -> None:
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[1]
    dist_dir = root / "web" / "dist"
    index_file = dist_dir / "index.html"
    if not index_file.exists():
        return

    dist_root = dist_dir.resolve()

    def _resolve_asset(full_path: str) -> Path | None:
        candidate = (dist_root / full_path).resolve()
        try:
            candidate.relative_to(dist_root)
        except ValueError:
            return None
        return candidate

    @app.get("/", include_in_schema=False)
    async def spa_index():
        return FileResponse(index_file)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)

        candidate = _resolve_asset(full_path) if full_path else index_file
        if candidate and candidate.is_file():
            return FileResponse(candidate)

        if Path(full_path).suffix:
            raise HTTPException(status_code=404)

        return FileResponse(index_file)


app = FastAPI(title="cooagents", version="0.2.0", lifespan=lifespan)

limiter = Limiter(key_func=client_ip, default_limits=["300/minute"])
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limited", "message": "Too many requests"},
    )


app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(NotFoundError)
async def not_found_handler(request, exc):
    return JSONResponse(status_code=404, content={"error": "not_found", "message": str(exc)})


@app.exception_handler(ConflictError)
async def conflict_handler(request, exc):
    return JSONResponse(status_code=409, content={"error": "conflict", "message": str(exc), "current_stage": exc.current_stage})


@app.exception_handler(BadRequestError)
async def bad_request_handler(request, exc):
    return JSONResponse(status_code=400, content={"error": "bad_request", "message": str(exc)})


@app.exception_handler(NotImplementedError)
async def not_implemented_handler(request, exc):
    logging.getLogger(__name__).exception(
        "NotImplementedError at %s: %s", request.url.path, exc
    )
    return JSONResponse(
        status_code=501,
        content={"error": "not_implemented", "message": str(exc)},
    )


@app.exception_handler(AuthError)
async def auth_error_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "unauthenticated", "message": str(exc)},
    )


@app.get("/health")
async def health(request: Request):
    db = request.app.state.db
    ws = await db.fetchone("SELECT COUNT(*) AS c FROM workspaces WHERE status='active'")
    dw = await db.fetchone(
        "SELECT COUNT(*) AS c FROM dev_works "
        "WHERE current_step NOT IN ('COMPLETED','ESCALATED','CANCELLED')"
    )
    return {
        "status": "ok",
        "uptime": int(time.time() - request.app.state.start_time),
        "db": "connected",
        "active_workspaces": ws["c"] if ws else 0,
        "active_dev_works": dw["c"] if dw else 0,
    }


from fastapi import Depends

from routes.auth import router as auth_router
from routes.design_docs import router as design_docs_router
from routes.design_works import router as design_works_router
from routes.dev_iteration_notes import router as dev_iteration_notes_router
from routes.dev_works import router as dev_works_router
from routes.gates import router as gates_router
from routes.metrics import router as metrics_router
from routes.agent_hosts import router as agent_hosts_router
from routes.repos import router as repos_router
from routes.reviews import router as reviews_router
from routes.webhooks import router as webhooks_router
from routes.workspace_events import router as workspace_events_router
from routes.workspaces import router as workspaces_router

# Auth endpoints are public. Everything else requires a valid session.
auth_required = [Depends(get_current_user)]

app.include_router(auth_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(repos_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(workspaces_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(design_works_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(design_docs_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(dev_works_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(dev_iteration_notes_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(reviews_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(workspace_events_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(gates_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(metrics_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(agent_hosts_router, prefix="/api/v1", dependencies=auth_required)
mount_dashboard_spa(app)
