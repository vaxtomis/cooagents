"""Workspace lifecycle routes (Phase 2).

Endpoints:
  POST   /api/v1/workspaces           — create (DB + FS)
  GET    /api/v1/workspaces           — list, ?status=active|archived
  GET    /api/v1/workspaces/{id}      — fetch one
  DELETE /api/v1/workspaces/{id}      — soft-archive (DB + workspace.md)
  POST   /api/v1/workspaces/sync      — reconcile FS vs DB (FS wins)

Delegates all business logic to WorkspaceManager.
"""
from fastapi import APIRouter, Request, Response
from slowapi import Limiter

from src.exceptions import BadRequestError, NotFoundError
from src.models import (
    CreateWorkspaceRequest,
    MaterializeReportResponse,
    RegenerateIndexResponse,
    WorkspaceSyncReport,
)
from src.request_utils import client_ip

limiter = Limiter(key_func=client_ip)

router = APIRouter(tags=["workspaces"])


@router.post("/workspaces", status_code=201)
@limiter.limit("20/minute")
async def create_workspace(
    req: CreateWorkspaceRequest, request: Request, response: Response
):
    wm = request.app.state.workspaces
    ws = await wm.create_with_scaffold(title=req.title, slug=req.slug)
    response.headers["Location"] = f"/api/v1/workspaces/{ws['id']}"
    return ws


@router.get("/workspaces")
async def list_workspaces(request: Request, status: str | None = None):
    wm = request.app.state.workspaces
    if status and status not in {"active", "archived"}:
        raise BadRequestError("status must be 'active' or 'archived'")
    rows = await wm.list(status=status)
    return [dict(r) for r in rows]


# Static paths MUST be declared before parameterized siblings so a future
# `POST /workspaces/{workspace_id}` does not shadow `/workspaces/sync`.
@router.post("/workspaces/sync")
@limiter.limit("5/minute")
async def sync_all(request: Request) -> WorkspaceSyncReport:
    wm = request.app.state.workspaces
    report = await wm.reconcile()
    return WorkspaceSyncReport(**report)


@router.post("/workspaces/{workspace_id}/materialize")
@limiter.limit("10/minute")
async def materialize_workspace(
    workspace_id: str, request: Request,
) -> MaterializeReportResponse:
    """Hydrate local FS from DB+OSS for a workspace (Phase 5)."""
    ws_sync = request.app.state.workspace_sync
    report = await ws_sync.materialize(workspace_id)
    return MaterializeReportResponse(
        workspace_id=report.workspace_id,
        pulled=report.pulled,
        skipped=report.skipped,
        missing_oss=report.missing_oss,
        errors=list(report.errors),
    )


@router.post("/workspaces/{workspace_id}/regenerate-index")
@limiter.limit("20/minute")
async def regenerate_index(
    workspace_id: str, request: Request,
) -> RegenerateIndexResponse:
    """Re-render workspace.md from DB state via CAS (Phase 5)."""
    wm = request.app.state.workspaces
    result = await wm.regenerate_workspace_md(workspace_id)
    return RegenerateIndexResponse(**result)


@router.get("/workspaces/{workspace_id}")
async def get_workspace(workspace_id: str, request: Request):
    wm = request.app.state.workspaces
    row = await wm.get(workspace_id)
    if not row:
        raise NotFoundError(f"workspace {workspace_id!r} not found")
    return dict(row)


@router.delete("/workspaces/{workspace_id}", status_code=204)
@limiter.limit("20/minute")
async def archive_workspace(workspace_id: str, request: Request):
    wm = request.app.state.workspaces
    await wm.archive_with_scaffold(workspace_id)
    return Response(status_code=204)
