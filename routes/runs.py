from fastapi import APIRouter, Depends, Request, Response, HTTPException, UploadFile, Form
from slowapi import Limiter
from src.auth import get_current_user
from src.request_utils import client_ip
from src.models import (
    CreateRunRequest, ApproveRequest, RejectRequest, RetryRequest,
    RecoverRequest, SubmitRequirementRequest, ResolveConflictRequest,
)
from src.exceptions import NotFoundError, ConflictError, BadRequestError
from src.file_converter import validate_upload, convert_docx_to_md
from src.run_brief import build_brief, resolve_run_by_ticket
from src.path_validation import (
    RepoPathError,
    RepoUrlError,
    validate_repo_path,
    validate_repo_url,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# Per-route limiter. Must use the same key_func as the app-level limiter so
# slowapi's middleware associates hits with the right bucket.
limiter = Limiter(key_func=client_ip)

router = APIRouter(tags=["runs"])


def _validate_run_inputs(request: Request, repo_path: str, repo_url: str | None) -> str:
    """Apply workspace + host allowlist to run creation. Returns safe repo_path."""
    security = request.app.state.settings.security
    try:
        safe_path = validate_repo_path(repo_path, security.resolved_workspace_root())
    except RepoPathError as exc:
        raise BadRequestError(str(exc))
    if repo_url:
        try:
            validate_repo_url(
                repo_url,
                security.allowed_repo_hosts,
                security.allowed_repo_schemes,
            )
        except RepoUrlError as exc:
            raise BadRequestError(str(exc))
    return str(safe_path)


@router.post("/runs", status_code=201)
@limiter.limit("10/minute")
async def create_run(req: CreateRunRequest, request: Request):
    sm = request.app.state.sm
    safe_repo_path = _validate_run_inputs(request, req.repo_path, req.repo_url)
    result = await sm.create_run(
        req.ticket, safe_repo_path, req.description, req.preferences,
        notify_channel=req.notify_channel, notify_to=req.notify_to,
        repo_url=req.repo_url,
        design_agent=req.design_agent, dev_agent=req.dev_agent,
    )
    return result


@router.post("/runs/upload-requirement", status_code=201)
@limiter.limit("5/minute")
async def create_run_with_requirement(
    request: Request,
    file: UploadFile,
    ticket: str = Form(...),
    repo_path: str = Form(...),
    description: str | None = Form(None),
    notify_channel: str | None = Form(None),
    notify_to: str | None = Form(None),
    repo_url: str | None = Form(None),
    design_agent: str | None = Form(None),
    dev_agent: str | None = Form(None),
):
    import tempfile
    from pathlib import Path

    ext = validate_upload(file.filename or "")
    safe_repo_path = _validate_run_inputs(request, repo_path, repo_url)
    sm = request.app.state.sm

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_input = Path(tmp_dir) / f"upload.{ext}"
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise BadRequestError(
                f"Uploaded file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"
            )
        tmp_input.write_bytes(content)

        if ext == "docx":
            tmp_output = Path(tmp_dir) / "converted.md"
            await convert_docx_to_md(tmp_input, tmp_output)
            req_content = tmp_output.read_text(encoding="utf-8")
        else:
            req_content = tmp_input.read_text(encoding="utf-8")

    result = await sm.create_run_with_requirement(
        ticket, safe_repo_path, req_content, file.filename or "unknown",
        description=description,
        notify_channel=notify_channel, notify_to=notify_to,
        repo_url=repo_url, design_agent=design_agent, dev_agent=dev_agent,
    )
    return result


@router.get("/runs")
async def list_runs(
    request: Request,
    response: Response,
    status: str = None,
    ticket: str = None,
    current_stage: str = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = 20,
    offset: int = 0,
):
    db = request.app.state.db
    where_clauses = []
    params = []

    if status:
        where_clauses.append("status=?")
        params.append(status)

    if ticket:
        where_clauses.append("ticket LIKE ?")
        params.append(f"%{ticket}%")

    if current_stage:
        where_clauses.append("current_stage=?")
        params.append(current_stage)

    where_sql = ""
    if where_clauses:
        where_sql = " WHERE " + " AND ".join(where_clauses)

    total_row = await db.fetchone(f"SELECT COUNT(*) AS c FROM runs{where_sql}", tuple(params))
    response.headers["X-Total-Count"] = str(total_row["c"] if total_row else 0)

    sort_column_whitelist = {
        "created_at", "updated_at", "ticket", "status", "current_stage",
    }
    order_by = sort_by if sort_by in sort_column_whitelist else "created_at"
    order_direction = "ASC" if str(sort_order).lower() == "asc" else "DESC"
    assert order_direction in ("ASC", "DESC")

    sql = f"SELECT * FROM runs{where_sql} ORDER BY {order_by} {order_direction} LIMIT ? OFFSET ?"
    rows = await db.fetchall(sql, tuple(params + [limit, offset]))
    return [dict(r) for r in rows]


@router.get("/runs/brief")
async def get_run_brief_by_ticket(request: Request, ticket: str = None):
    if not ticket:
        raise BadRequestError("Query parameter 'ticket' is required")
    db = request.app.state.db
    run_id = await resolve_run_by_ticket(db, ticket)
    if not run_id:
        raise NotFoundError(f"No run found for ticket {ticket}")
    brief = await build_brief(db, run_id)
    if not brief:
        raise NotFoundError(f"Run {run_id} not found")
    return brief


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


@router.get("/runs/{run_id}/brief")
async def get_run_brief(run_id: str, request: Request):
    db = request.app.state.db
    brief = await build_brief(db, run_id)
    if not brief:
        raise NotFoundError(f"Run {run_id} not found")
    return brief


@router.post("/runs/{run_id}/tick")
async def tick_run(run_id: str, request: Request):
    sm = request.app.state.sm
    return await sm.tick(run_id)


@router.post("/runs/{run_id}/approve")
async def approve_run(
    run_id: str,
    req: ApproveRequest,
    request: Request,
    current_user: str = Depends(get_current_user),
):
    sm = request.app.state.sm
    return await sm.approve(run_id, req.gate.value, current_user, req.comment)


@router.post("/runs/{run_id}/reject")
async def reject_run(
    run_id: str,
    req: RejectRequest,
    request: Request,
    current_user: str = Depends(get_current_user),
):
    sm = request.app.state.sm
    return await sm.reject(run_id, req.gate.value, current_user, req.reason)


@router.post("/runs/{run_id}/retry")
async def retry_run(
    run_id: str,
    req: RetryRequest,
    request: Request,
    current_user: str = Depends(get_current_user),
):
    sm = request.app.state.sm
    return await sm.retry(run_id, current_user, req.note)


@router.post("/runs/{run_id}/recover")
async def recover_run(
    run_id: str,
    req: RecoverRequest,
    request: Request,
    current_user: str = Depends(get_current_user),
):
    executor = request.app.state.executor
    await executor.recover(run_id, req.action.value)
    sm = request.app.state.sm
    return await sm.tick(run_id)


@router.post("/runs/{run_id}/submit-requirement")
async def submit_requirement(run_id: str, req: SubmitRequirementRequest, request: Request):
    sm = request.app.state.sm
    return await sm.submit_requirement(run_id, req.content)


@router.post("/runs/{run_id}/resolve-conflict")
async def resolve_conflict(
    run_id: str,
    request: Request,
    req: ResolveConflictRequest | None = None,
    current_user: str = Depends(get_current_user),
):
    sm = request.app.state.sm
    return await sm.resolve_conflict(run_id, current_user)


@router.delete("/runs/{run_id}")
async def cancel_run(
    run_id: str,
    request: Request,
    cleanup: bool = False,
    current_user: str = Depends(get_current_user),
):
    sm = request.app.state.sm
    return await sm.cancel(run_id, cleanup)
