"""Workspace events read-only projection route (Phase 5.5).

Endpoint:
    GET /api/v1/workspaces/{workspace_id}/events
        ?limit=&offset=&event_name=

Read-only: pure SELECT against ``workspace_events``; no mutation.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request

from src.exceptions import BadRequestError, NotFoundError

router = APIRouter(tags=["workspace-events"])

_EVENT_NAME_MAX_LEN = 120


@router.get("/workspaces/{workspace_id}/events")
async def list_workspace_events(
    workspace_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    event_name: list[str] | None = Query(None, max_length=20),
):
    db = request.app.state.db

    # Distinguish "unknown workspace" (404) from "workspace has no events" (200 [])
    ws_row = await db.fetchone(
        "SELECT id FROM workspaces WHERE id=?", (workspace_id,)
    )
    if not ws_row:
        raise NotFoundError(f"workspace {workspace_id!r} not found")

    conditions: list[str] = ["workspace_id = ?"]
    params: list[object] = [workspace_id]

    if event_name:
        # Per-value length cap (Query(max_length=...) only caps list length).
        for name in event_name:
            if len(name) > _EVENT_NAME_MAX_LEN:
                raise BadRequestError(
                    f"event_name length must be ≤ {_EVENT_NAME_MAX_LEN}"
                )
        # Deduplicate while preserving order — keeps bound-param count predictable.
        seen: set[str] = set()
        unique_names: list[str] = []
        for name in event_name:
            if name not in seen:
                seen.add(name)
                unique_names.append(name)
        placeholders = ",".join("?" for _ in unique_names)
        conditions.append(f"event_name IN ({placeholders})")
        params.extend(unique_names)

    where_sql = " WHERE " + " AND ".join(conditions)

    count_row = await db.fetchone(
        f"SELECT COUNT(*) AS c FROM workspace_events{where_sql}",
        tuple(params),
    )
    total = count_row["c"] if count_row else 0

    events = await db.fetchall(
        "SELECT id, event_id, event_name, workspace_id, correlation_id, "
        f"payload_json, ts FROM workspace_events{where_sql} "
        "ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
        tuple([*params, limit, offset]),
    )

    for event in events:
        raw = event.get("payload_json")
        if raw:
            try:
                event["payload"] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                event["payload"] = raw
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
