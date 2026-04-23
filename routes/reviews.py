"""Review read-only projection routes (Phase 5.5).

Endpoints:
    GET /api/v1/reviews?dev_work_id=...      — reviews for a DevWork
    GET /api/v1/reviews?design_work_id=...   — reviews for a DesignWork

Exactly one filter is required; supplying neither or both returns 400.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from src.exceptions import BadRequestError
from src.models import Review

router = APIRouter(tags=["reviews"])


def _decode_json_list(raw: str | None) -> list[dict] | None:
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, list):
        return None
    return [item for item in decoded if isinstance(item, dict)]


def _row_to_review(row: dict) -> Review:
    return Review(
        id=row["id"],
        dev_work_id=row.get("dev_work_id"),
        design_work_id=row.get("design_work_id"),
        dev_iteration_note_id=row.get("dev_iteration_note_id"),
        round=row["round"],
        score=row.get("score"),
        issues=_decode_json_list(row.get("issues_json")),
        findings=_decode_json_list(row.get("findings_json")),
        problem_category=row.get("problem_category"),
        reviewer=row.get("reviewer"),
        created_at=row["created_at"],
    )


@router.get("/reviews")
async def list_reviews(
    request: Request,
    dev_work_id: str | None = None,
    design_work_id: str | None = None,
) -> list[Review]:
    if dev_work_id is not None and design_work_id is not None:
        raise BadRequestError(
            "exactly one of dev_work_id or design_work_id is required"
        )
    if dev_work_id is None and design_work_id is None:
        raise BadRequestError(
            "exactly one of dev_work_id or design_work_id is required"
        )
    if dev_work_id is not None and not dev_work_id:
        raise BadRequestError("dev_work_id must not be empty")
    if design_work_id is not None and not design_work_id:
        raise BadRequestError("design_work_id must not be empty")

    db = request.app.state.db
    if dev_work_id is not None:
        rows = await db.fetchall(
            "SELECT * FROM reviews WHERE dev_work_id=? "
            "ORDER BY round ASC, created_at ASC",
            (dev_work_id,),
        )
    else:
        rows = await db.fetchall(
            "SELECT * FROM reviews WHERE design_work_id=? "
            "ORDER BY round ASC, created_at ASC",
            (design_work_id,),
        )
    return [_row_to_review(r) for r in rows]
