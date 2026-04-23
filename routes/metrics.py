"""Workspace metrics projection (Phase 8).

Endpoint:
    GET /api/v1/workspaces?since=&until=

Returns the four PRD Success Metrics as a single aggregate. Time window is
optional; both bounds accept ISO8601 strings (``2026-04-23T00:00:00+00:00``).
Pure read: four SELECT aggregates, no mutation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request

from src.exceptions import BadRequestError
from src.models import WorkspaceMetrics

router = APIRouter(tags=["metrics"])


def _parse_iso(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise BadRequestError(
            f"{name} must be ISO8601 (e.g. 2026-04-23T00:00:00+00:00): {exc}"
        ) from exc
    return value


def _append_range(
    where: list[str],
    params: list[Any],
    column: str,
    since: str | None,
    until: str | None,
) -> None:
    if since is not None:
        where.append(f"{column} >= ?")
        params.append(since)
    if until is not None:
        where.append(f"{column} <= ?")
        params.append(until)


@router.get("/metrics/workspaces")
async def workspace_metrics(
    request: Request,
    since: str | None = Query(None, description="ISO8601 lower bound (inclusive)"),
    until: str | None = Query(None, description="ISO8601 upper bound (inclusive)"),
) -> WorkspaceMetrics:
    since = _parse_iso(since, "since")
    until = _parse_iso(until, "until")

    db = request.app.state.db

    # active_workspaces: status='active' + workspace.created_at window
    active_where = ["status='active'"]
    active_params: list[Any] = []
    _append_range(active_where, active_params, "created_at", since, until)
    active_row = await db.fetchone(
        "SELECT COUNT(*) AS c FROM workspaces WHERE " + " AND ".join(active_where),
        tuple(active_params),
    )
    active_workspaces = int(active_row["c"] if active_row else 0)

    # total_workspaces (denominator for HI ratio): all statuses
    ws_where: list[str] = []
    ws_params: list[Any] = []
    _append_range(ws_where, ws_params, "created_at", since, until)
    ws_sql = "SELECT COUNT(*) AS c FROM workspaces"
    if ws_where:
        ws_sql += " WHERE " + " AND ".join(ws_where)
    total_ws_row = await db.fetchone(ws_sql, tuple(ws_params))
    total_workspaces = int(total_ws_row["c"] if total_ws_row else 0)

    # human_intervention events in window
    hi_where = ["event_name='workspace.human_intervention'"]
    hi_params: list[Any] = []
    _append_range(hi_where, hi_params, "ts", since, until)
    hi_row = await db.fetchone(
        "SELECT COUNT(*) AS c FROM workspace_events WHERE "
        + " AND ".join(hi_where),
        tuple(hi_params),
    )
    hi_count = int(hi_row["c"] if hi_row else 0)

    # dev_works aggregate: numerator (fps=1), denominator (terminal), avg rounds
    dw_where = ["current_step IN ('COMPLETED','ESCALATED')"]
    dw_params: list[Any] = []
    _append_range(dw_where, dw_params, "created_at", since, until)
    dw_row = await db.fetchone(
        "SELECT "
        "SUM(CASE WHEN first_pass_success=1 THEN 1 ELSE 0 END) AS numerator, "
        "COUNT(*) AS denominator, "
        "AVG(iteration_rounds) AS avg_rounds "
        "FROM dev_works WHERE " + " AND ".join(dw_where),
        tuple(dw_params),
    )
    numerator = int(dw_row["numerator"] or 0) if dw_row else 0
    denominator = int(dw_row["denominator"] or 0) if dw_row else 0
    avg_rounds = float(dw_row["avg_rounds"] or 0.0) if dw_row else 0.0

    human_intervention_per_workspace = (
        hi_count / total_workspaces if total_workspaces > 0 else 0.0
    )
    first_pass_success_rate = (
        numerator / denominator if denominator > 0 else 0.0
    )

    return WorkspaceMetrics(
        human_intervention_per_workspace=human_intervention_per_workspace,
        active_workspaces=active_workspaces,
        first_pass_success_rate=first_pass_success_rate,
        avg_iteration_rounds=avg_rounds,
    )
