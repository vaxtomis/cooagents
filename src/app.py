import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from src.config import load_settings, load_agent_hosts
from src.database import Database
from src.artifact_manager import ArtifactManager
from src.host_manager import HostManager
from src.job_manager import JobManager
from src.acpx_executor import AcpxExecutor
from src.webhook_notifier import WebhookNotifier
from src.merge_manager import MergeManager
from src.state_machine import StateMachine
from src.trace_emitter import TraceEmitter
from src.trace_middleware import TraceMiddleware
from src.exceptions import NotFoundError, ConflictError, BadRequestError
from src.skill_deployer import deploy_skills


@asynccontextmanager
async def lifespan(app: FastAPI):
    project_root = Path(__file__).resolve().parents[1]
    coop_dir = project_root / ".coop"
    settings = load_settings()
    await deploy_skills(settings)

    # Tracing infrastructure — create emitter first (no DB yet)
    trace_emitter = TraceEmitter(enabled=settings.tracing.enabled)

    db = Database(
        db_path=settings.database.path,
        schema_path="db/schema.sql",
        on_trace_event=trace_emitter.emit_sync if settings.tracing.enabled else None,
    )
    await db.connect()

    # Wire DB into emitter after connect
    trace_emitter.set_db(db)
    consumer_task = asyncio.create_task(trace_emitter.start_consumer()) if settings.tracing.enabled else None

    artifacts = ArtifactManager(db, project_root=project_root)
    hosts = HostManager(db)
    jobs = JobManager(db, coop_dir=coop_dir, project_root=project_root)
    webhooks = WebhookNotifier(
        db,
        openclaw_hooks=settings.openclaw.hooks if settings.openclaw.hooks.enabled else None,
    )
    merger = MergeManager(db, webhooks)

    executor = AcpxExecutor(
        db, jobs, hosts, artifacts, webhooks,
        config=settings, coop_dir=coop_dir, project_root=project_root,
    )
    sm = StateMachine(
        db, artifacts, hosts, executor, webhooks, merger,
        coop_dir=coop_dir, config=settings, job_manager=jobs, project_root=project_root,
        trace_emitter=trace_emitter,
    )
    executor.set_state_machine(sm)

    agent_config = load_agent_hosts()
    await hosts.load_from_config(agent_config)
    await executor.restore_on_startup()

    # Background scheduler
    from src.scheduler import Scheduler
    scheduler = Scheduler(db, hosts, jobs, executor, webhooks, settings, state_machine=sm)
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


app = FastAPI(title="cooagents", version="0.2.0", lifespan=lifespan)
app.add_middleware(TraceMiddleware)  # emitter resolved lazily from app.state


@app.exception_handler(NotFoundError)
async def not_found_handler(request, exc):
    return JSONResponse(status_code=404, content={"error": "not_found", "message": str(exc)})


@app.exception_handler(ConflictError)
async def conflict_handler(request, exc):
    return JSONResponse(status_code=409, content={"error": "conflict", "message": str(exc), "current_stage": exc.current_stage})


@app.exception_handler(BadRequestError)
async def bad_request_handler(request, exc):
    return JSONResponse(status_code=400, content={"error": "bad_request", "message": str(exc)})


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


from routes.runs import router as runs_router
from routes.artifacts import router as artifacts_router
from routes.agent_hosts import router as hosts_router
from routes.webhooks import router as webhooks_router
from routes.repos import router as repos_router
from routes.diagnostics import create_diagnostics_router

app.include_router(runs_router, prefix="/api/v1")
app.include_router(artifacts_router, prefix="/api/v1")
app.include_router(hosts_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(repos_router, prefix="/api/v1")
app.include_router(create_diagnostics_router(), prefix="/api/v1")
