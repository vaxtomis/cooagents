"""DesignWork lifecycle routes (Phase 3).

Endpoints:
    POST   /api/v1/design-works                — create + background drive
    GET    /api/v1/design-works                — list; workspace_id is REQUIRED
    GET    /api/v1/design-works/{id}           — progress snapshot
    POST   /api/v1/design-works/{id}/tick      — manual single-step advance
    POST   /api/v1/design-works/{id}/cancel    — move to CANCELLED

Business logic lives in DesignWorkStateMachine.

Phase 4 (repo-registry): optional ``repo_refs`` on the create payload.
Empty list keeps pure-doc DesignWorks unchanged; non-empty triggers the
3-step validation chain (existence → health → branch resolves) before
the SM creates ``design_work_repos`` rows.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request, Response
from slowapi import Limiter

from routes._repo_refs_validation import validate_design_repo_refs
from src.exceptions import BadRequestError, NotFoundError
from src.models import (
    CreateDesignWorkRequest,
    DesignWorkPage,
    DesignRepoRefView,
    DesignWorkProgress,
    DesignWorkState,
)
from src.request_utils import client_ip

limiter = Limiter(key_func=client_ip)
router = APIRouter(tags=["design-works"])
_DESIGN_WORK_SORT_SQL: dict[str, str] = {
    "created_desc": "created_at DESC, id DESC",
    "created_asc": "created_at ASC, id ASC",
    "updated_desc": "updated_at DESC, id DESC",
    "updated_asc": "updated_at ASC, id ASC",
}


async def _load_repo_refs(db, dw_id: str) -> list[DesignRepoRefView]:
    rows = await db.fetchall(
        "SELECT repo_id, branch, rev FROM design_work_repos "
        "WHERE design_work_id=? ORDER BY repo_id",
        (dw_id,),
    )
    return [_row_to_repo_ref(r) for r in rows]


def _row_to_repo_ref(r: dict) -> DesignRepoRefView:
    return DesignRepoRefView(
        repo_id=r["repo_id"],
        branch=r["branch"],
        rev=r.get("rev"),
    )


async def _load_repo_refs_batch(
    db, dw_ids: list[str]
) -> dict[str, list[DesignRepoRefView]]:
    """Single-query bulk fetch — avoids N+1 on list endpoints."""
    if not dw_ids:
        return {}
    placeholders = ",".join("?" for _ in dw_ids)
    rows = await db.fetchall(
        f"SELECT design_work_id, repo_id, branch, rev FROM design_work_repos "
        f"WHERE design_work_id IN ({placeholders}) "
        f"ORDER BY design_work_id, repo_id",
        tuple(dw_ids),
    )
    grouped: dict[str, list[DesignRepoRefView]] = {dwid: [] for dwid in dw_ids}
    for r in rows:
        grouped[r["design_work_id"]].append(_row_to_repo_ref(r))
    return grouped


def _row_to_progress(
    row: dict,
    repo_refs: list[DesignRepoRefView] | None = None,
    *,
    is_running: bool = False,
) -> DesignWorkProgress:
    missing = None
    if row.get("missing_sections_json"):
        try:
            missing = json.loads(row["missing_sections_json"])
            if not isinstance(missing, list):
                missing = None
        except Exception:
            missing = None
    return DesignWorkProgress(
        id=row["id"],
        workspace_id=row["workspace_id"],
        mode=row["mode"],
        current_state=row["current_state"],
        loop=row["loop"],
        missing_sections=missing,
        output_design_doc_id=row.get("output_design_doc_id"),
        escalated_at=row.get("escalated_at"),
        title=row.get("title"),
        sub_slug=row.get("sub_slug"),
        version=row.get("version"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_running=is_running,
        repo_refs=repo_refs or [],
    )


@router.post("/design-works", status_code=201)
@limiter.limit("10/minute")
async def create_design_work(
    req: CreateDesignWorkRequest, request: Request, response: Response
) -> DesignWorkProgress:
    sm = request.app.state.design_work_sm
    # Empty repo_refs short-circuits the validator (preserves pure-doc
    # DesignWorks). Non-empty triggers the 3-step chain.
    validated = (
        await validate_design_repo_refs(
            req.repo_refs,
            request.app.state.repo_registry_repo,
            request.app.state.repo_inspector,
        )
        if req.repo_refs
        else []
    )
    dw = await sm.create(
        workspace_id=req.workspace_id,
        title=req.title,
        sub_slug=req.slug,
        user_input=req.user_input,
        mode=req.mode,
        parent_version=req.parent_version,
        needs_frontend_mockup=req.needs_frontend_mockup,
        agent=req.agent.value,
        rubric_threshold=req.rubric_threshold,  # U2 API override
        repo_refs=validated,
    )
    # Fire-and-forget background driver; errors are logged inside the SM,
    # and the SM clears its own task-tracking map via add_done_callback.
    sm.schedule_driver(dw["id"])
    response.headers["Location"] = f"/api/v1/design-works/{dw['id']}"
    refs = await _load_repo_refs(request.app.state.db, dw["id"])
    return _row_to_progress(dw, refs, is_running=sm.is_running(dw["id"]))


@router.get("/design-works")
async def list_design_works(
    request: Request,
    workspace_id: str,  # REQUIRED per U3 (option B)
    state: str | None = None,
    query: str | None = None,
    sort: str = "created_desc",
    limit: int = Query(12, ge=1, le=100),
    offset: int = Query(0, ge=0),
    paginate: bool = False,
) -> list[DesignWorkProgress] | DesignWorkPage:
    """List DesignWorks within a single workspace.

    ``workspace_id`` is mandatory: DesignWork always belongs to a workspace,
    and cross-workspace listing would encourage full-table scans. Omitting
    the param triggers FastAPI's 422 (missing query param).
    """
    db = request.app.state.db
    sm = request.app.state.design_work_sm
    if state and state not in {s.value for s in DesignWorkState}:
        raise BadRequestError(
            f"state must be one of {sorted(s.value for s in DesignWorkState)}"
        )
    try:
        order_sql = _DESIGN_WORK_SORT_SQL[sort]
    except KeyError as exc:
        raise BadRequestError(
            f"sort must be one of {sorted(_DESIGN_WORK_SORT_SQL)}"
        ) from exc
    conditions = ["workspace_id=?"]
    params: list[object] = [workspace_id]
    if state:
        conditions.append("current_state=?")
        params.append(state)
    if query:
        like = f"%{query.strip()}%"
        conditions.append("(COALESCE(title, '') LIKE ? OR COALESCE(sub_slug, '') LIKE ? OR id LIKE ?)")
        params.extend([like, like, like])
    where_sql = " WHERE " + " AND ".join(conditions)
    sql = (
        "SELECT * FROM design_works"
        f"{where_sql} ORDER BY {order_sql}"
    )
    page_params = [*params, limit, offset]
    if paginate:
        count_row = await db.fetchone(
            f"SELECT COUNT(*) AS c FROM design_works{where_sql}",
            tuple(params),
        )
        total = int(count_row["c"]) if count_row is not None else 0
        rows = await db.fetchall(
            f"{sql} LIMIT ? OFFSET ?",
            tuple(page_params),
        )
        refs_by_id = await _load_repo_refs_batch(db, [r["id"] for r in rows])
        return DesignWorkPage(
            items=[
                _row_to_progress(
                    r,
                    refs_by_id.get(r["id"], []),
                    is_running=sm.is_running(r["id"]),
                )
                for r in rows
            ],
            pagination={
                "limit": limit,
                "offset": offset,
                "total": total,
                "has_more": (offset + limit) < total,
            },
        )
    rows = await db.fetchall(sql, tuple(params))
    refs_by_id = await _load_repo_refs_batch(db, [r["id"] for r in rows])
    return [
        _row_to_progress(
            r,
            refs_by_id.get(r["id"], []),
            is_running=sm.is_running(r["id"]),
        )
        for r in rows
    ]


@router.get("/design-works/{dw_id}")
async def get_design_work(dw_id: str, request: Request) -> DesignWorkProgress:
    db = request.app.state.db
    row = await db.fetchone("SELECT * FROM design_works WHERE id=?", (dw_id,))
    if not row:
        raise NotFoundError(f"design_work {dw_id!r} not found")
    refs = await _load_repo_refs(db, dw_id)
    sm = request.app.state.design_work_sm
    return _row_to_progress(row, refs, is_running=sm.is_running(dw_id))


@router.post("/design-works/{dw_id}/tick")
@limiter.limit("30/minute")
async def tick_design_work(dw_id: str, request: Request) -> DesignWorkProgress:
    sm = request.app.state.design_work_sm
    dw = await sm.tick(dw_id)
    refs = await _load_repo_refs(request.app.state.db, dw_id)
    return _row_to_progress(dw, refs, is_running=sm.is_running(dw_id))


@router.post("/design-works/{dw_id}/cancel", status_code=204)
@limiter.limit("10/minute")
async def cancel_design_work(dw_id: str, request: Request) -> Response:
    sm = request.app.state.design_work_sm
    await sm.cancel(dw_id)
    return Response(status_code=204)
