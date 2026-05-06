"""Workspace lifecycle routes (Phase 2).

Endpoints:
  POST   /api/v1/workspaces                — create (DB + FS)
  GET    /api/v1/workspaces                — list, ?status=active|archived
  GET    /api/v1/workspaces/{id}           — fetch one
  DELETE /api/v1/workspaces/{id}           — soft-archive (DB + workspace.md)
  POST   /api/v1/workspaces/sync           — reconcile FS vs DB (FS wins)
  GET    /api/v1/workspaces/{id}/files     — list workspace_files index (Phase 8b)
  POST   /api/v1/workspaces/{id}/files     — agent worker write-back (Phase 8b)

Delegates all business logic to WorkspaceManager.
"""
from urllib.parse import quote

from fastapi import APIRouter, Header, Request, Response, UploadFile, File, Form, Query
from slowapi import Limiter

from src.exceptions import BadRequestError, NotFoundError
from src.models import CreateWorkspaceRequest, WorkspacePage, WorkspaceSyncReport
from src.request_utils import client_ip

limiter = Limiter(key_func=client_ip)

# Phase 8b: cap worker write-back uploads. The endpoint buffers the body
# in RAM (UploadFile.read), so an unbounded payload would OOM cooagents.
# 25 MiB matches the largest design-doc/screenshot we expect agents to
# emit; raise deliberately if a real workload needs more.
MAX_WORKER_UPLOAD_BYTES = 25 * 1024 * 1024

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
async def list_workspaces(
    request: Request,
    status: str | None = None,
    query: str | None = None,
    sort: str = "created_desc",
    limit: int = Query(12, ge=1, le=100),
    offset: int = Query(0, ge=0),
    paginate: bool = False,
):
    wm = request.app.state.workspaces
    if status and status not in {"active", "archived"}:
        raise BadRequestError("status must be 'active' or 'archived'")
    if paginate:
        page = await wm.list_page(
            status=status,
            query=query,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        return WorkspacePage(**page)
    rows = await wm.list(status=status, query=query, sort=sort)
    return [dict(r) for r in rows]


# Static paths MUST be declared before parameterized siblings so a future
# `POST /workspaces/{workspace_id}` does not shadow `/workspaces/sync`.
@router.post("/workspaces/sync")
@limiter.limit("5/minute")
async def sync_all(request: Request) -> WorkspaceSyncReport:
    """Reconcile FS vs DB (single-mode FS-wins).

    Inserts DB rows for FS-only dirs, archives DB rows whose local dir is
    missing.
    """
    wm = request.app.state.workspaces
    report = await wm.reconcile()
    return WorkspaceSyncReport(**report)


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


# ---------------------------------------------------------------------------
# Phase 8b — workspace_files HTTP plane for the remote agent worker.
#
# The worker reads the active index via GET to drive materialize, and writes
# back diff outputs via POST. Both endpoints reuse the standard auth chain
# (cookie or X-Agent-Token); cooagents remains the sole writer of OSS + DB.
# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/files")
async def list_workspace_files(workspace_id: str, request: Request):
    """Return the workspace_files index for *workspace_id*.

    Used by the agent worker's materialize step: HEAD per row, GET on
    mismatches. The response includes ``status`` so the worker can
    detect an archived workspace and abort before materialize.
    """
    wm = request.app.state.workspaces
    ws = await wm.get(workspace_id)
    if not ws:
        raise NotFoundError(f"workspace {workspace_id!r} not found")
    repo = request.app.state.registry.repo
    rows = await repo.list_for_workspace(workspace_id)
    return {
        "workspace_id": workspace_id,
        "slug": ws["slug"],
        "status": ws.get("status"),
        "files": [dict(r) for r in rows],
    }


@router.post("/workspaces/{workspace_id}/files", status_code=201)
@limiter.limit("120/minute")
async def write_workspace_file(
    workspace_id: str,
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    relative_path: str = Form(...),
    kind: str = Form(...),
    expected_prior_hash: str | None = Header(
        default=None, alias="X-Expected-Prior-Hash"
    ),
):
    """Worker write-back. Phase 8b CAS path.

    The ``X-Expected-Prior-Hash`` header is REQUIRED — the worker must
    always assert a CAS predicate so a buggy or compromised client cannot
    silently clobber files. Accepted values:

    * ``"none"`` (case-insensitive), empty string, or ``"*"`` — caller
      asserts the file does not exist yet.
    * any hex string — caller asserts the existing file's ``content_hash``
      matches.

    Returns the workspace_files row on success. Returns 400 if the header
    is missing, 409 if the workspace is archived, and 412 with
    ``{current_hash, expected_hash}`` body when the predicate fails.
    """
    if expected_prior_hash is None:
        raise BadRequestError(
            "X-Expected-Prior-Hash header is required on this endpoint"
        )

    # Reject oversized uploads up-front based on Content-Length to avoid
    # buffering an attacker-sized body in RAM. Streaming clients without
    # a Content-Length still get caught after read() below.
    declared_len = request.headers.get("content-length")
    if declared_len is not None:
        try:
            if int(declared_len) > MAX_WORKER_UPLOAD_BYTES:
                raise BadRequestError(
                    f"upload exceeds {MAX_WORKER_UPLOAD_BYTES} byte limit"
                )
        except ValueError:
            raise BadRequestError("invalid Content-Length header")

    wm = request.app.state.workspaces
    ws = await wm.get(workspace_id)
    if not ws:
        raise NotFoundError(f"workspace {workspace_id!r} not found")
    if ws.get("status") != "active":
        raise BadRequestError(
            f"workspace {workspace_id!r} is not active "
            f"(status={ws.get('status')!r}); writes are rejected"
        )

    data = await file.read()
    if not data:
        raise BadRequestError("file payload is empty")
    if len(data) > MAX_WORKER_UPLOAD_BYTES:
        raise BadRequestError(
            f"upload exceeds {MAX_WORKER_UPLOAD_BYTES} byte limit"
        )

    normalized = expected_prior_hash.strip().lower()
    if normalized in ("", "none", "*"):
        cas: str | None = None
    else:
        cas = expected_prior_hash.strip()

    registry = request.app.state.registry
    row = await registry.register(
        workspace_row=dict(ws),
        relative_path=relative_path,
        data=data,
        kind=kind,
        expected_prior_hash=cas,
    )
    response.headers["Location"] = (
        f"/api/v1/workspaces/{workspace_id}/files?"
        f"relative_path={quote(relative_path, safe='/')}"
    )
    return row
