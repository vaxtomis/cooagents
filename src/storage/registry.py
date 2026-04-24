"""Workspace file registry — DB-layer repo + FileStore composite writer.

Phase 5 upgrades the Phase 3 2-step helper into a three-step CAS protocol:

    registry.register(workspace_row=ws, relative_path="designs/foo.md",
                      data=body, kind="design_doc", expected_etag=None)

Steps:
  1. Local atomic write via ``store.put_bytes`` (LocalFileStore.put_bytes is
     already atomic via temp-then-rename).
  2. OSS conditional PUT via ``store.put_bytes_conditional`` (only when the
     backend supports CAS; detected by duck-typing on the method).
  3. DB CAS UPDATE matching the caller's ``expected_etag`` (or INSERT for
     a brand-new row).

On step-2 EtagMismatch the exception propagates to the caller (typically
``regenerate_workspace_md``'s retry loop). Local FS is now ahead of OSS —
the boot-time recovery scan heals that on next startup.

Phase 3 callers (``put_bytes`` / ``put_markdown`` / ``put_json``) keep
their signatures: they delegate to ``register`` with a sentinel
``expected_etag=_SENTINEL`` which means "read the current etag from the DB
row (if any) and use that as the CAS token".

Design references:
  * PRD §Write Path & Concurrency Model
  * .claude/PRPs/plans/phase-5-registry-sync-protocol.plan.md
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.exceptions import BadRequestError, NotFoundError
from src.storage.base import EtagMismatch, FileRef, FileStore, normalize_key

logger = logging.getLogger(__name__)


# Sentinel "no explicit value passed" for the tri-valued expected_etag arg.
# Using a module-level object keeps it identity-comparable across callers.
_SENTINEL: Any = object()


# Must stay in lockstep with db/schema.sql `workspace_files.kind` CHECK clause.
# A parity test in tests/test_workspace_files_repo.py enforces equality with
# the SQL literals via regex.
_VALID_KINDS: frozenset[str] = frozenset({
    "design_doc",
    "design_input",
    "iteration_note",
    "prompt",
    "image",
    "workspace_md",
    "context",
    "artifact",
    "other",
})


def _is_integrity_error(exc: BaseException) -> bool:
    """Duck-type detection of UNIQUE-constraint violations across DB drivers.

    Avoids importing sqlite3 / aiosqlite here; any exception class whose
    qualified name contains 'IntegrityError' counts. Matches sqlite3,
    aiosqlite, and most DB-API 2.0 backends.
    """
    for cls in type(exc).__mro__:
        if cls.__name__ == "IntegrityError":
            return True
    return False


def _supports_conditional(store: FileStore) -> bool:
    """True iff the backend can CAS via put_bytes_conditional.

    LocalFileStore deliberately does NOT implement it — the local backend has
    no ETag concept. OSSFileStore does. Avoids importing OSSFileStore here so
    the registry module stays pure-Python with no SDK dependency when OSS is
    off.
    """
    return hasattr(store, "put_bytes_conditional")


class WorkspaceFilesRepo:
    """DB-layer CRUD wrapper for ``workspace_files``.

    The repo is intentionally unaware of the FileStore — it only manipulates
    rows. ``WorkspaceFileRegistry`` is the composite service that combines a
    FileStore write with an ``upsert`` call here.
    """

    # Exposed as class attribute for the parity test and for callers that
    # want to pre-validate kind before constructing a registry.
    _VALID_KINDS: frozenset[str] = _VALID_KINDS

    def __init__(self, db: Any) -> None:
        self.db = db

    @staticmethod
    def _new_id() -> str:
        return f"wf-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def upsert(
        self,
        *,
        workspace_id: str,
        relative_path: str,
        kind: str,
        content_hash: str,
        byte_size: int,
        local_mtime_ns: int,
        oss_key: str | None = None,
        oss_etag: str | None = None,
        last_synced_at: str | None = None,
    ) -> dict[str, Any]:
        """Insert-or-update the workspace_files row for a given (ws, path).

        Validates ``kind`` and ``relative_path`` at the boundary so callers get
        targeted ``BadRequestError`` messages instead of opaque IntegrityError
        surfaces. ``oss_key`` / ``oss_etag`` / ``last_synced_at`` are Phase-5
        additions; Phase 3 callers that omit them keep NULL semantics.
        """
        if kind not in _VALID_KINDS:
            raise BadRequestError(
                f"invalid workspace_files.kind={kind!r}; "
                f"expected one of {sorted(_VALID_KINDS)}"
            )
        # normalize_key raises BadRequestError on absolute / backslash / drive
        # letter / '..' segments — identical guard to the FileStore side.
        rel_norm = normalize_key(relative_path).as_posix()

        now = self._now()
        async with self.db.transaction():
            existing = await self.db.fetchone(
                "SELECT id, created_at FROM workspace_files "
                "WHERE workspace_id=? AND relative_path=?",
                (workspace_id, rel_norm),
            )
            if existing:
                await self.db.execute(
                    "UPDATE workspace_files SET kind=?, content_hash=?, "
                    "byte_size=?, oss_key=?, oss_etag=?, local_mtime_ns=?, "
                    "last_synced_at=?, updated_at=? WHERE id=?",
                    (
                        kind, content_hash, byte_size, oss_key, oss_etag,
                        local_mtime_ns, last_synced_at, now, existing["id"],
                    ),
                )
                created_at = existing["created_at"]
                wf_id = existing["id"]
            else:
                wf_id = self._new_id()
                created_at = now
                await self.db.execute(
                    "INSERT INTO workspace_files(id, workspace_id, "
                    "relative_path, kind, content_hash, byte_size, oss_key, "
                    "oss_etag, local_mtime_ns, last_synced_at, created_at, "
                    "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (wf_id, workspace_id, rel_norm, kind, content_hash,
                     byte_size, oss_key, oss_etag, local_mtime_ns,
                     last_synced_at, created_at, now),
                )
        logger.debug(
            "workspace_files upsert: ws=%s path=%s kind=%s size=%d",
            workspace_id, rel_norm, kind, byte_size,
        )
        return {
            "id": wf_id,
            "workspace_id": workspace_id,
            "relative_path": rel_norm,
            "kind": kind,
            "content_hash": content_hash,
            "byte_size": byte_size,
            "oss_key": oss_key,
            "oss_etag": oss_etag,
            "local_mtime_ns": local_mtime_ns,
            "last_synced_at": last_synced_at,
            "created_at": created_at,
            "updated_at": now,
        }

    async def get(
        self, workspace_id: str, relative_path: str
    ) -> dict[str, Any] | None:
        return await self.db.fetchone(
            "SELECT * FROM workspace_files "
            "WHERE workspace_id=? AND relative_path=?",
            (workspace_id, relative_path),
        )

    async def list_for_workspace(
        self, workspace_id: str
    ) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            "SELECT * FROM workspace_files WHERE workspace_id=? "
            "ORDER BY relative_path",
            (workspace_id,),
        )

    async def delete(self, workspace_id: str, relative_path: str) -> None:
        """Idempotent — deleting a missing row is a silent no-op."""
        await self.db.execute(
            "DELETE FROM workspace_files "
            "WHERE workspace_id=? AND relative_path=?",
            (workspace_id, relative_path),
        )


class WorkspaceFileRegistry:
    """FileStore + WorkspaceFilesRepo composite writer/reader.

    Every workspace-internal write site routes through this service. Phase 5
    upgrades the 2-step composite into a three-step CAS protocol via
    ``register()``. Phase 3 callers keep working via the ``put_bytes`` /
    ``put_markdown`` / ``put_json`` delegates.

    Atomicity note: ``register()`` does NOT roll back the local write on a
    subsequent DB CAS failure. The protocol trusts the boot-time recovery
    scan to heal such inconsistencies. The legacy ``put_bytes`` rollback
    semantics are removed (mid-write crash now leaves a local file that the
    recovery scan re-registers on next boot).
    """

    def __init__(
        self, *, store: FileStore, repo: WorkspaceFilesRepo
    ) -> None:
        self.store = store
        self.repo = repo

    @staticmethod
    def compose_key(workspace_slug: str, relative_path: str) -> str:
        """Join ``<slug>/<relative_path>`` for FileStore consumption."""
        if not isinstance(workspace_slug, str) or not workspace_slug:
            raise BadRequestError(
                f"workspace_slug must be non-empty str, got {workspace_slug!r}"
            )
        if "/" in workspace_slug or "\\" in workspace_slug:
            raise BadRequestError(
                f"workspace_slug must not contain path separators: "
                f"{workspace_slug!r}"
            )
        norm = normalize_key(relative_path)
        return f"{workspace_slug}/{norm.as_posix()}"

    # Backward-compat alias; callers may still reach for the underscore name.
    _compose_key = compose_key

    async def register(
        self,
        *,
        workspace_row: dict[str, Any],
        relative_path: str,
        data: bytes,
        kind: str,
        expected_etag: Any = _SENTINEL,
    ) -> dict[str, Any]:
        """Three-step CAS write: local atomic → OSS conditional → DB CAS.

        ``expected_etag`` is tri-valued:
          * ``_SENTINEL`` (the default): read current etag from DB and use
            it. Phase 3 callers (``put_bytes``, ``put_markdown``,
            ``put_json``) rely on this — they don't know about CAS.
          * ``None``: first-create contract — use ``If-None-Match: *`` on
            OSS; DB CAS ``WHERE oss_etag IS NULL``.
          * any string: conditional overwrite — use ``If-Match=<etag>`` on
            OSS; DB CAS ``WHERE oss_etag=<etag>``.

        Raises :class:`EtagMismatch` if either the OSS conditional PUT or
        the DB CAS UPDATE fails its precondition.
        """
        if kind not in _VALID_KINDS:
            raise BadRequestError(
                f"invalid workspace_files.kind={kind!r}; "
                f"expected one of {sorted(_VALID_KINDS)}"
            )
        content_hash = hashlib.sha256(data).hexdigest()
        rel_norm = normalize_key(relative_path).as_posix()
        store_key = self._compose_key(workspace_row["slug"], relative_path)

        # Resolve expected_etag from DB when caller did not pass one
        # explicitly. Phase 3 semantics: whatever etag is currently on the
        # row (may be None if never flushed) becomes the precondition.
        if expected_etag is _SENTINEL:
            existing = await self.repo.get(workspace_row["id"], rel_norm)
            expected_etag = existing["oss_etag"] if existing else None

        # Step 1 + 2: write bytes. With CAS-capable backends, a single
        # conditional PUT covers both the atomic-write invariant AND the
        # ETag precondition. With LocalFileStore, ``put_bytes`` is itself
        # atomic (temp-then-rename) and there is no ETag concept, so the
        # conditional branch is skipped.
        oss_etag_new: str | None = None
        oss_key: str | None = None
        last_synced_at: str | None = None
        if _supports_conditional(self.store):
            if expected_etag is None:
                oss_ref = await self.store.put_bytes_conditional(
                    store_key, data, if_none_match="*",
                )
            else:
                oss_ref = await self.store.put_bytes_conditional(
                    store_key, data, if_match=expected_etag,
                )
            ref = oss_ref
            oss_etag_new = oss_ref.etag
            oss_key = store_key
            last_synced_at = datetime.now(timezone.utc).isoformat()
        else:
            ref = await self.store.put_bytes(store_key, data)

        # Step 3: DB CAS UPDATE (or INSERT if row doesn't yet exist).
        updated = await self._cas_upsert(
            workspace_row=workspace_row,
            relative_path=rel_norm,
            kind=kind,
            content_hash=content_hash,
            byte_size=ref.size,
            local_mtime_ns=ref.mtime_ns,
            oss_key=oss_key,
            oss_etag_new=oss_etag_new,
            oss_etag_expected=expected_etag,
            last_synced_at=last_synced_at,
        )
        if updated is None:
            raise EtagMismatch(
                f"DB etag changed under us for {rel_norm!r} "
                f"(expected={expected_etag!r})"
            )
        return updated

    async def _cas_upsert(
        self,
        *,
        workspace_row: dict[str, Any],
        relative_path: str,
        kind: str,
        content_hash: str,
        byte_size: int,
        local_mtime_ns: int,
        oss_key: str | None,
        oss_etag_new: str | None,
        oss_etag_expected: Any,
        last_synced_at: str | None,
    ) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        existing = await self.repo.get(workspace_row["id"], relative_path)
        if existing is None:
            # INSERT path — no CAS needed; UNIQUE(workspace_id, relative_path)
            # surfaces the rare concurrent-insert race. Translate to
            # EtagMismatch so the retry loop in regenerate_workspace_md
            # covers it instead of escaping as a raw IntegrityError.
            try:
                return await self.repo.upsert(
                    workspace_id=workspace_row["id"],
                    relative_path=relative_path,
                    kind=kind,
                    content_hash=content_hash,
                    byte_size=byte_size,
                    local_mtime_ns=local_mtime_ns,
                    oss_key=oss_key,
                    oss_etag=oss_etag_new,
                    last_synced_at=last_synced_at,
                )
            except Exception as exc:
                if _is_integrity_error(exc):
                    return None  # signals EtagMismatch to caller
                raise

        # UPDATE path — CAS on (workspace_id, relative_path, oss_etag).
        # NULL is never equal to NULL in SQL, so branch the WHERE clause.
        if oss_etag_expected is None:
            where_clause = (
                "WHERE workspace_id=? AND relative_path=? AND oss_etag IS NULL"
            )
            params_tail: tuple[Any, ...] = (
                workspace_row["id"], relative_path,
            )
        else:
            where_clause = (
                "WHERE workspace_id=? AND relative_path=? AND oss_etag=?"
            )
            params_tail = (
                workspace_row["id"], relative_path, oss_etag_expected,
            )

        rc = await self.repo.db.execute_rowcount(
            "UPDATE workspace_files SET kind=?, content_hash=?, byte_size=?, "
            "oss_key=?, oss_etag=?, local_mtime_ns=?, last_synced_at=?, "
            f"updated_at=? {where_clause}",
            (
                kind, content_hash, byte_size, oss_key, oss_etag_new,
                local_mtime_ns, last_synced_at, now, *params_tail,
            ),
        )
        if rc == 0:
            return None  # signals EtagMismatch to caller
        return await self.repo.get(workspace_row["id"], relative_path)

    async def put_bytes(
        self,
        *,
        workspace_row: dict[str, Any],
        relative_path: str,
        data: bytes,
        kind: str,
    ) -> dict[str, Any]:
        """Write bytes and register metadata. Returns the workspace_files row.

        Phase 3 signature preserved: delegates to ``register`` with the
        sentinel ``expected_etag`` meaning "use the current DB etag (or
        None) as the CAS precondition".
        """
        return await self.register(
            workspace_row=workspace_row,
            relative_path=relative_path,
            data=data,
            kind=kind,
            expected_etag=_SENTINEL,
        )

    async def put_markdown(
        self,
        *,
        workspace_row: dict[str, Any],
        relative_path: str,
        text: str,
        kind: str,
    ) -> dict[str, Any]:
        """Sugar over ``put_bytes`` that encodes UTF-8 once.

        Preserves the bytes-write invariant: the hash is computed on the exact
        bytes written to disk, avoiding Windows CRLF translation drift.
        """
        encoded = text.encode("utf-8")
        return await self.put_bytes(
            workspace_row=workspace_row,
            relative_path=relative_path,
            data=encoded,
            kind=kind,
        )

    async def put_json(
        self,
        *,
        workspace_row: dict[str, Any],
        relative_path: str,
        payload: dict | list,
        kind: str,
    ) -> dict[str, Any]:
        """Serialise a Python object as deterministic UTF-8 JSON and register."""
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode(
            "utf-8"
        )
        return await self.put_bytes(
            workspace_row=workspace_row,
            relative_path=relative_path,
            data=encoded,
            kind=kind,
        )

    async def read_bytes(
        self, *, workspace_slug: str, relative_path: str
    ) -> bytes:
        store_key = self._compose_key(workspace_slug, relative_path)
        return await self.store.get_bytes(store_key)

    async def read_text(
        self, *, workspace_slug: str, relative_path: str
    ) -> str:
        data = await self.read_bytes(
            workspace_slug=workspace_slug, relative_path=relative_path
        )
        return data.decode("utf-8")

    async def stat(
        self, *, workspace_slug: str, relative_path: str
    ) -> FileRef | None:
        store_key = self._compose_key(workspace_slug, relative_path)
        return await self.store.stat(store_key)

    async def delete(
        self, *, workspace_row: dict[str, Any], relative_path: str
    ) -> None:
        """Delete FS then DB. On crash between, DB orphan is Phase-5 sweepable."""
        store_key = self._compose_key(workspace_row["slug"], relative_path)
        await self.store.delete(store_key)
        await self.repo.delete(
            workspace_row["id"], normalize_key(relative_path).as_posix()
        )

    async def index_existing(
        self,
        *,
        workspace_row: dict[str, Any],
        relative_path: str,
        kind: str,
    ) -> dict[str, Any]:
        """Register an already-on-disk file: read, hash, stat, then upsert.

        Used by (a) ``WorkspaceManager.create_with_scaffold`` where the FS write
        must precede the ``workspaces`` row (FK constraint), (b) LLM-produced
        outputs that the LLM writes directly to an absolute path under
        ``<workspaces_root>/<slug>/`` (Step3 context, Step4/5 artifacts), and
        (c) Phase 5's ``startup_recovery_scan``.
        """
        store_key = self._compose_key(workspace_row["slug"], relative_path)
        ref = await self.store.stat(store_key)
        if ref is None:
            raise NotFoundError(f"index_existing: file not found at {store_key!r}")
        data = await self.store.get_bytes(store_key)
        content_hash = hashlib.sha256(data).hexdigest()
        return await self.repo.upsert(
            workspace_id=workspace_row["id"],
            relative_path=normalize_key(relative_path).as_posix(),
            kind=kind,
            content_hash=content_hash,
            byte_size=ref.size,
            local_mtime_ns=ref.mtime_ns,
        )
