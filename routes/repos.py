from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from src.models import MergeRequest, EnsureRepoRequest
from src.exceptions import BadRequestError
from src.path_validation import (
    RepoPathError,
    RepoUrlError,
    validate_repo_path,
    validate_repo_url,
)
from src.request_utils import client_ip

limiter = Limiter(key_func=client_ip)

router = APIRouter(tags=["repos"])


@router.post("/repos/ensure")
@limiter.limit("10/minute")
async def ensure_repo(req: EnsureRepoRequest, request: Request):
    from src.git_utils import ensure_repo as _ensure_repo

    security = request.app.state.settings.security
    try:
        safe_path = validate_repo_path(req.repo_path, security.resolved_workspace_root())
    except RepoPathError as exc:
        raise BadRequestError(str(exc))

    if req.repo_url:
        try:
            validate_repo_url(
                req.repo_url,
                security.allowed_repo_hosts,
                security.allowed_repo_schemes,
            )
        except RepoUrlError as exc:
            raise BadRequestError(str(exc))

    try:
        result = await _ensure_repo(str(safe_path), req.repo_url)
    except ValueError as e:
        raise BadRequestError(str(e))
    status_code = 200 if result == "exists" else 201
    return JSONResponse(status_code=status_code, content={"status": result})


@router.get("/runs/{run_id}/jobs")
async def list_jobs(run_id: str, request: Request):
    jm = request.app.state.jobs
    return await jm.get_jobs(run_id)


@router.get("/runs/{run_id}/jobs/{job_id}/output")
async def get_job_output(run_id: str, job_id: str, request: Request):
    jm = request.app.state.jobs
    output = await jm.get_output(job_id)
    return {"job_id": job_id, "output": output}


@router.get("/runs/{run_id}/conflicts")
async def get_conflicts(run_id: str, request: Request):
    db = request.app.state.db
    run = await db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
    if not run:
        from src.exceptions import NotFoundError
        raise NotFoundError(f"Run {run_id} not found")
    run = dict(run)
    wt = run.get("dev_worktree")
    if not wt:
        return {"conflicts": []}
    from src.git_utils import check_conflicts
    conflicts = await check_conflicts(wt)
    return {"conflicts": conflicts}


@router.post("/runs/{run_id}/merge")
async def merge_run(run_id: str, request: Request, req: MergeRequest = None):
    merger = request.app.state.merger
    db = request.app.state.db
    run = await db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
    if not run:
        from src.exceptions import NotFoundError
        raise NotFoundError(f"Run {run_id} not found")
    run = dict(run)
    priority = req.priority if req else 0
    await merger.enqueue(run_id, run.get("dev_branch", ""), priority=priority)
    return {"status": "queued"}


@router.post("/runs/{run_id}/merge-skip")
async def merge_skip(run_id: str, request: Request):
    merger = request.app.state.merger
    await merger.skip(run_id)
    return {"status": "skipped"}


@router.get("/repos")
async def list_repo_runs(request: Request, path: str = None):
    db = request.app.state.db
    if path:
        rows = await db.fetchall(
            "SELECT * FROM runs WHERE repo_path=? ORDER BY created_at DESC", (path,)
        )
    else:
        rows = await db.fetchall("SELECT DISTINCT repo_path FROM runs")
    return [dict(r) for r in rows]


@router.get("/repos/merge-queue")
async def get_merge_queue(request: Request, path: str = None):
    merger = request.app.state.merger
    return await merger.list_queue()
