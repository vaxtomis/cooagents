"""DB-layer lifecycle for host-local agent execution leases.

``agent_dispatches`` records the logical LLM call. ``agent_executions``
records the concrete process/session cleanup authority on one host. Janitors
must only act on rows created here, never on process names alone.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.exceptions import BadRequestError, NotFoundError


_VALID_AGENTS: frozenset[str] = frozenset({"claude", "codex"})
_VALID_EXECUTION_MODES: frozenset[str] = frozenset({"local", "ssh"})
_VALID_CORRELATION_KINDS: frozenset[str] = frozenset(
    {"design_work", "dev_work"}
)
_VALID_STATES: frozenset[str] = frozenset(
    {
        "starting",
        "running",
        "stale",
        "cancelling",
        "terminated",
        "killed",
        "exited",
        "abandoned",
    }
)
_LIVE_STATES: tuple[str, ...] = ("starting", "running", "stale", "cancelling")


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _new_execution_id() -> str:
    return f"aex-{uuid.uuid4().hex[:12]}"


def new_run_token() -> str:
    return uuid.uuid4().hex


class AgentExecutionRepo:
    """CRUD and state transitions for ``agent_executions``."""

    def __init__(self, db: Any, *, lease_ttl_s: int = 120) -> None:
        self.db = db
        self.lease_ttl_s = lease_ttl_s

    def _lease_expiry(self, now: datetime | None = None) -> str:
        base = now or _now_dt()
        return (base + timedelta(seconds=self.lease_ttl_s)).isoformat()

    async def create_starting(
        self,
        *,
        dispatch_id: str | None,
        host_id: str,
        agent: str,
        execution_mode: str,
        correlation_kind: str,
        correlation_id: str,
        cwd: str,
        session_name: str | None = None,
        session_role: str | None = None,
        run_token: str | None = None,
    ) -> dict[str, Any]:
        if agent not in _VALID_AGENTS:
            raise BadRequestError(
                f"invalid execution agent={agent!r}; "
                f"expected one of {sorted(_VALID_AGENTS)}"
            )
        if execution_mode not in _VALID_EXECUTION_MODES:
            raise BadRequestError(
                f"invalid execution_mode={execution_mode!r}; "
                f"expected one of {sorted(_VALID_EXECUTION_MODES)}"
            )
        if correlation_kind not in _VALID_CORRELATION_KINDS:
            raise BadRequestError(
                f"invalid correlation_kind={correlation_kind!r}; "
                f"expected one of {sorted(_VALID_CORRELATION_KINDS)}"
            )
        now_dt = _now_dt()
        now = now_dt.isoformat()
        execution_id = _new_execution_id()
        token = run_token or new_run_token()
        await self.db.execute(
            "INSERT INTO agent_executions("
            "id, dispatch_id, host_id, agent, execution_mode, "
            "correlation_kind, correlation_id, run_token, session_name, "
            "session_role, cwd, state, last_heartbeat_at, lease_expires_at, "
            "started_at, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                execution_id,
                dispatch_id,
                host_id,
                agent,
                execution_mode,
                correlation_kind,
                correlation_id,
                token,
                session_name,
                session_role,
                cwd,
                "starting",
                now,
                self._lease_expiry(now_dt),
                now,
                now,
                now,
            ),
        )
        row = await self.get(execution_id)
        assert row is not None
        return row

    async def get(self, execution_id: str) -> dict[str, Any] | None:
        return await self.db.fetchone(
            "SELECT * FROM agent_executions WHERE id=?", (execution_id,)
        )

    async def mark_process_started(
        self,
        execution_id: str,
        *,
        pid: int,
        pgid: int | None,
        pid_starttime: str | None,
        cwd: str | None = None,
        worker_pid: int | None = None,
        worker_pid_starttime: str | None = None,
    ) -> None:
        now_dt = _now_dt()
        now = now_dt.isoformat()
        rowcount = await self.db.execute_rowcount(
            "UPDATE agent_executions SET state='running', pid=?, pgid=?, "
            "pid_starttime=?, cwd=COALESCE(?, cwd), worker_pid=COALESCE(?, worker_pid), "
            "worker_pid_starttime=COALESCE(?, worker_pid_starttime), "
            "last_heartbeat_at=?, lease_expires_at=?, finished_at=NULL, "
            "exit_code=NULL, updated_at=? "
            "WHERE id=?",
            (
                pid,
                pgid,
                pid_starttime,
                cwd,
                worker_pid,
                worker_pid_starttime,
                now,
                self._lease_expiry(now_dt),
                now,
                execution_id,
            ),
        )
        if rowcount == 0:
            raise NotFoundError(f"agent execution not found: {execution_id!r}")

    async def heartbeat(self, execution_id: str) -> None:
        now_dt = _now_dt()
        now = now_dt.isoformat()
        rowcount = await self.db.execute_rowcount(
            "UPDATE agent_executions SET last_heartbeat_at=?, "
            "lease_expires_at=?, updated_at=? WHERE id=? "
            "AND state IN ('starting','running','stale','cancelling')",
            (now, self._lease_expiry(now_dt), now, execution_id),
        )
        if rowcount == 0:
            raise NotFoundError(
                f"live agent execution not found: {execution_id!r}"
            )

    async def mark_state(
        self,
        execution_id: str,
        *,
        state: str,
        exit_code: int | None = None,
        cleanup_reason: str | None = None,
    ) -> None:
        if state not in _VALID_STATES:
            raise BadRequestError(
                f"invalid execution state={state!r}; "
                f"expected one of {sorted(_VALID_STATES)}"
            )
        now = _now()
        terminal = state in {"terminated", "killed", "exited", "abandoned"}
        rowcount = await self.db.execute_rowcount(
            "UPDATE agent_executions SET state=?, exit_code=COALESCE(?, exit_code), "
            "cleanup_reason=COALESCE(?, cleanup_reason), "
            "finished_at=CASE WHEN ? THEN COALESCE(finished_at, ?) ELSE finished_at END, "
            "updated_at=? WHERE id=?",
            (
                state,
                exit_code,
                cleanup_reason,
                1 if terminal else 0,
                now,
                now,
                execution_id,
            ),
        )
        if rowcount == 0:
            raise NotFoundError(f"agent execution not found: {execution_id!r}")

    async def mark_exited(
        self, execution_id: str, *, exit_code: int | None,
    ) -> None:
        await self.mark_state(execution_id, state="exited", exit_code=exit_code)

    async def mark_cleanup_started(
        self, execution_id: str, *, reason: str,
    ) -> None:
        now = _now()
        rowcount = await self.db.execute_rowcount(
            "UPDATE agent_executions SET state='cancelling', "
            "cleanup_reason=?, cleanup_attempts=cleanup_attempts+1, "
            "updated_at=? WHERE id=?",
            (reason, now, execution_id),
        )
        if rowcount == 0:
            raise NotFoundError(f"agent execution not found: {execution_id!r}")

    async def list_expired_for_host(
        self,
        host_id: str,
        *,
        now: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in _LIVE_STATES)
        return await self.db.fetchall(
            "SELECT * FROM agent_executions WHERE host_id=? "
            f"AND state IN ({placeholders}) AND lease_expires_at<=? "
            "ORDER BY lease_expires_at LIMIT ?",
            (host_id, *_LIVE_STATES, now or _now(), limit),
        )

    async def list_for_correlation(
        self, *, correlation_kind: str, correlation_id: str,
    ) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            "SELECT * FROM agent_executions WHERE correlation_kind=? "
            "AND correlation_id=? ORDER BY started_at",
            (correlation_kind, correlation_id),
        )
