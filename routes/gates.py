"""Web-only gate approval endpoint (Phase 5).

Per user decision: Phase 5 inbound is Web-only. OpenClaw is outbound-only
(notification consumer, no callback). This endpoint serves the Web UI
exclusively — actor identity is always injected from the session.

    GET  /api/v1/gates/{gate_id}
    POST /api/v1/gates/{gate_id}/{approve|reject}
        body {"note": "..."}

gate_id format:
    dev:<dev_work_id>:<gate_key>     — DevWork exit gate (gate_key='exit')
    des:<design_work_id>:<gate_key>  — DesignWork gate (future)

Note: there is no DevWork entry gate — "准入" is the user's act of
POSTing /dev-works (picking a design version + writing a prompt). Only
the exit gate becomes a SM waiting state, and only when
``config.devwork.require_human_exit_confirm=true`` (v1 default false).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter

from src.auth import get_current_user
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.models import GateActionRequest
from src.request_utils import client_ip
from src.webhook_events import WebhookEvent

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=client_ip)
router = APIRouter(tags=["gates"])

_GATE_TABLES = {"dev": "dev_works", "des": "design_works"}


def _parse_gate_id(gate_id: str) -> tuple[str, str, str]:
    parts = gate_id.split(":", 2)
    if len(parts) != 3 or parts[0] not in _GATE_TABLES:
        raise BadRequestError(f"invalid gate_id format: {gate_id!r}")
    return parts[0], parts[1], parts[2]


def _decode_gates(blob: str | None, *, kind: str, work_id: str) -> dict:
    """Defensive parse of ``gates_json``. Single-writer SM owns the column,
    but a manual edit or partial migration could still corrupt it; surface
    a 400 instead of letting the JSONDecodeError become a 500.
    """
    if not blob:
        return {}
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise BadRequestError(
            f"gates_json on {kind} {work_id!r} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise BadRequestError(
            f"gates_json on {kind} {work_id!r} is not a JSON object"
        )
    return data


@router.get("/gates/{gate_id}")
async def get_gate(gate_id: str, request: Request):
    kind, work_id, gate_key = _parse_gate_id(gate_id)
    table = _GATE_TABLES[kind]
    db = request.app.state.db
    row = await db.fetchone(
        f"SELECT id, workspace_id, gates_json FROM {table} WHERE id=?",
        (work_id,),
    )
    if row is None:
        raise NotFoundError(f"{kind} work {work_id!r} not found")
    gates = _decode_gates(row.get("gates_json"), kind=kind, work_id=work_id)
    gate = gates.get(gate_key)
    if not gate:
        raise NotFoundError(
            f"gate {gate_key!r} not found on {kind} {work_id!r}"
        )
    return {
        "gate_id": gate_id,
        "workspace_id": row["workspace_id"],
        "work_id": work_id,
        "gate_key": gate_key,
        **gate,
    }


@router.post("/gates/{gate_id}/{action}")
@limiter.limit("60/minute")
async def act_on_gate(
    gate_id: str,
    action: Literal["approve", "reject"],
    req: GateActionRequest,
    request: Request,
    current_user: str = Depends(get_current_user),
):
    kind, work_id, gate_key = _parse_gate_id(gate_id)
    table = _GATE_TABLES[kind]
    actor = current_user
    db = request.app.state.db
    now = datetime.now(timezone.utc).isoformat()

    row = await db.fetchone(
        f"SELECT id, workspace_id, gates_json FROM {table} WHERE id=?",
        (work_id,),
    )
    if row is None:
        raise NotFoundError(f"{kind} work {work_id!r} not found")
    gates = _decode_gates(row.get("gates_json"), kind=kind, work_id=work_id)
    gate = gates.get(gate_key)
    if not gate:
        raise NotFoundError(
            f"gate {gate_key!r} not found on {kind} {work_id!r}"
        )
    if gate.get("status") != "waiting":
        raise ConflictError(
            f"gate already {gate.get('status')!r}",
            current_stage=gate.get("status"),
        )
    gate["status"] = "approved" if action == "approve" else "rejected"
    gate["actor"] = actor
    gate["note"] = req.note
    gate["acted_at"] = now
    gates[gate_key] = gate
    await db.execute(
        f"UPDATE {table} SET gates_json=?, updated_at=? WHERE id=?",
        (json.dumps(gates, ensure_ascii=False), now, work_id),
    )

    sm = (
        request.app.state.dev_work_sm
        if kind == "dev"
        else request.app.state.design_work_sm
    )
    try:
        await sm.tick(work_id)
    except Exception:
        logger.exception(
            "post-gate tick failed for %s work_id=%s", kind, work_id
        )

    webhooks = getattr(request.app.state, "webhooks", None)
    if webhooks is not None:
        await webhooks.deliver(
            WebhookEvent.WORKSPACE_HUMAN_INTERVENTION,
            workspace_id=row["workspace_id"],
            correlation_id=work_id,
            payload={
                "actor": actor,
                "action": action,
                "target": gate_id,
                "note": req.note,
            },
        )
    return {
        "gate_id": gate_id,
        "status": gate["status"],
        "actor": actor,
    }
