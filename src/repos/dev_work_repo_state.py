"""DB-layer access to ``dev_work_repos.push_state`` (Phase 5, repo-registry).

Single seam for the worker push-state writeback route and for the read
paths that need the JOINed ``repos.url`` / ``repos.ssh_key_path``.

Single-writer-per-state-value invariant:
  * the SM (``src.dev_work_sm``) is the only writer of ``pending`` (on
    ``dev_work_repos`` INSERT at create time).
  * :class:`DevWorkRepoStateRepo` is the only writer of ``pushed`` /
    ``failed``, exclusively via :meth:`update_push_state`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.exceptions import BadRequestError, ConflictError, NotFoundError

# Mirrors the schema CHECK on dev_work_repos.push_state.
_VALID_PUSH_STATES: frozenset[str] = frozenset({"pending", "pushed", "failed"})
# Worker-writable subset — the SM owns ``pending`` and the route refuses
# to let a malformed worker unwind state by re-asserting it.
_WORKER_WRITABLE_STATES: frozenset[str] = frozenset({"pushed", "failed"})
# Cap on persisted error strings — same rationale as
# src.agent_hosts.repo._sanitize_health_err and
# src.repos.registry._sanitize_fetch_err.
_MAX_PUSH_ERR_LEN = 256


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_push_err(err: str | None) -> str | None:
    if err is None:
        return None
    # ``str.isprintable()`` already accepts U+0020 SPACE; this strips
    # control bytes (NUL, BEL, \t, \r, \n, …) without dropping spaces.
    cleaned = "".join(ch for ch in err if ch.isprintable())
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_PUSH_ERR_LEN:
        cleaned = cleaned[: _MAX_PUSH_ERR_LEN - 1] + "…"
    return cleaned


class DevWorkRepoStateRepo:
    """DB-layer reads + the single push-state writer."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def list_for_dev_work(
        self, dev_work_id: str
    ) -> list[dict[str, Any]]:
        """Return rows joined with ``repos.url`` and ``repos.ssh_key_path``.

        Caller-side ``dict[str, Any]`` so we can layer the pydantic
        ``WorkerRepoHandoff`` view at the route boundary without dragging
        ``src.models`` into this module.
        """
        rows = await self.db.fetchall(
            "SELECT dwr.repo_id, dwr.mount_name, dwr.base_branch, "
            "dwr.base_rev, dwr.devwork_branch, dwr.push_state, "
            "dwr.push_err, dwr.is_primary, "
            "r.url AS url, r.ssh_key_path AS ssh_key_path "
            "FROM dev_work_repos dwr "
            "JOIN repos r ON r.id = dwr.repo_id "
            "WHERE dwr.dev_work_id=? "
            "ORDER BY dwr.mount_name",
            (dev_work_id,),
        )
        return [dict(r) for r in rows]

    async def list_for_dev_works_batch(
        self, dev_work_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Bulk-fetch handoff rows keyed by ``dev_work_id``.

        Avoids N+1 on the list-DevWork endpoint. Returns an empty dict
        when ``dev_work_ids`` is empty so callers don't need to gate.
        """
        if not dev_work_ids:
            return {}
        placeholders = ",".join("?" for _ in dev_work_ids)
        rows = await self.db.fetchall(
            f"SELECT dwr.dev_work_id, dwr.repo_id, dwr.mount_name, "
            f"dwr.base_branch, dwr.base_rev, dwr.devwork_branch, "
            f"dwr.push_state, dwr.push_err, dwr.is_primary, "
            f"r.url AS url, r.ssh_key_path AS ssh_key_path "
            f"FROM dev_work_repos dwr "
            f"JOIN repos r ON r.id = dwr.repo_id "
            f"WHERE dwr.dev_work_id IN ({placeholders}) "
            f"ORDER BY dwr.dev_work_id, dwr.mount_name",
            tuple(dev_work_ids),
        )
        grouped: dict[str, list[dict[str, Any]]] = {
            dwid: [] for dwid in dev_work_ids
        }
        for r in rows:
            grouped[r["dev_work_id"]].append(dict(r))
        return grouped

    async def update_push_state(
        self,
        dev_work_id: str,
        mount_name: str,
        *,
        push_state: str,
        error_msg: str | None = None,
    ) -> dict[str, Any]:
        """Forward-only push outcome writeback.

        Allowed transitions:
          * ``pending -> pushed`` / ``pending -> failed``
          * ``failed  -> pushed`` (a retry that ultimately succeeded)
          * ``pushed  -> pushed`` (idempotent no-op for at-least-once
            workers)

        Rejected:
          * ``push_state`` not in :data:`_WORKER_WRITABLE_STATES`
            (e.g. ``"pending"``) → :class:`BadRequestError`.
          * unknown ``(dev_work_id, mount_name)`` →
            :class:`NotFoundError`.
          * ``pushed -> failed`` → :class:`ConflictError`. Push outcomes
            don't regress: if a row reports ``pushed`` once, the remote
            already has the branch and a later ``failed`` is wrong.
        """
        if push_state not in _WORKER_WRITABLE_STATES:
            raise BadRequestError(
                f"invalid push_state={push_state!r}; "
                f"expected one of {sorted(_WORKER_WRITABLE_STATES)}"
            )
        now = _now()
        # Clear push_err on success; sanitise + persist on failure.
        err = _sanitize_push_err(error_msg) if push_state == "failed" else None
        # Atomic guard: fold the ``pushed -> failed`` rejection into the
        # UPDATE WHERE clause so two concurrent workers can't both pass
        # a check-then-update guard. Branch on rowcount + a follow-up
        # SELECT to disambiguate "row missing" vs "guard tripped".
        rowcount = await self.db.execute_rowcount(
            "UPDATE dev_work_repos SET push_state=?, push_err=?, "
            "updated_at=? WHERE dev_work_id=? AND mount_name=? "
            "AND NOT (push_state='pushed' AND ?='failed')",
            (push_state, err, now, dev_work_id, mount_name, push_state),
        )
        if rowcount == 0:
            existing = await self.db.fetchone(
                "SELECT push_state FROM dev_work_repos "
                "WHERE dev_work_id=? AND mount_name=?",
                (dev_work_id, mount_name),
            )
            if existing is None:
                raise NotFoundError(
                    f"dev_work_repo not found: dev_work_id={dev_work_id!r} "
                    f"mount_name={mount_name!r}"
                )
            # The only WHERE clause that filters an existing row is the
            # ``pushed -> failed`` guard.
            raise ConflictError(
                f"push_state for {dev_work_id!r}/{mount_name!r} is "
                f"already 'pushed'; refusing transition to 'failed'",
                current_stage="pushed",
            )
        row = await self.db.fetchone(
            "SELECT * FROM dev_work_repos "
            "WHERE dev_work_id=? AND mount_name=?",
            (dev_work_id, mount_name),
        )
        # ``row`` is guaranteed non-None here: we just successfully
        # UPDATEd the same primary key. The cast keeps mypy honest.
        assert row is not None
        return dict(row)
