"""Internal worker endpoints for agent execution leases.

Mounted under the normal auth dependency; remote workers authenticate with
``X-Agent-Token`` through the existing agent-token path.
"""
from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Query, Request

from src.exceptions import BadRequestError, NotFoundError


router = APIRouter()


class ExecutionStartedRequest(BaseModel):
    pid: int
    pgid: int | None = None
    pid_starttime: str | None = None
    cwd: str | None = None
    worker_pid: int | None = None
    worker_pid_starttime: str | None = None


class ExecutionExitedRequest(BaseModel):
    exit_code: int | None = None


class CleanupResultRequest(BaseModel):
    state: str
    exit_code: int | None = None
    cleanup_reason: str | None = None


@router.post("/internal/agent-executions/{execution_id}/started")
async def mark_execution_started(
    execution_id: str, payload: ExecutionStartedRequest, request: Request
):
    repo = request.app.state.agent_execution_repo
    await repo.mark_process_started(
        execution_id,
        pid=payload.pid,
        pgid=payload.pgid,
        pid_starttime=payload.pid_starttime,
        cwd=payload.cwd,
        worker_pid=payload.worker_pid,
        worker_pid_starttime=payload.worker_pid_starttime,
    )
    row = await repo.get(execution_id)
    if row is None:
        raise NotFoundError(f"agent execution not found: {execution_id!r}")
    return row


@router.post("/internal/agent-executions/{execution_id}/heartbeat")
async def heartbeat_execution(execution_id: str, request: Request):
    repo = request.app.state.agent_execution_repo
    await repo.heartbeat(execution_id)
    row = await repo.get(execution_id)
    if row is None:
        raise NotFoundError(f"agent execution not found: {execution_id!r}")
    return row


@router.post("/internal/agent-executions/{execution_id}/exited")
async def mark_execution_exited(
    execution_id: str, payload: ExecutionExitedRequest, request: Request
):
    repo = request.app.state.agent_execution_repo
    await repo.mark_exited(execution_id, exit_code=payload.exit_code)
    row = await repo.get(execution_id)
    if row is None:
        raise NotFoundError(f"agent execution not found: {execution_id!r}")
    return row


@router.get("/internal/agent-executions/expired")
async def list_expired_executions(
    request: Request,
    host_id: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
):
    repo = request.app.state.agent_execution_repo
    return await repo.list_expired_for_host(host_id, limit=limit)


@router.post("/internal/agent-executions/{execution_id}/cleanup-result")
async def mark_cleanup_result(
    execution_id: str, payload: CleanupResultRequest, request: Request
):
    if payload.state not in {"stale", "terminated", "killed", "abandoned"}:
        raise BadRequestError(
            "cleanup result state must be stale, terminated, killed, or abandoned"
        )
    repo = request.app.state.agent_execution_repo
    await repo.mark_state(
        execution_id,
        state=payload.state,
        exit_code=payload.exit_code,
        cleanup_reason=payload.cleanup_reason,
    )
    row = await repo.get(execution_id)
    if row is None:
        raise NotFoundError(f"agent execution not found: {execution_id!r}")
    return row
