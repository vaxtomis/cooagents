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

Phase 4 (repo-registry): ``repo_path`` is replaced by ``repo_refs``. The
4-step validation chain runs *after* the active-DevWork-per-design_doc
fast-path so a duplicate doesn't pay the inspector cost.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request, Response
from slowapi import Limiter

from routes._repo_refs_validation import validate_dev_repo_refs
from src.exceptions import ConflictError, NotFoundError
from src.models import (
    CreateDevWorkRequest,
    DevRepoRefView,
    DevWorkProgress,
    DevWorkStep,
)
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


async def _load_repo_refs(
    db, dev_id: str
) -> list[DevRepoRefView]:
    rows = await db.fetchall(
        "SELECT repo_id, mount_name, base_branch, base_rev, "
        "devwork_branch, push_state, is_primary "
        "FROM dev_work_repos WHERE dev_work_id=? ORDER BY mount_name",
        (dev_id,),
    )
    return [_row_to_repo_ref(r) for r in rows]


def _row_to_repo_ref(r: dict) -> DevRepoRefView:
    return DevRepoRefView(
        repo_id=r["repo_id"],
        mount_name=r["mount_name"],
        base_branch=r["base_branch"],
        base_rev=r.get("base_rev"),
        devwork_branch=r["devwork_branch"],
        push_state=r["push_state"],
        is_primary=bool(r.get("is_primary")),
    )


async def _load_repo_refs_batch(
    db, dev_ids: list[str]
) -> dict[str, list[DevRepoRefView]]:
    """Single-query bulk fetch — avoids N+1 on list endpoints."""
    if not dev_ids:
        return {}
    placeholders = ",".join("?" for _ in dev_ids)
    rows = await db.fetchall(
        f"SELECT dev_work_id, repo_id, mount_name, base_branch, base_rev, "
        f"devwork_branch, push_state, is_primary "
        f"FROM dev_work_repos WHERE dev_work_id IN ({placeholders}) "
        f"ORDER BY dev_work_id, mount_name",
        tuple(dev_ids),
    )
    grouped: dict[str, list[DevRepoRefView]] = {dwid: [] for dwid in dev_ids}
    for r in rows:
        grouped[r["dev_work_id"]].append(_row_to_repo_ref(r))
    return grouped


def _row_to_progress(
    row: dict, repo_refs: list[DevRepoRefView] | None = None
) -> DevWorkProgress:
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
        repo_refs=repo_refs or [],
    )


@router.post("/dev-works", status_code=201)
@limiter.limit("10/minute")
async def create_dev_work(
    req: CreateDevWorkRequest, request: Request, response: Response
) -> DevWorkProgress:
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

    # 4-step validation chain runs AFTER the active-DevWork fast-path so a
    # duplicate request doesn't pay the inspector cost.
    validated = await validate_dev_repo_refs(
        req.repo_refs,
        request.app.state.repo_registry_repo,
        request.app.state.repo_inspector,
    )

    sm = request.app.state.dev_work_sm
    try:
        dw = await sm.create(
            workspace_id=req.workspace_id,
            design_doc_id=req.design_doc_id,
            repo_refs=validated,
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
    refs = await _load_repo_refs(db, dw["id"])
    return _row_to_progress(dw, refs)


@router.get("/dev-works")
async def list_dev_works(
    request: Request, workspace_id: str
) -> list[DevWorkProgress]:
    db = request.app.state.db
    rows = await db.fetchall(
        "SELECT * FROM dev_works WHERE workspace_id=? ORDER BY created_at DESC",
        (workspace_id,),
    )
    refs_by_id = await _load_repo_refs_batch(db, [r["id"] for r in rows])
    return [_row_to_progress(r, refs_by_id.get(r["id"], [])) for r in rows]


@router.get("/dev-works/{dev_id}")
async def get_dev_work(dev_id: str, request: Request) -> DevWorkProgress:
    db = request.app.state.db
    row = await db.fetchone("SELECT * FROM dev_works WHERE id=?", (dev_id,))
    if not row:
        raise NotFoundError(f"dev_work {dev_id!r} not found")
    refs = await _load_repo_refs(db, dev_id)
    return _row_to_progress(row, refs)


@router.post("/dev-works/{dev_id}/tick")
@limiter.limit("30/minute")
async def tick_dev_work(dev_id: str, request: Request) -> DevWorkProgress:
    sm = request.app.state.dev_work_sm
    dw = await sm.tick(dev_id)
    refs = await _load_repo_refs(request.app.state.db, dev_id)
    return _row_to_progress(dw, refs)


@router.post("/dev-works/{dev_id}/cancel", status_code=204)
@limiter.limit("10/minute")
async def cancel_dev_work(dev_id: str, request: Request) -> Response:
    sm = request.app.state.dev_work_sm
    await sm.cancel(dev_id)
    return Response(status_code=204)
