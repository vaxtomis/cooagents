"""Workspace event emission — Phase 3 local telemetry + Phase 5 webhooks.

``emit_workspace_event`` writes to the ``workspace_events`` log table.
``emit_and_deliver`` does the same, then fans the event out to
``webhook_subscriptions`` via ``WebhookNotifier.deliver``. Use the latter
from SM/manager code so the local log row and the outbound webhook
envelope share a single ``event_id`` (consumers can dedupe).

Event naming convention (PRD L270):
    workspace.*        — workspace lifecycle (emitted by WorkspaceManager)
    design_work.*      — DesignWork state machine
    design_doc.*       — DesignDoc publication
    dev_work.*         — DevWork state machine + step handlers
    webhook.*          — delivery self-log (internal)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any


async def emit_workspace_event(
    db,
    *,
    event_name: str,
    workspace_id: str | None = None,
    correlation_id: str | None = None,
    payload: dict | None = None,
    event_id: str | None = None,
) -> str:
    eid = event_id or str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO workspace_events"
        "(event_id, event_name, workspace_id, correlation_id, payload_json, ts) "
        "VALUES(?,?,?,?,?,?)",
        (
            eid,
            event_name,
            workspace_id,
            correlation_id,
            json.dumps(payload, ensure_ascii=False) if payload else None,
            ts,
        ),
    )
    return eid


async def emit_and_deliver(
    db,
    webhooks: Any,
    *,
    event_name: str,
    workspace_id: str | None = None,
    correlation_id: str | None = None,
    payload: dict | None = None,
) -> str:
    """Log the event locally AND fan it out via webhooks (single event_id).

    ``webhooks`` may be ``None`` (test fixtures, standalone managers) —
    delivery is skipped but the local log row is still written.
    """
    eid = await emit_workspace_event(
        db,
        event_name=event_name,
        workspace_id=workspace_id,
        correlation_id=correlation_id,
        payload=payload,
    )
    if webhooks is not None:
        await webhooks.deliver(
            event_name,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            payload=payload,
            event_id=eid,
        )
    return eid
