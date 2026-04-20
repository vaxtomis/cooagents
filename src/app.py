import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from src.acpx_executor import AcpxExecutor
from src.artifact_manager import ArtifactManager
from src.auth import AuthError, AuthSettings, get_current_user
from src.request_utils import client_ip
from src.config import load_agent_hosts, load_settings
from src.database import Database
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.host_manager import HostManager
from src.job_manager import JobManager
from src.merge_manager import MergeManager
from src.skill_deployer import deploy_skills
from src.sse import SSEBroadcaster
from src.state_machine import StateMachine
from src.trace_emitter import TraceEmitter
from src.trace_middleware import TraceMiddleware
from src.webhook_notifier import WebhookNotifier


@asynccontextmanager
async def lifespan(app: FastAPI):
    project_root = Path(__file__).resolve().parents[1]
    coop_dir = project_root / ".coop"
    settings = load_settings()
    # Fail fast if auth env is missing. Public deployment must not boot without it.
    app.state.auth = AuthSettings.from_env()
    await deploy_skills(settings)

    sse_broadcaster = SSEBroadcaster()
    trace_emitter = TraceEmitter(enabled=settings.tracing.enabled, broadcaster=sse_broadcaster)

    db = Database(
        db_path=settings.database.path,
        schema_path="db/schema.sql",
        on_trace_event=trace_emitter.emit_sync if settings.tracing.enabled else None,
    )
    await db.connect()

    trace_emitter.set_db(db)
    consumer_task = asyncio.create_task(trace_emitter.start_consumer()) if settings.tracing.enabled else None

    artifacts = ArtifactManager(db, project_root=project_root)
    hosts = HostManager(db)
    jobs = JobManager(db, coop_dir=coop_dir, project_root=project_root)
    webhooks = WebhookNotifier(
        db,
        openclaw_hooks=settings.openclaw.hooks if settings.openclaw.hooks.enabled else None,
        trace_emitter=trace_emitter,
        artifact_manager=artifacts,
    )
    merger = MergeManager(db, webhooks)

    executor = AcpxExecutor(
        db,
        jobs,
        hosts,
        artifacts,
        webhooks,
        config=settings,
        coop_dir=coop_dir,
        project_root=project_root,
        trace_emitter=trace_emitter,
    )
    sm = StateMachine(
        db,
        artifacts,
        hosts,
        executor,
        webhooks,
        merger,
        coop_dir=coop_dir,
        config=settings,
        job_manager=jobs,
        project_root=project_root,
        trace_emitter=trace_emitter,
    )
    executor.set_state_machine(sm)

    agent_config = load_agent_hosts()
    await hosts.load_from_config(agent_config)
    await executor.restore_on_startup()

    from src.scheduler import Scheduler

    scheduler = Scheduler(
        db,
        hosts,
        jobs,
        executor,
        webhooks,
        settings,
        state_machine=sm,
        trace_emitter=trace_emitter,
    )
    await scheduler.start()

    app.state.db = db
    app.state.sm = sm
    app.state.artifacts = artifacts
    app.state.hosts = hosts
    app.state.jobs = jobs
    app.state.executor = executor
    app.state.webhooks = webhooks
    app.state.merger = merger
    app.state.settings = settings
    app.state.scheduler = scheduler
    app.state.trace_emitter = trace_emitter
    app.state.sse_broadcaster = sse_broadcaster
    app.state.start_time = time.time()

    yield

    await scheduler.stop()
    if consumer_task:
        trace_emitter.stop()
        try:
            await asyncio.wait_for(consumer_task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
    await webhooks.close()
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
app.add_middleware(TraceMiddleware)

# Global rate limiter. Per-route overrides via @limiter.limit(...) decorator.
# Why: public-web deployment needs resource protection on mutation + upload
# endpoints. `client_ip` honours X-Forwarded-For from trusted proxies so
# buckets actually separate per-user behind nginx/caddy.
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


@app.exception_handler(AuthError)
async def auth_error_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "unauthenticated", "message": str(exc)},
    )


@app.get("/health")
async def health(request: Request):
    db = request.app.state.db
    active_runs = await db.fetchone("SELECT COUNT(*) as c FROM runs WHERE status='running'")
    active_jobs = await db.fetchone("SELECT COUNT(*) as c FROM jobs WHERE status IN ('starting','running')")
    return {
        "status": "ok",
        "uptime": int(time.time() - request.app.state.start_time),
        "db": "connected",
        "active_runs": active_runs["c"],
        "active_jobs": active_jobs["c"],
    }


from fastapi import Depends

from routes.agent_hosts import router as hosts_router
from routes.artifacts import router as artifacts_router
from routes.auth import router as auth_router
from routes.diagnostics import create_diagnostics_router
from routes.events import create_events_router
from routes.repos import router as repos_router
from routes.runs import router as runs_router
from routes.sse import create_sse_router
from routes.webhooks import router as webhooks_router

# Auth endpoints are public. Everything else requires a valid session.
auth_required = [Depends(get_current_user)]

app.include_router(auth_router, prefix="/api/v1")
app.include_router(runs_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(artifacts_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(hosts_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(webhooks_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(repos_router, prefix="/api/v1", dependencies=auth_required)
app.include_router(create_events_router(), prefix="/api/v1", dependencies=auth_required)
app.include_router(create_sse_router(), prefix="/api/v1", dependencies=auth_required)
app.include_router(create_diagnostics_router(), prefix="/api/v1", dependencies=auth_required)
mount_dashboard_spa(app)