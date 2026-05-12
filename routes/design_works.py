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

from fastapi import APIRouter, Body, Query, Request, Response
from slowapi import Limiter

from routes._repo_refs_validation import validate_design_repo_refs
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.models import (
    CreateDesignWorkRequest,
    DesignWorkRetrySource,
    DesignWorkMode,
    DesignWorkPage,
    DesignRepoRefView,
    DesignWorkProgress,
    DesignWorkState,
    RepoRef,
    RetryDesignWorkRequest,
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


async def _load_retry_repo_refs(db, dw_id: str) -> list[RepoRef]:
    rows = await db.fetchall(
        "SELECT repo_id, branch FROM design_work_repos "
        "WHERE design_work_id=? ORDER BY repo_id",
        (dw_id,),
    )
    return [
        RepoRef(repo_id=row["repo_id"], base_branch=row["branch"])
        for row in rows
    ]


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
    max_loops: int | None = None,
) -> DesignWorkProgress:
    missing = None
    if row.get("missing_sections_json"):
        try:
            missing = json.loads(row["missing_sections_json"])
            if not isinstance(missing, list):
                missing = None
        except Exception:
            missing = None
    attachment_paths = _decode_attachment_paths(row.get("gates_json"))
    return DesignWorkProgress(
        id=row["id"],
        workspace_id=row["workspace_id"],
        mode=row["mode"],
        current_state=row["current_state"],
        loop=row["loop"],
        max_loops=max_loops if max_loops is not None else 0,
        missing_sections=missing,
        output_design_doc_id=row.get("output_design_doc_id"),
        escalated_at=row.get("escalated_at"),
        escalation_reason=row.get("escalation_reason"),
        title=row.get("title"),
        sub_slug=row.get("sub_slug"),
        version=row.get("version"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        is_running=is_running,
        repo_refs=repo_refs or [],
        attachment_paths=attachment_paths,
    )


def _decode_attachment_paths(blob: str | None) -> list[str]:
    if not blob:
        return []
    try:
        gates = json.loads(blob)
    except Exception:
        return []
    paths = gates.get("attachment_paths") if isinstance(gates, dict) else None
    if not isinstance(paths, list):
        return []
    return [p for p in paths if isinstance(p, str)]


def _ensure_retryable(source: dict) -> None:
    if source["current_state"] != DesignWorkState.ESCALATED.value:
        raise ConflictError(
            "only ESCALATED DesignWork can be retried",
            current_stage=source["current_state"],
        )
    if source["mode"] != DesignWorkMode.new.value:
        raise ConflictError(
            "only mode=new DesignWork retry is supported",
            current_stage=source["mode"],
        )


async def _read_source_user_input(request: Request, source: dict) -> str:
    workspace = await request.app.state.workspaces.get(source["workspace_id"])
    if workspace is None:
        raise NotFoundError(f"workspace {source['workspace_id']!r} not found")
    input_path = source.get("user_input_path")
    if not input_path:
        raise NotFoundError(f"design_work {source['id']!r} has no source input file")
    return await request.app.state.registry.read_text(
        workspace_slug=workspace["slug"],
        relative_path=input_path,
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
        agent=req.agent.value if req.agent is not None else None,
        rubric_threshold=req.rubric_threshold,  # U2 API override
        max_loops=req.max_loops,
        repo_refs=validated,
        attachment_paths=req.attachment_paths,
    )
    # Fire-and-forget background driver; errors are logged inside the SM,
    # and the SM clears its own task-tracking map via add_done_callback.
    sm.schedule_driver(dw["id"])
    response.headers["Location"] = f"/api/v1/design-works/{dw['id']}"
    refs = await _load_repo_refs(request.app.state.db, dw["id"])
    return _row_to_progress(
        dw,
        refs,
        is_running=sm.is_running(dw["id"]),
        max_loops=sm._resolve_max_loops(dw),
    )


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
                    max_loops=sm._resolve_max_loops(r),
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
            max_loops=sm._resolve_max_loops(r),
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
    return _row_to_progress(
        row,
        refs,
        is_running=sm.is_running(dw_id),
        max_loops=sm._resolve_max_loops(row),
    )


@router.post("/design-works/{dw_id}/tick")
@limiter.limit("30/minute")
async def tick_design_work(dw_id: str, request: Request) -> DesignWorkProgress:
    sm = request.app.state.design_work_sm
    dw = await sm.tick(dw_id)
    refs = await _load_repo_refs(request.app.state.db, dw_id)
    return _row_to_progress(
        dw,
        refs,
        is_running=sm.is_running(dw_id),
        max_loops=sm._resolve_max_loops(dw),
    )


@router.get("/design-works/{dw_id}/retry-source")
async def get_design_work_retry_source(
    dw_id: str, request: Request
) -> DesignWorkRetrySource:
    db = request.app.state.db
    source = await db.fetchone("SELECT * FROM design_works WHERE id=?", (dw_id,))
    if not source:
        raise NotFoundError(f"design_work {dw_id!r} not found")
    _ensure_retryable(source)
    user_input = await _read_source_user_input(request, source)
    refs = await _load_retry_repo_refs(db, dw_id)
    return DesignWorkRetrySource(
        title=source.get("title") or source["id"],
        slug=source.get("sub_slug") or source["id"],
        user_input=user_input,
        needs_frontend_mockup=bool(source.get("needs_frontend_mockup")),
        agent=source.get("agent"),
        repo_refs=refs,
        attachment_paths=_decode_attachment_paths(source.get("gates_json")),
    )


@router.post("/design-works/{dw_id}/retry", status_code=201)
@limiter.limit("10/minute")
async def retry_design_work(
    dw_id: str,
    request: Request,
    response: Response,
    payload: RetryDesignWorkRequest | None = Body(default=None),
) -> DesignWorkProgress:
    db = request.app.state.db
    source = await db.fetchone("SELECT * FROM design_works WHERE id=?", (dw_id,))
    if not source:
        raise NotFoundError(f"design_work {dw_id!r} not found")
    _ensure_retryable(source)
    fields_set = payload.model_fields_set if payload is not None else set()
    user_input = (
        payload.user_input
        if payload is not None and "user_input" in fields_set
        else await _read_source_user_input(request, source)
    )
    refs = (
        payload.repo_refs or []
        if payload is not None and "repo_refs" in fields_set
        else await _load_retry_repo_refs(db, dw_id)
    )
    attachment_paths = (
        payload.attachment_paths or []
        if payload is not None and "attachment_paths" in fields_set
        else _decode_attachment_paths(source.get("gates_json"))
    )
    validated = (
        await validate_design_repo_refs(
            refs,
            request.app.state.repo_registry_repo,
            request.app.state.repo_inspector,
        )
        if refs
        else []
    )

    sm = request.app.state.design_work_sm
    title = (
        payload.title
        if payload is not None and "title" in fields_set
        else source["title"]
    )
    sub_slug = (
        payload.slug
        if payload is not None and "slug" in fields_set
        else source["sub_slug"]
    )
    needs_frontend_mockup = (
        payload.needs_frontend_mockup
        if payload is not None and "needs_frontend_mockup" in fields_set
        else bool(source.get("needs_frontend_mockup"))
    )
    agent = (
        payload.agent.value if payload is not None and payload.agent is not None
        else None if payload is not None and "agent" in fields_set
        else source.get("agent")
    )
    created = await sm.create(
        workspace_id=source["workspace_id"],
        title=title,
        sub_slug=sub_slug,
        user_input=user_input,
        mode=DesignWorkMode.new,
        parent_version=source.get("parent_version"),
        needs_frontend_mockup=bool(needs_frontend_mockup),
        agent=agent,
        repo_refs=validated,
        attachment_paths=attachment_paths,
    )
    sm.schedule_driver(created["id"])
    response.headers["Location"] = f"/api/v1/design-works/{created['id']}"
    created_refs = await _load_repo_refs(db, created["id"])
    return _row_to_progress(
        created,
        created_refs,
        is_running=sm.is_running(created["id"]),
        max_loops=sm._resolve_max_loops(created),
    )


@router.post("/design-works/{dw_id}/cancel", status_code=204)
@limiter.limit("10/minute")
async def cancel_design_work(dw_id: str, request: Request) -> Response:
    sm = request.app.state.design_work_sm
    await sm.cancel(dw_id)
    return Response(status_code=204)
