"""Workspace file registry — DB-layer repo + FileStore composite writer.

Phase 3 introduces the single write path for every workspace artifact:

    registry.put_markdown(workspace_row=ws, relative_path="designs/foo.md",
                          text=body, kind="design_doc")

The registry is a 2-step composite (FileStore write + DB upsert). It is NOT
transactional across the two steps — Phase 5 will add a proper ``register()``
method with an expected-ETag parameter that wraps local + OSS + DB CAS. Phase
3 callers cannot rely on atomicity across the FS/DB boundary; on a DB failure
after the FS write the registry best-effort deletes the FS file.

Design references:
  * PRD §Write Path & Concurrency Model
  * .claude/PRPs/plans/completed/phase-1-storage-abstraction.plan.md
  * .claude/PRPs/plans/completed/phase-2-db-schema.plan.md
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.exceptions import BadRequestError, NotFoundError
from src.storage.base import FileRef, FileStore, normalize_key

logger = logging.getLogger(__name__)


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
    ) -> dict[str, Any]:
        """Insert-or-update the workspace_files row for a given (ws, path).

        Validates ``kind`` and ``relative_path`` at the boundary so callers get
        targeted ``BadRequestError`` messages instead of opaque IntegrityError
        surfaces. ``oss_key`` / ``oss_etag`` / ``last_synced_at`` stay NULL in
        Phase 3 — Phase 5 fills them.
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
                    "byte_size=?, local_mtime_ns=?, updated_at=? WHERE id=?",
                    (kind, content_hash, byte_size, local_mtime_ns, now,
                     existing["id"]),
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
                     byte_size, None, None, local_mtime_ns, None, created_at,
                     now),
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
            "oss_key": None,
            "oss_etag": None,
            "local_mtime_ns": local_mtime_ns,
            "last_synced_at": None,
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

    Every workspace-internal write site in Phase 3 routes through this
    service. The contract is strictly 2-step: put bytes through the FileStore,
    then upsert the metadata row. On DB failure the FS write is best-effort
    rolled back. Phase 5 replaces this with a true CAS ``register()``.
    """

    def __init__(
        self, *, store: FileStore, repo: WorkspaceFilesRepo
    ) -> None:
        self.store = store
        self.repo = repo

    @staticmethod
    def _compose_key(workspace_slug: str, relative_path: str) -> str:
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

    async def put_bytes(
        self,
        *,
        workspace_row: dict[str, Any],
        relative_path: str,
        data: bytes,
        kind: str,
    ) -> dict[str, Any]:
        """Write bytes and register metadata. Returns the workspace_files row."""
        content_hash = hashlib.sha256(data).hexdigest()
        store_key = self._compose_key(workspace_row["slug"], relative_path)
        ref = await self.store.put_bytes(store_key, data)
        try:
            return await self.repo.upsert(
                workspace_id=workspace_row["id"],
                relative_path=normalize_key(relative_path).as_posix(),
                kind=kind,
                content_hash=content_hash,
                byte_size=ref.size,
                local_mtime_ns=ref.mtime_ns,
            )
        except Exception:
            logger.exception(
                "workspace_files upsert failed; rolling back FS write at %s",
                store_key,
            )
            try:
                await self.store.delete(store_key)
            except OSError:
                logger.warning(
                    "rollback delete failed for %s — leaves FS orphan",
                    store_key,
                )
            raise

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
