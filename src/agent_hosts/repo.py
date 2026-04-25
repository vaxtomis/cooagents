"""DB-layer repos for ``agent_hosts`` and ``agent_dispatches`` (Phase 8a).

Mirrors the style of :class:`src.storage.registry.WorkspaceFilesRepo`:
explicit boundary validation, JSON-encoded list columns, and isolated
transactions per write so callers don't need to wrap them.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.config import AgentsConfig
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.models import LOCAL_HOST_ID

logger = logging.getLogger(__name__)


_VALID_AGENT_TYPES: frozenset[str] = frozenset({"claude", "codex", "both"})
_VALID_HEALTH: frozenset[str] = frozenset({"unknown", "healthy", "unhealthy"})
_ACTIVE_DISPATCH_STATES: frozenset[str] = frozenset({"queued", "running"})
_VALID_DISPATCH_STATES: frozenset[str] = frozenset(
    {"queued", "running", "succeeded", "failed", "timeout"}
)
_VALID_CORRELATION_KINDS: frozenset[str] = frozenset({"design_work", "dev_work"})

# Cap on persisted error strings. Raw asyncssh / OS exceptions can include
# IPs, banner data, or multi-line stack frames. The DB column ends up in API
# responses, so trim aggressively and strip control bytes.
_MAX_HEALTH_ERR_LEN = 256


def _sanitize_health_err(err: str | None) -> str | None:
    if err is None:
        return None
    cleaned = "".join(ch for ch in err if ch.isprintable() or ch == " ")
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_HEALTH_ERR_LEN:
        cleaned = cleaned[: _MAX_HEALTH_ERR_LEN - 1] + "…"
    return cleaned


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_host_id() -> str:
    return f"ah-{uuid.uuid4().hex[:12]}"


def _new_dispatch_id() -> str:
    return f"ad-{uuid.uuid4().hex[:12]}"


def _decode_labels(value: Any) -> list[str]:
    """Normalise the ``labels_json`` column for callers."""
    if value is None or value == "":
        return []
    try:
        out = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(x) for x in out] if isinstance(out, list) else []


def _row_with_labels(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["labels"] = _decode_labels(out.pop("labels_json", "[]"))
    return out


class AgentHostRepo:
    """DB-layer CRUD for the ``agent_hosts`` table."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def upsert(
        self,
        *,
        id: str,
        host: str,
        agent_type: str,
        max_concurrent: int = 1,
        ssh_key: str | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        if agent_type not in _VALID_AGENT_TYPES:
            raise BadRequestError(
                f"invalid agent_type={agent_type!r}; "
                f"expected one of {sorted(_VALID_AGENT_TYPES)}"
            )
        if max_concurrent < 1:
            raise BadRequestError(
                f"max_concurrent must be >= 1, got {max_concurrent}"
            )
        labels_json = json.dumps(list(labels or []))
        now = _now()

        async with self.db.transaction():
            existing = await self.db.fetchone(
                "SELECT id, created_at, health_status FROM agent_hosts WHERE id=?",
                (id,),
            )
            if existing:
                # Preserve health columns on plain upsert (sync_from_config
                # case). Use update_health() to mutate health.
                await self.db.execute(
                    "UPDATE agent_hosts SET host=?, agent_type=?, "
                    "max_concurrent=?, ssh_key=?, labels_json=?, updated_at=? "
                    "WHERE id=?",
                    (host, agent_type, max_concurrent, ssh_key, labels_json,
                     now, id),
                )
                created_at = existing["created_at"]
            else:
                created_at = now
                await self.db.execute(
                    "INSERT INTO agent_hosts(id, host, agent_type, "
                    "max_concurrent, ssh_key, labels_json, health_status, "
                    "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (id, host, agent_type, max_concurrent, ssh_key,
                     labels_json, "unknown", created_at, now),
                )

        row = await self.db.fetchone(
            "SELECT * FROM agent_hosts WHERE id=?", (id,)
        )
        if row is None:  # pragma: no cover - upsert just wrote this row
            raise RuntimeError(
                f"agent_hosts row {id!r} disappeared between upsert and read"
            )
        return _row_with_labels(row)

    async def get(self, id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM agent_hosts WHERE id=?", (id,)
        )
        if row is None:
            return None
        return _row_with_labels(row)

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM agent_hosts ORDER BY id"
        )
        return [_row_with_labels(r) for r in rows]

    async def list_active(self) -> list[dict[str, Any]]:
        """Hosts currently considered eligible for dispatch (healthy only)."""
        rows = await self.db.fetchall(
            "SELECT * FROM agent_hosts WHERE health_status='healthy' "
            "ORDER BY id"
        )
        return [_row_with_labels(r) for r in rows]

    async def update_health(
        self,
        id: str,
        *,
        status: str,
        err: str | None = None,
    ) -> None:
        if status not in _VALID_HEALTH:
            raise BadRequestError(
                f"invalid health status={status!r}; "
                f"expected one of {sorted(_VALID_HEALTH)}"
            )
        now = _now()
        await self.db.execute(
            "UPDATE agent_hosts SET health_status=?, last_health_at=?, "
            "last_health_err=?, updated_at=? WHERE id=?",
            (status, now, _sanitize_health_err(err), now, id),
        )

    async def delete(self, id: str) -> None:
        if id == LOCAL_HOST_ID:
            raise BadRequestError("cannot delete the local host")
        # Refuse if active dispatches exist — orphaned FK rows would block
        # later reads. Same defensive guard as workspace deletion.
        active = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM agent_dispatches "
            "WHERE host_id=? AND state IN ('queued','running')",
            (id,),
        )
        if active and active["c"] > 0:
            raise ConflictError(
                f"agent host {id!r} has {active['c']} in-flight dispatches; "
                "wait for them to finish before deleting"
            )
        existing = await self.db.fetchone(
            "SELECT id FROM agent_hosts WHERE id=?", (id,)
        )
        if existing is None:
            raise NotFoundError(f"agent host not found: {id!r}")
        await self.db.execute("DELETE FROM agent_hosts WHERE id=?", (id,))

    async def sync_from_config(
        self, config: AgentsConfig
    ) -> dict[str, list[str]]:
        """Reconcile agent_hosts table against ``config/agents.yaml``.

        - Rows in config: upsert (preserves health).
        - Rows in DB but not in config: marked ``unknown`` — never deleted
          (in-flight dispatches must not lose their FK target).
        - Always ensures the reserved ``local`` host exists, even when the
          YAML omits it.
        """
        wanted_ids = {h.id for h in config.hosts}

        upserted: list[str] = []
        for h in config.hosts:
            await self.upsert(
                id=h.id,
                host=h.host,
                agent_type=h.agent_type,
                max_concurrent=h.max_concurrent,
                ssh_key=h.ssh_key,
                labels=h.labels,
            )
            upserted.append(h.id)

        if LOCAL_HOST_ID not in wanted_ids:
            # Always-present invariant (see plan AD10 / 8a.12 GOTCHA).
            await self.upsert(
                id=LOCAL_HOST_ID,
                host=LOCAL_HOST_ID,
                agent_type="both",
                max_concurrent=1,
                labels=[],
            )
            upserted.append(LOCAL_HOST_ID)

        # Mark everything else as unknown so the probe loop will re-evaluate.
        existing = await self.db.fetchall(
            "SELECT id FROM agent_hosts"
        )
        stale = [
            r["id"] for r in existing
            if r["id"] not in upserted and r["id"] != LOCAL_HOST_ID
        ]
        for sid in stale:
            await self.update_health(sid, status="unknown", err=None)

        return {"upserted": upserted, "marked_unknown": stale}


class AgentDispatchRepo:
    """DB-layer CRUD for the ``agent_dispatches`` lifecycle."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def start(
        self,
        *,
        host_id: str,
        workspace_id: str,
        correlation_id: str,
        correlation_kind: str,
    ) -> dict[str, Any]:
        if correlation_kind not in _VALID_CORRELATION_KINDS:
            raise BadRequestError(
                f"invalid correlation_kind={correlation_kind!r}; "
                f"expected one of {sorted(_VALID_CORRELATION_KINDS)}"
            )
        ad_id = _new_dispatch_id()
        now = _now()
        await self.db.execute(
            "INSERT INTO agent_dispatches(id, host_id, workspace_id, "
            "correlation_id, correlation_kind, state, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (ad_id, host_id, workspace_id, correlation_id, correlation_kind,
             "queued", now, now),
        )
        return {
            "id": ad_id,
            "host_id": host_id,
            "workspace_id": workspace_id,
            "correlation_id": correlation_id,
            "correlation_kind": correlation_kind,
            "state": "queued",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "created_at": now,
            "updated_at": now,
        }

    async def mark_running(self, id: str) -> None:
        now = _now()
        rowcount = await self.db.execute_rowcount(
            "UPDATE agent_dispatches SET state='running', started_at=?, "
            "updated_at=? WHERE id=?",
            (now, now, id),
        )
        if rowcount == 0:
            raise NotFoundError(f"agent dispatch not found: {id!r}")

    async def mark_finished(
        self,
        id: str,
        *,
        state: str,
        exit_code: int | None,
    ) -> None:
        if state not in _VALID_DISPATCH_STATES:
            raise BadRequestError(
                f"invalid dispatch state={state!r}; "
                f"expected one of {sorted(_VALID_DISPATCH_STATES)}"
            )
        now = _now()
        rowcount = await self.db.execute_rowcount(
            "UPDATE agent_dispatches SET state=?, exit_code=?, "
            "finished_at=?, updated_at=? WHERE id=?",
            (state, exit_code, now, now, id),
        )
        if rowcount == 0:
            raise NotFoundError(f"agent dispatch not found: {id!r}")

    async def get(self, id: str) -> dict[str, Any] | None:
        return await self.db.fetchone(
            "SELECT * FROM agent_dispatches WHERE id=?", (id,)
        )

    async def list_for_correlation(
        self, *, correlation_kind: str, correlation_id: str
    ) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            "SELECT * FROM agent_dispatches WHERE correlation_kind=? "
            "AND correlation_id=? ORDER BY created_at",
            (correlation_kind, correlation_id),
        )
