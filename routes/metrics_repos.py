"""Repo-registry metrics projection (Phase 9).

Endpoint:
    GET /api/v1/metrics/repos?since=&until=

Returns two of the three PRD repo-registry Success Metrics:

* ``multi_repo_dev_work_share`` — share of DevWorks bound to >1 repo, optionally
  windowed by ``dev_works.created_at``.
* ``healthy_repos_share`` — share of registered repos with ``fetch_status =
  'healthy'``. This is a current-state snapshot and intentionally ignores the
  query window (parallel to ``WorkspaceMetrics.active_workspaces``).

Metric 1 (DevWork creation reject rate) is deferred to a follow-up PRP; the
schema has no ``dev_works.state`` / ``last_err`` columns and the operator
scoped its measurement out of Phase 9.

Pure read: a handful of SELECT aggregates, no mutation.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from routes._metrics_common import append_range, parse_iso
from src.models import RepoRegistryMetrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics/repos")
async def repo_registry_metrics(
    request: Request,
    since: str | None = Query(None, description="ISO8601 lower bound (inclusive)"),
    until: str | None = Query(None, description="ISO8601 upper bound (inclusive)"),
) -> RepoRegistryMetrics:
    since = parse_iso(since, "since")
    until = parse_iso(until, "until")

    db = request.app.state.db

    # healthy_repos_share: current-state snapshot, independent of window.
    # The window would otherwise exclude repos registered before `since` that
    # are still healthy/error today — misleading for a "fleet health right
    # now" gauge. Keep this metric unwindowed on purpose, parallel to
    # ``active_workspaces`` in routes/metrics.py.
    health_row = await db.fetchone(
        "SELECT "
        "SUM(CASE WHEN fetch_status='healthy' THEN 1 ELSE 0 END) AS healthy, "
        "COUNT(*) AS total "
        "FROM repos",
        (),
    )
    healthy = int((health_row["healthy"] if health_row else 0) or 0)
    total_repos = int((health_row["total"] if health_row else 0) or 0)

    # multi_repo_dev_work_share denominator: count of DevWorks created in window.
    dw_where: list[str] = []
    dw_params: list[str] = []
    append_range(dw_where, dw_params, "created_at", since, until)
    dw_sql = "SELECT COUNT(*) AS c FROM dev_works"
    if dw_where:
        dw_sql += " WHERE " + " AND ".join(dw_where)
    dw_row = await db.fetchone(dw_sql, tuple(dw_params))
    created_count = int((dw_row["c"] if dw_row else 0) or 0)

    # Numerator: count of DevWorks with >1 dev_work_repos rows. The JOIN on
    # dev_works lets the time filter apply to dev_works.created_at (matches
    # denominator semantics) and defensively drops any orphan dev_work_repos
    # rows whose parent dev_work has been deleted.
    multi_where: list[str] = []
    multi_params: list[str] = []
    append_range(multi_where, multi_params, "dw.created_at", since, until)
    multi_sql = (
        "SELECT COUNT(*) AS c FROM ("
        "SELECT dwr.dev_work_id "
        "FROM dev_work_repos dwr "
        "JOIN dev_works dw ON dw.id = dwr.dev_work_id"
    )
    if multi_where:
        multi_sql += " WHERE " + " AND ".join(multi_where)
    multi_sql += " GROUP BY dwr.dev_work_id HAVING COUNT(*) > 1)"
    multi_row = await db.fetchone(multi_sql, tuple(multi_params))
    multi_count = int((multi_row["c"] if multi_row else 0) or 0)

    multi_repo_dev_work_share = (
        multi_count / created_count if created_count > 0 else 0.0
    )
    healthy_repos_share = (
        healthy / total_repos if total_repos > 0 else 0.0
    )

    return RepoRegistryMetrics(
        multi_repo_dev_work_share=multi_repo_dev_work_share,
        healthy_repos_share=healthy_repos_share,
    )
