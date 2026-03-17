import time
from contextlib import asynccontextmanager
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
from src.exceptions import NotFoundError, ConflictError


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    db = Database(db_path=settings.database.path, schema_path="db/schema.sql")
    await db.connect()

    artifacts = ArtifactManager(db)
    hosts = HostManager(db)
    jobs = JobManager(db)
    webhooks = WebhookNotifier(db)
    merger = MergeManager(db, webhooks)

    executor = AcpxExecutor(db, jobs, hosts, artifacts, webhooks, config=settings, coop_dir=".coop")
    sm = StateMachine(db, artifacts, hosts, executor, webhooks, merger, coop_dir=".coop", config=settings, job_manager=jobs)
    executor.set_state_machine(sm)

    agent_config = load_agent_hosts()
    await hosts.load_from_config(agent_config)
    await executor.restore_on_startup()

    # Background scheduler
    from src.scheduler import Scheduler
    scheduler = Scheduler(db, hosts, jobs, executor, webhooks, settings)
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
    app.state.start_time = time.time()

    yield

    await scheduler.stop()
    await webhooks.close()
    await db.close()


app = FastAPI(title="cooagents", version="0.2.0", lifespan=lifespan)


@app.exception_handler(NotFoundError)
async def not_found_handler(request, exc):
    return JSONResponse(status_code=404, content={"error": "not_found", "message": str(exc)})


@app.exception_handler(ConflictError)
async def conflict_handler(request, exc):
    return JSONResponse(status_code=409, content={"error": "conflict", "message": str(exc), "current_stage": exc.current_stage})


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


# Import and register routers — these will be created in Tasks 14-17
# We'll import them with try/except so the app can start even if routes don't exist yet
try:
    from routes.runs import router as runs_router
    app.include_router(runs_router, prefix="/api/v1")
except ImportError:
    pass

try:
    from routes.artifacts import router as artifacts_router
    app.include_router(artifacts_router, prefix="/api/v1")
except ImportError:
    pass

try:
    from routes.agent_hosts import router as hosts_router
    app.include_router(hosts_router, prefix="/api/v1")
except ImportError:
    pass

try:
    from routes.webhooks import router as webhooks_router
    app.include_router(webhooks_router, prefix="/api/v1")
except ImportError:
    pass

try:
    from routes.repos import router as repos_router
    app.include_router(repos_router, prefix="/api/v1")
except ImportError:
    pass
