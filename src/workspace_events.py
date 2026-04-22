"""Workspace event emission — Phase 3 local telemetry.

Writes to the ``workspace_events`` table only (pure log). Phase 5 will add
an outbound webhook contract on top; until then, no external delivery.

Event naming convention (PRD L270):
    workspace.*        — workspace lifecycle (emitted by WorkspaceManager)
    design_work.*      — DesignWork state machine
    design_doc.*       — DesignDoc publication
    dev_work.*         — DevWork (Phase 4)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone


async def emit_workspace_event(
    db,
    *,
    event_name: str,
    workspace_id: str | None = None,
    correlation_id: str | None = None,
    payload: dict | None = None,
) -> str:
    event_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO workspace_events"
        "(event_id, event_name, workspace_id, correlation_id, payload_json, ts) "
        "VALUES(?,?,?,?,?,?)",
        (
            event_id,
            event_name,
            workspace_id,
            correlation_id,
            json.dumps(payload, ensure_ascii=False) if payload else None,
            ts,
        ),
    )
    return event_id
