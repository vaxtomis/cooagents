"""DevWork lifecycle routes (Phase 4).

Endpoints:
    POST   /api/v1/dev-works                — create + background drive
    GET    /api/v1/dev-works                — list; workspace_id REQUIRED
    GET    /api/v1/dev-works/{id}           — progress snapshot
    POST   /api/v1/dev-works/{id}/tick      — manual single-step advance
    POST   /api/v1/dev-works/{id}/cancel    — move to CANCELLED

C1 (v1 invariant): at most one active DevWork per design_doc. POST of a
second DevWork against the same ``design_doc_id`` while a prior one is not
in a terminal state returns 409. Enforced in two layers: fast-path SELECT
here (returns 409) and a partial UNIQUE index on the DB (returns 409 on
race-winner's INSERT via sqlite3.IntegrityError).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Request, Response
from slowapi import Limiter

from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.models import CreateDevWorkRequest, DevWorkProgress, DevWorkStep
from src.request_utils import client_ip

limiter = Limiter(key_func=client_ip)
router = APIRouter(tags=["dev-works"])

# Derived from DevWorkStep so adding a step updates both the enum and this
# tuple consistently. _TERMINAL must match src.dev_work_sm._TERMINAL.
_TERMINAL_STEPS = frozenset({
    DevWorkStep.COMPLETED,
    DevWorkStep.ESCALATED,
    DevWorkStep.CANCELLED,
})
_NON_TERMINAL = tuple(
    s.value for s in DevWorkStep if s not in _TERMINAL_STEPS
)


def _validate_repo_path(repo_path: str, workspace_root: Path) -> Path:
    """Ensure *repo_path* resolves under *workspace_root* and is a git repo.

    Defense-in-depth: blocks path-traversal payloads (``../../etc``) and
    filesystem paths outside the configured workspace root. Raising
    BadRequestError causes FastAPI to return 400 before the SM stores the
    value.
    """
    try:
        resolved = Path(repo_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise BadRequestError(f"invalid repo_path: {exc}") from exc
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise BadRequestError(
            f"repo_path must resolve under workspace_root "
            f"({workspace_root}); got {resolved}"
        ) from exc
    if not resolved.exists() or not resolved.is_dir():
        raise BadRequestError(
            f"repo_path does not exist or is not a directory: {resolved}"
        )
    if not (resolved / ".git").exists():
        raise BadRequestError(
            f"repo_path is not a git repository (missing .git): {resolved}"
        )
    return resolved


def _row_to_progress(row: dict) -> DevWorkProgress:
    fps = row.get("first_pass_success")
    return DevWorkProgress(
        id=row["id"],
        workspace_id=row["workspace_id"],
        design_doc_id=row["design_doc_id"],
        current_step=row["current_step"],
        iteration_rounds=row["iteration_rounds"],
        first_pass_success=bool(fps) if fps is not None else None,
        last_score=row.get("last_score"),
        last_problem_category=row.get("last_problem_category"),
        escalated_at=row.get("escalated_at"),
        completed_at=row.get("completed_at"),
        worktree_path=row.get("worktree_path"),
        worktree_branch=row.get("worktree_branch"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.post("/dev-works", status_code=201)
@limiter.limit("10/minute")
async def create_dev_work(
    req: CreateDevWorkRequest, request: Request, response: Response
) -> DevWorkProgress:
    settings = request.app.state.settings
    workspace_root = settings.security.resolved_workspace_root()
    resolved_repo = _validate_repo_path(req.repo_path, workspace_root)

    db = request.app.state.db
    placeholders = ",".join("?" for _ in _NON_TERMINAL)
    existing = await db.fetchone(
        f"SELECT id FROM dev_works WHERE design_doc_id=? "
        f"AND current_step IN ({placeholders})",
        (req.design_doc_id, *_NON_TERMINAL),
    )
    if existing is not None:
        raise ConflictError(
            f"design_doc {req.design_doc_id} already has active DevWork "
            f"{existing['id']}; v1 permits at most one active DevWork per "
            f"design_doc",
            current_stage=None,
        )

    sm = request.app.state.dev_work_sm
    try:
        dw = await sm.create(
            workspace_id=req.workspace_id,
            design_doc_id=req.design_doc_id,
            repo_path=str(resolved_repo),
            prompt=req.prompt,
            agent=req.agent.value,
        )
    except sqlite3.IntegrityError as exc:
        # Partial UNIQUE index on dev_works(design_doc_id) WHERE step not in
        # terminal set — caught race that slipped past the SELECT above.
        raise ConflictError(
            f"design_doc {req.design_doc_id} already has active DevWork "
            f"(race): {exc}",
            current_stage=None,
        ) from exc
    sm.schedule_driver(dw["id"])
    response.headers["Location"] = f"/api/v1/dev-works/{dw['id']}"
    return _row_to_progress(dw)


@router.get("/dev-works")
async def list_dev_works(
    request: Request, workspace_id: str
) -> list[DevWorkProgress]:
    db = request.app.state.db
    rows = await db.fetchall(
        "SELECT * FROM dev_works WHERE workspace_id=? ORDER BY created_at DESC",
        (workspace_id,),
    )
    return [_row_to_progress(r) for r in rows]


@router.get("/dev-works/{dev_id}")
async def get_dev_work(dev_id: str, request: Request) -> DevWorkProgress:
    db = request.app.state.db
    row = await db.fetchone("SELECT * FROM dev_works WHERE id=?", (dev_id,))
    if not row:
        raise NotFoundError(f"dev_work {dev_id!r} not found")
    return _row_to_progress(row)


@router.post("/dev-works/{dev_id}/tick")
@limiter.limit("30/minute")
async def tick_dev_work(dev_id: str, request: Request) -> DevWorkProgress:
    sm = request.app.state.dev_work_sm
    dw = await sm.tick(dev_id)
    return _row_to_progress(dw)


@router.post("/dev-works/{dev_id}/cancel", status_code=204)
@limiter.limit("10/minute")
async def cancel_dev_work(dev_id: str, request: Request) -> Response:
    sm = request.app.state.dev_work_sm
    await sm.cancel(dev_id)
    return Response(status_code=204)
