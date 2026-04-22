"""DesignWork lifecycle routes (Phase 3).

Endpoints:
    POST   /api/v1/design-works                — create + background drive
    GET    /api/v1/design-works                — list; workspace_id is REQUIRED
    GET    /api/v1/design-works/{id}           — progress snapshot
    POST   /api/v1/design-works/{id}/tick      — manual single-step advance
    POST   /api/v1/design-works/{id}/cancel    — move to CANCELLED

Business logic lives in DesignWorkStateMachine.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response
from slowapi import Limiter

from src.exceptions import NotFoundError
from src.models import CreateDesignWorkRequest, DesignWorkProgress
from src.request_utils import client_ip

limiter = Limiter(key_func=client_ip)
router = APIRouter(tags=["design-works"])


def _row_to_progress(row: dict) -> DesignWorkProgress:
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
    )


@router.post("/design-works", status_code=201)
@limiter.limit("10/minute")
async def create_design_work(
    req: CreateDesignWorkRequest, request: Request, response: Response
) -> DesignWorkProgress:
    sm = request.app.state.design_work_sm
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
    )
    # Fire-and-forget background driver; errors are logged inside the SM,
    # and the SM clears its own task-tracking map via add_done_callback.
    sm.schedule_driver(dw["id"])
    response.headers["Location"] = f"/api/v1/design-works/{dw['id']}"
    return _row_to_progress(dw)


@router.get("/design-works")
async def list_design_works(
    request: Request, workspace_id: str  # REQUIRED per U3 (option B)
) -> list[DesignWorkProgress]:
    """List DesignWorks within a single workspace.

    ``workspace_id`` is mandatory: DesignWork always belongs to a workspace,
    and cross-workspace listing would encourage full-table scans. Omitting
    the param triggers FastAPI's 422 (missing query param).
    """
    db = request.app.state.db
    rows = await db.fetchall(
        "SELECT * FROM design_works WHERE workspace_id=? ORDER BY created_at DESC",
        (workspace_id,),
    )
    return [_row_to_progress(r) for r in rows]


@router.get("/design-works/{dw_id}")
async def get_design_work(dw_id: str, request: Request) -> DesignWorkProgress:
    db = request.app.state.db
    row = await db.fetchone("SELECT * FROM design_works WHERE id=?", (dw_id,))
    if not row:
        raise NotFoundError(f"design_work {dw_id!r} not found")
    return _row_to_progress(row)


@router.post("/design-works/{dw_id}/tick")
@limiter.limit("30/minute")
async def tick_design_work(dw_id: str, request: Request) -> DesignWorkProgress:
    sm = request.app.state.design_work_sm
    dw = await sm.tick(dw_id)
    return _row_to_progress(dw)


@router.post("/design-works/{dw_id}/cancel", status_code=204)
@limiter.limit("10/minute")
async def cancel_design_work(dw_id: str, request: Request) -> Response:
    sm = request.app.state.design_work_sm
    await sm.cancel(dw_id)
    return Response(status_code=204)
