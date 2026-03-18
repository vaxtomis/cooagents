from fastapi import APIRouter, Request, HTTPException
from src.models import (
    CreateRunRequest, ApproveRequest, RejectRequest, RetryRequest,
    RecoverRequest, SubmitRequirementRequest, ResolveConflictRequest,
)
from src.exceptions import NotFoundError, ConflictError

router = APIRouter(tags=["runs"])


@router.post("/runs", status_code=201)
async def create_run(req: CreateRunRequest, request: Request):
    sm = request.app.state.sm
    result = await sm.create_run(
        req.ticket, req.repo_path, req.description, req.preferences,
        notify_channel=req.notify_channel, notify_to=req.notify_to,
        repo_url=req.repo_url,
    )
    return result


@router.get("/runs")
async def list_runs(request: Request, status: str = None, limit: int = 20, offset: int = 0):
    db = request.app.state.db
    sql = "SELECT * FROM runs"
    params = []
    if status:
        sql += " WHERE status=?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await db.fetchall(sql, tuple(params))
    return [dict(r) for r in rows]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    db = request.app.state.db
    run = await db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
    if not run:
        raise NotFoundError(f"Run {run_id} not found")
    run = dict(run)
    run["run_id"] = run["id"]

    # Enrich with related data
    steps = await db.fetchall("SELECT * FROM steps WHERE run_id=? ORDER BY created_at", (run_id,))
    approvals = await db.fetchall("SELECT * FROM approvals WHERE run_id=? ORDER BY created_at", (run_id,))
    events = await db.fetchall("SELECT * FROM events WHERE run_id=? ORDER BY created_at DESC LIMIT 20", (run_id,))
    artifacts = await db.fetchall("SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at", (run_id,))

    run["steps"] = [dict(s) for s in steps]
    run["approvals"] = [dict(a) for a in approvals]
    run["recent_events"] = [dict(e) for e in events]
    run["artifacts"] = [dict(a) for a in artifacts]
    return run


@router.post("/runs/{run_id}/tick")
async def tick_run(run_id: str, request: Request):
    sm = request.app.state.sm
    return await sm.tick(run_id)


@router.post("/runs/{run_id}/approve")
async def approve_run(run_id: str, req: ApproveRequest, request: Request):
    sm = request.app.state.sm
    return await sm.approve(run_id, req.gate.value, req.by, req.comment)


@router.post("/runs/{run_id}/reject")
async def reject_run(run_id: str, req: RejectRequest, request: Request):
    sm = request.app.state.sm
    return await sm.reject(run_id, req.gate.value, req.by, req.reason)


@router.post("/runs/{run_id}/retry")
async def retry_run(run_id: str, req: RetryRequest, request: Request):
    sm = request.app.state.sm
    return await sm.retry(run_id, req.by, req.note)


@router.post("/runs/{run_id}/recover")
async def recover_run(run_id: str, req: RecoverRequest, request: Request):
    executor = request.app.state.executor
    await executor.recover(run_id, req.action.value)
    sm = request.app.state.sm
    return await sm.tick(run_id)


@router.post("/runs/{run_id}/submit-requirement")
async def submit_requirement(run_id: str, req: SubmitRequirementRequest, request: Request):
    sm = request.app.state.sm
    return await sm.submit_requirement(run_id, req.content)


@router.post("/runs/{run_id}/resolve-conflict")
async def resolve_conflict(run_id: str, req: ResolveConflictRequest, request: Request):
    sm = request.app.state.sm
    return await sm.resolve_conflict(run_id, req.by)


@router.delete("/runs/{run_id}")
async def cancel_run(run_id: str, request: Request, cleanup: bool = False):
    sm = request.app.state.sm
    return await sm.cancel(run_id, cleanup)
