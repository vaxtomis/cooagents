"""Shared helpers for metrics projections.

Both ``routes/metrics.py`` (workspace metrics) and
``routes/metrics_repos.py`` (repo-registry metrics) accept an optional
``?since=&until=`` ISO8601 window and translate it into parameterized SQL
range predicates. These helpers live here so neither route reaches across
the leading-underscore convention into a sibling module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.exceptions import BadRequestError


def parse_iso(value: str | None, name: str) -> str | None:
    """Parse and normalize to the canonical UTC isoformat used by writers.

    DB rows are written as ``datetime.now(timezone.utc).isoformat()`` (offset
    ``+00:00``). Clients may pass ``Z`` suffix or naive/alt-offset values;
    normalize so lexicographic ``>=`` / ``<=`` binds match stored rows.
    Naive input is assumed UTC.
    """
    if value is None:
        return None
    raw = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise BadRequestError(
            f"{name} must be ISO8601 (e.g. 2026-04-23T00:00:00+00:00): {exc}"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def append_range(
    where: list[str],
    params: list[Any],
    column: str,
    since: str | None,
    until: str | None,
) -> None:
    """Append inclusive ``since`` / ``until`` predicates to a WHERE list.

    ``column`` is interpolated unquoted — callers must pass a hardcoded
    column name (never user input) to avoid SQL injection.
    """
    if since is not None:
        where.append(f"{column} >= ?")
        params.append(since)
    if until is not None:
        where.append(f"{column} <= ?")
        params.append(until)
