"""DB-layer registry for the ``repos`` table (Phase 1, repo-registry).

Mirrors the style of :class:`src.agent_hosts.repo.AgentHostRepo`: explicit
boundary validation, JSON-encoded list columns, and isolated transactions
per write so callers don't need to wrap them.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.config import ReposConfig
from src.exceptions import BadRequestError, ConflictError, NotFoundError

logger = logging.getLogger(__name__)


_VALID_FETCH_STATUSES: frozenset[str] = frozenset(
    {"unknown", "healthy", "stale", "error"}
)
# Cap on persisted error strings — same rationale as agent_hosts._sanitize_health_err.
_MAX_FETCH_ERR_LEN = 256
# fetch_status values that imply we successfully reached the remote and can
# stamp ``last_fetched_at`` with the current time. ``stale`` is *not*
# included: staleness means the row is old, so refreshing the timestamp
# when transitioning healthy→stale would contradict the marker itself.
_SUCCESSFUL_FETCH_STATUSES: frozenset[str] = frozenset({"healthy"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_repo_id() -> str:
    return f"repo-{uuid.uuid4().hex[:12]}"


def _sanitize_fetch_err(err: str | None) -> str | None:
    if err is None:
        return None
    cleaned = "".join(ch for ch in err if ch.isprintable() or ch == " ")
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_FETCH_ERR_LEN:
        cleaned = cleaned[: _MAX_FETCH_ERR_LEN - 1] + "…"
    return cleaned


def _decode_labels(value: Any) -> list[str]:
    """Normalise the ``labels_json`` column for callers."""
    if value is None or value == "":
        return []
    try:
        out = json.loads(value)
    except (TypeError, ValueError):
        # Manual SQL edits or restored backups can corrupt this column. Log so
        # the silent fallback to [] doesn't hide schema corruption from ops.
        logger.warning("malformed labels_json on repos row: %r", value)
        return []
    return [str(x) for x in out] if isinstance(out, list) else []


def _row_with_labels(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["labels"] = _decode_labels(out.pop("labels_json", "[]"))
    return out


class RepoRegistryRepo:
    """DB-layer CRUD for the ``repos`` table."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def upsert(
        self,
        *,
        id: str,
        name: str,
        url: str,
        default_branch: str = "main",
        vendor: str | None = None,
        credential_ref: str | None = None,
        bare_clone_path: str | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        if not name or not name.strip():
            raise BadRequestError("repo name must not be empty")
        if not url or not url.strip():
            raise BadRequestError("repo url must not be empty")
        if not default_branch or not default_branch.strip():
            raise BadRequestError("repo default_branch must not be empty")

        labels_json = json.dumps(list(labels or []))
        now = _now()

        async with self.db.transaction():
            existing = await self.db.fetchone(
                "SELECT id, created_at FROM repos WHERE id=?",
                (id,),
            )
            if existing:
                # Preserve runtime state (fetch_status / last_fetched_at /
                # last_fetch_err) on plain upsert. Use update_fetch_status()
                # to mutate those columns explicitly.
                await self.db.execute(
                    "UPDATE repos SET name=?, url=?, vendor=?, "
                    "default_branch=?, credential_ref=?, bare_clone_path=?, "
                    "labels_json=?, updated_at=? WHERE id=?",
                    (name, url, vendor, default_branch, credential_ref,
                     bare_clone_path, labels_json, now, id),
                )
            else:
                await self.db.execute(
                    "INSERT INTO repos(id, name, url, vendor, "
                    "default_branch, credential_ref, bare_clone_path, "
                    "labels_json, fetch_status, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (id, name, url, vendor, default_branch, credential_ref,
                     bare_clone_path, labels_json, "unknown", now, now),
                )

        row = await self.db.fetchone("SELECT * FROM repos WHERE id=?", (id,))
        if row is None:  # pragma: no cover - upsert just wrote this row
            raise RuntimeError(
                f"repos row {id!r} disappeared between upsert and read"
            )
        return _row_with_labels(row)

    async def get(self, id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM repos WHERE id=?", (id,))
        if row is None:
            return None
        return _row_with_labels(row)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM repos WHERE name=?", (name,)
        )
        if row is None:
            return None
        return _row_with_labels(row)

    async def list_all(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall("SELECT * FROM repos ORDER BY name")
        return [_row_with_labels(r) for r in rows]

    async def update_fetch_status(
        self,
        id: str,
        *,
        status: str,
        err: str | None = None,
        bare_clone_path: str | None = None,
    ) -> None:
        if status not in _VALID_FETCH_STATUSES:
            raise BadRequestError(
                f"invalid fetch_status={status!r}; "
                f"expected one of {sorted(_VALID_FETCH_STATUSES)}"
            )
        now = _now()
        # Stamp last_fetched_at only when we actually reached the remote.
        last_fetched_at = now if status in _SUCCESSFUL_FETCH_STATUSES else None

        sets = [
            "fetch_status=?",
            "last_fetched_at=COALESCE(?, last_fetched_at)",
            "last_fetch_err=?",
        ]
        params: list[Any] = [status, last_fetched_at, _sanitize_fetch_err(err)]
        if bare_clone_path is not None:
            sets.append("bare_clone_path=?")
            params.append(bare_clone_path)
        sets.append("updated_at=?")
        params.append(now)
        params.append(id)
        await self.db.execute(
            f"UPDATE repos SET {', '.join(sets)} WHERE id=?",
            tuple(params),
        )

    async def delete(self, id: str) -> None:
        # Existence first so an unknown id raises NotFoundError before we
        # spend two FK count scans on it.
        existing = await self.db.fetchone(
            "SELECT id FROM repos WHERE id=?", (id,)
        )
        if existing is None:
            raise NotFoundError(f"repo not found: {id!r}")
        # Refuse if any FK reference exists in design_work_repos /
        # dev_work_repos. The DB-side ON DELETE RESTRICT is defense-in-depth;
        # the Python-side check produces a usable error message.
        for table in ("design_work_repos", "dev_work_repos"):
            row = await self.db.fetchone(
                f"SELECT COUNT(*) AS c FROM {table} WHERE repo_id=?",
                (id,),
            )
            if row and row["c"] > 0:
                raise ConflictError(
                    f"repo {id!r} is referenced by {row['c']} {table} rows; "
                    "remove those references before deleting"
                )
        await self.db.execute("DELETE FROM repos WHERE id=?", (id,))

    async def sync_from_config(
        self, config: ReposConfig
    ) -> dict[str, list[str]]:
        """Reconcile the ``repos`` table against ``config/repos.yaml``.

        - Rows in config (matched by ``name``): upsert (preserves runtime
          state — ``fetch_status`` / ``last_fetched_at`` / ``last_fetch_err``).
        - Rows in DB but not in config: marked ``fetch_status='unknown'``.
          **Never deleted** — in-flight FK references from
          ``dev_work_repos`` / ``design_work_repos`` must not break.
        - New repos in config: allocated a fresh ``repo-<hex12>`` id.
        """
        wanted_names = {r.name for r in config.repos}

        upserted_ids: list[str] = []
        for r in config.repos:
            existing = await self.get_by_name(r.name)
            repo_id = existing["id"] if existing else _new_repo_id()
            await self.upsert(
                id=repo_id,
                name=r.name,
                url=r.url,
                default_branch=r.default_branch,
                vendor=r.vendor,
                # v1 stores the SSH key path directly in credential_ref. The
                # column name is the abstraction seam for a future Vault swap.
                credential_ref=r.ssh_key_path,
                labels=r.labels,
            )
            upserted_ids.append(repo_id)

        existing_rows = await self.db.fetchall("SELECT id, name FROM repos")
        stale_ids = [
            r["id"] for r in existing_rows if r["name"] not in wanted_names
        ]
        for sid in stale_ids:
            await self.update_fetch_status(sid, status="unknown", err=None)

        logger.info(
            "repos sync: upserted=%d marked_unknown=%d",
            len(upserted_ids), len(stale_ids),
        )
        return {"upserted": upserted_ids, "marked_unknown": stale_ids}
