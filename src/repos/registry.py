"""DB-layer registry for the ``repos`` table (Phase 1, repo-registry).

Mirrors the style of :class:`src.agent_hosts.repo.AgentHostRepo`: explicit
boundary validation and isolated transactions per write so callers don't
need to wrap them.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.config import ReposConfig
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.models import RepoRole

logger = logging.getLogger(__name__)


_VALID_FETCH_STATUSES: frozenset[str] = frozenset(
    {"unknown", "healthy", "error"}
)
# Phase 4 (repo-registry): closed enum stored on repos.role. Single source of
# truth is src.models.RepoRole — derive here so adding a role only requires
# updating the enum.
_VALID_REPO_ROLES: frozenset[str] = frozenset(r.value for r in RepoRole)
# Cap on persisted error strings — same rationale as agent_hosts._sanitize_health_err.
_MAX_FETCH_ERR_LEN = 256
# fetch_status values that imply we successfully reached the remote and can
# stamp ``last_fetched_at`` with the current time.
_SUCCESSFUL_FETCH_STATUSES: frozenset[str] = frozenset({"healthy"})
_REPO_SORT_SQL: dict[str, str] = {
    "name_asc": "LOWER(name) ASC, id ASC",
    "name_desc": "LOWER(name) DESC, id DESC",
    "updated_desc": "updated_at DESC, id DESC",
    "updated_asc": "updated_at ASC, id ASC",
    "last_fetched_desc": "COALESCE(last_fetched_at, '') DESC, id DESC",
    "last_fetched_asc": "COALESCE(last_fetched_at, '') ASC, id ASC",
}


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
        local_path: str | None = None,
        default_branch: str = "main",
        ssh_key_path: str | None = None,
        bare_clone_path: str | None = None,
        role: str = "other",
    ) -> dict[str, Any]:
        if not name or not name.strip():
            raise BadRequestError("repo name must not be empty")
        if not url or not url.strip():
            raise BadRequestError("repo url must not be empty")
        if not default_branch or not default_branch.strip():
            raise BadRequestError("repo default_branch must not be empty")
        if role not in _VALID_REPO_ROLES:
            raise BadRequestError(
                f"invalid role={role!r}; expected one of "
                f"{sorted(_VALID_REPO_ROLES)}"
            )

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
                    "UPDATE repos SET name=?, url=?, local_path=?, default_branch=?, "
                    "ssh_key_path=?, bare_clone_path=?, role=?, updated_at=? "
                    "WHERE id=?",
                    (name, url, local_path, default_branch, ssh_key_path,
                     bare_clone_path, role, now, id),
                )
            else:
                await self.db.execute(
                    "INSERT INTO repos(id, name, url, local_path, default_branch, "
                    "ssh_key_path, bare_clone_path, role, fetch_status, "
                    "created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (id, name, url, local_path, default_branch, ssh_key_path,
                     bare_clone_path, role, "unknown", now, now),
                )

        row = await self.db.fetchone("SELECT * FROM repos WHERE id=?", (id,))
        if row is None:  # pragma: no cover - upsert just wrote this row
            raise RuntimeError(
                f"repos row {id!r} disappeared between upsert and read"
            )
        return dict(row)

    async def get(self, id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM repos WHERE id=?", (id,))
        if row is None:
            return None
        return dict(row)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM repos WHERE name=?", (name,)
        )
        if row is None:
            return None
        return dict(row)

    async def get_by_local_path(self, path: str) -> dict[str, Any] | None:
        row = await self.db.fetchone(
            "SELECT * FROM repos WHERE local_path=?", (path,)
        )
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def _build_list_where(
        *,
        role: str | None = None,
        fetch_status: str | None = None,
        query: str | None = None,
    ) -> tuple[str, list[object]]:
        conditions: list[str] = []
        params: list[object] = []
        if role:
            conditions.append("role=?")
            params.append(role)
        if fetch_status:
            conditions.append("fetch_status=?")
            params.append(fetch_status)
        if query:
            like = f"%{query.strip()}%"
            conditions.append(
                "(name LIKE ? OR url LIKE ? OR local_path LIKE ? "
                "OR default_branch LIKE ?)"
            )
            params.extend([like, like, like, like])
        where_sql = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        return where_sql, params

    @staticmethod
    def _order_sql(sort: str) -> str:
        try:
            return _REPO_SORT_SQL[sort]
        except KeyError as exc:
            raise BadRequestError(
                f"invalid repo sort={sort!r}; expected one of "
                f"{sorted(_REPO_SORT_SQL)}"
            ) from exc

    async def list_all(
        self,
        *,
        role: str | None = None,
        fetch_status: str | None = None,
        query: str | None = None,
        sort: str = "name_asc",
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where_sql, params = self._build_list_where(
            role=role,
            fetch_status=fetch_status,
            query=query,
        )
        rows = await self.db.fetchall(
            "SELECT * FROM repos"
            f"{where_sql} ORDER BY {self._order_sql(sort)}"
            + (" LIMIT ? OFFSET ?" if limit is not None else ""),
            tuple([*params, limit, offset] if limit is not None else params),
        )
        return [dict(r) for r in rows]

    async def list_page(
        self,
        *,
        role: str | None = None,
        fetch_status: str | None = None,
        query: str | None = None,
        sort: str = "updated_desc",
        limit: int = 12,
        offset: int = 0,
    ) -> dict[str, object]:
        where_sql, params = self._build_list_where(
            role=role,
            fetch_status=fetch_status,
            query=query,
        )
        count_row = await self.db.fetchone(
            f"SELECT COUNT(*) AS c FROM repos{where_sql}",
            tuple(params),
        )
        total = int(count_row["c"]) if count_row is not None else 0
        rows = await self.list_all(
            role=role,
            fetch_status=fetch_status,
            query=query,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        return {
            "items": rows,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total,
                "has_more": (offset + limit) < total,
            },
        }

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
        # the Python-side check produces a usable error message. SQL is
        # written out per-table (no f-string identifier interpolation) so
        # this never serves as a copy-paste template for user-supplied table
        # names.
        design_row = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM design_work_repos WHERE repo_id=?",
            (id,),
        )
        if design_row and design_row["c"] > 0:
            raise ConflictError(
                f"repo {id!r} is referenced by {design_row['c']} "
                "design_work_repos rows; remove those references before deleting"
            )
        dev_row = await self.db.fetchone(
            "SELECT COUNT(*) AS c FROM dev_work_repos WHERE repo_id=?",
            (id,),
        )
        if dev_row and dev_row["c"] > 0:
            raise ConflictError(
                f"repo {id!r} is referenced by {dev_row['c']} "
                "dev_work_repos rows; remove those references before deleting"
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
                # ``local_path`` is operator-managed metadata in v1, not
                # config-owned runtime state. Preserve the DB value on sync so
                # a startup reload or /repos/sync does not silently erase it.
                local_path=existing.get("local_path") if existing else None,
                default_branch=r.default_branch,
                ssh_key_path=r.ssh_key_path,
                role=r.role,
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
