"""Global event index endpoints."""
from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request


def create_events_router(db=None):
    router = APIRouter(tags=["events"])

    def _get_db(request: Request | None = None):
        if db is not None:
            return db
        return request.app.state.db

    @router.get("/events")
    async def list_events(
        request: Request,
        run_id: str | None = Query(None),
        level: str | None = Query(None, pattern="^(debug|info|warning|error)$"),
        span_type: str | None = Query(None),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        d = _get_db(request)

        conditions: list[str] = []
        params: list[object] = []

        if level:
            conditions.append("e.level = ?")
            params.append(level)

        if run_id:
            conditions.append("e.run_id = ?")
            params.append(run_id)

        if span_type:
            conditions.append("e.span_type = ?")
            params.append(span_type)

        where_sql = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        count_row = await d.fetchone(
            f"SELECT COUNT(*) AS c FROM events e{where_sql}",
            tuple(params),
        )
        total = count_row["c"] if count_row else 0

        events = await d.fetchall(
            f"""
            SELECT
                e.*,
                r.ticket AS ticket
            FROM events e
            LEFT JOIN runs r ON r.id = e.run_id
            {where_sql}
            ORDER BY e.created_at DESC, e.id DESC
            LIMIT ? OFFSET ?
            """,
            tuple([*params, limit, offset]),
        )

        for event in events:
            if event.get("payload_json"):
                try:
                    event["payload"] = json.loads(event["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    event["payload"] = event["payload_json"]
            else:
                event["payload"] = None
            event.pop("payload_json", None)

        return {
            "events": events,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total,
            },
        }

    return router