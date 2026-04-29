"""
Async SQLite database wrapper using aiosqlite.
"""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

# Project root: two levels up from this file (src/database.py -> src/ -> project root)
ROOT = Path(__file__).resolve().parents[1]


class Database:
    """Async SQLite database wrapper."""

    _BUSY_TIMEOUT_MS = 5000
    _LOCK_RETRY_ATTEMPTS = 3
    _LOCK_RETRY_BASE_DELAY_SEC = 0.1

    def __init__(self, db_path: str | Path, schema_path: str | Path) -> None:
        db = Path(db_path)
        if not db.is_absolute():
            db = ROOT / db
        self._db_path = db
        # Resolve schema_path relative to project root if not absolute
        schema = Path(schema_path)
        if not schema.is_absolute():
            schema = ROOT / schema
        self._schema_path = schema
        self._conn: aiosqlite.Connection | None = None
        # Track whether we are inside a manual transaction block
        self._in_transaction: bool = False

    async def connect(self) -> None:
        """Open the aiosqlite connection, apply schema, and enable WAL mode."""
        self._conn = await aiosqlite.connect(self._db_path, timeout=self._BUSY_TIMEOUT_MS / 1000)
        # Return rows as sqlite3.Row so they support both index and key access
        self._conn.row_factory = sqlite3.Row
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute(f"PRAGMA busy_timeout={self._BUSY_TIMEOUT_MS}")
        # Apply schema (idempotent via CREATE IF NOT EXISTS)
        schema_sql = self._schema_path.read_text(encoding="utf-8")
        await self._conn.executescript(schema_sql)
        # Run idempotent in-place migrations for schema reshapes that
        # ``CREATE TABLE IF NOT EXISTS`` cannot apply to existing tables.
        await self._migrate()
        # Enable WAL journal mode for better concurrency
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Apply forward-only migrations to existing tables.

        ``CREATE TABLE IF NOT EXISTS`` is a no-op on existing tables, so
        any column rename / drop / CHECK change in ``db/schema.sql`` must
        be re-applied here for environments that ran an earlier schema.

        Each step is idempotent (checks ``PRAGMA table_info`` first) so
        starting on a fresh DB or repeated startups are both safe.
        """
        conn = self._ensure_connected()

        # repo-registry phase 3: collapse ``credential_ref`` → ``ssh_key_path``,
        # drop ``vendor`` and ``labels_json``, normalize legacy ``stale`` rows.
        async with conn.execute("PRAGMA table_info(repos)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if "credential_ref" in cols and "ssh_key_path" not in cols:
            await conn.execute(
                "ALTER TABLE repos RENAME COLUMN credential_ref TO ssh_key_path"
            )
        if "vendor" in cols:
            await conn.execute("ALTER TABLE repos DROP COLUMN vendor")
        if "labels_json" in cols:
            await conn.execute("ALTER TABLE repos DROP COLUMN labels_json")
        # The CHECK constraint that previously permitted ``'stale'`` lives
        # on the existing table definition; rewriting it requires a full
        # table rebuild. Instead, normalize any legacy 'stale' rows to
        # 'unknown' — the health loop will re-evaluate on its next tick.
        await conn.execute(
            "UPDATE repos SET fetch_status='unknown' WHERE fetch_status='stale'"
        )

        # devwork-acpx phase 3: add ``current_progress_json`` to dev_works.
        # Idempotent: PRAGMA gate before ALTER. Existing rows stay NULL — no
        # backfill, the heartbeat writer populates the column lazily.
        async with conn.execute("PRAGMA table_info(dev_works)") as cur:
            dw_cols = {row[1] for row in await cur.fetchall()}
        if "current_progress_json" not in dw_cols:
            await conn.execute(
                "ALTER TABLE dev_works ADD COLUMN current_progress_json TEXT"
            )

        # devwork-acpx phase 9: add ``session_anchor_path`` to dev_works.
        # Idempotent: PRAGMA gate before ALTER. Existing rows stay NULL —
        # _s0_init backfills the column on the next tick for in-flight DevWorks.
        if "session_anchor_path" not in dw_cols:
            await conn.execute(
                "ALTER TABLE dev_works ADD COLUMN session_anchor_path TEXT"
            )

        # devwork-acpx phase 6: add per-mount ``worktree_path`` to
        # dev_work_repos. Idempotent: PRAGMA gate before ALTER. Existing
        # rows stay NULL — _s0_init backfills the primary row's path on
        # the next tick for in-flight DevWorks; non-primary in-flight rows
        # remain NULL (mount table renders the legacy placeholder).
        async with conn.execute("PRAGMA table_info(dev_work_repos)") as cur:
            dwr_cols = {row[1] for row in await cur.fetchall()}
        if "worktree_path" not in dwr_cols:
            await conn.execute(
                "ALTER TABLE dev_work_repos ADD COLUMN worktree_path TEXT"
            )

        # devwork-acpx phase 4: extend workspace_files.kind CHECK to permit
        # 'feedback'. SQLite cannot ALTER a CHECK constraint in place, so we
        # rebuild the table when the existing definition pre-dates Phase 4.
        async with conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='workspace_files'"
        ) as cur:
            row = await cur.fetchone()
        wf_sql = row[0] if row else ""
        if wf_sql and "'feedback'" not in wf_sql:
            # Wrap the rebuild in an explicit transaction so a crash mid-script
            # cannot leave ``workspace_files_new`` orphaned. The leading
            # ``DROP IF EXISTS`` further protects against retry after an
            # interrupted earlier attempt that did commit the CREATE.
            await conn.executescript(
                """
                BEGIN;
                DROP TABLE IF EXISTS workspace_files_new;
                CREATE TABLE workspace_files_new (
                  id                TEXT PRIMARY KEY,
                  workspace_id      TEXT NOT NULL REFERENCES workspaces(id),
                  relative_path     TEXT NOT NULL,
                  kind              TEXT NOT NULL CHECK(kind IN (
                                        'design_doc','design_input','iteration_note',
                                        'prompt','image','workspace_md',
                                        'context','artifact','feedback','other')),
                  content_hash      TEXT,
                  byte_size         INTEGER,
                  local_mtime_ns    INTEGER,
                  created_at        TEXT NOT NULL,
                  updated_at        TEXT NOT NULL,
                  UNIQUE(workspace_id, relative_path)
                );
                INSERT INTO workspace_files_new
                  SELECT id, workspace_id, relative_path, kind, content_hash,
                         byte_size, local_mtime_ns, created_at, updated_at
                  FROM workspace_files;
                DROP TABLE workspace_files;
                ALTER TABLE workspace_files_new RENAME TO workspace_files;
                CREATE INDEX IF NOT EXISTS idx_workspace_files_workspace
                  ON workspace_files(workspace_id);
                CREATE INDEX IF NOT EXISTS idx_workspace_files_kind
                  ON workspace_files(kind);
                COMMIT;
                """
            )

        await conn.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _ensure_connected(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._conn

    def _is_locked_error(self, exc: sqlite3.OperationalError) -> bool:
        message = str(exc).lower()
        return "database is locked" in message or "database table is locked" in message

    async def _retry_locked_operation(self, operation):
        attempts = 1 if self._in_transaction else self._LOCK_RETRY_ATTEMPTS
        for attempt in range(attempts):
            try:
                return await operation()
            except sqlite3.OperationalError as exc:
                if attempt == attempts - 1 or not self._is_locked_error(exc):
                    raise
                await asyncio.sleep(self._LOCK_RETRY_BASE_DELAY_SEC * (2 ** attempt))

    async def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int | None:
        """Execute a write statement and return lastrowid.

        When called inside a ``transaction()`` context manager the change is
        NOT committed immediately — the caller is responsible for the commit/
        rollback at the end of the transaction block.
        """
        conn = self._ensure_connected()

        async def _execute_once():
            try:
                async with conn.execute(sql, params or ()) as cursor:
                    lastrowid = cursor.lastrowid
                # Auto-commit only when we are NOT inside an explicit transaction block.
                if not self._in_transaction:
                    await conn.commit()
                return lastrowid
            except sqlite3.OperationalError:
                if not self._in_transaction:
                    await conn.rollback()
                raise

        return await self._retry_locked_operation(_execute_once)

    async def execute_rowcount(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        """Execute a write statement and return ``cursor.rowcount``.

        Use this for compare-and-swap style updates where the caller needs to
        know whether the WHERE clause matched a row.
        """
        conn = self._ensure_connected()

        async def _execute_once() -> int:
            try:
                async with conn.execute(sql, params or ()) as cursor:
                    rowcount = cursor.rowcount
                if not self._in_transaction:
                    await conn.commit()
                return rowcount
            except sqlite3.OperationalError:
                if not self._in_transaction:
                    await conn.rollback()
                raise

        return await self._retry_locked_operation(_execute_once)

    async def fetchone(self, sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
        """Execute a query and return the first row as a dict, or None."""
        conn = self._ensure_connected()

        async def _fetchone_once():
            async with conn.execute(sql, params or ()) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                return dict(row)

        return await self._retry_locked_operation(_fetchone_once)

    async def fetchall(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        """Execute a query and return all rows as a list of dicts."""
        conn = self._ensure_connected()

        async def _fetchall_once():
            async with conn.execute(sql, params or ()) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

        return await self._retry_locked_operation(_fetchall_once)

    @asynccontextmanager
    async def transaction(self):
        """Async context manager that commits on success or rolls back on exception."""
        conn = self._ensure_connected()
        self._in_transaction = True
        await conn.execute("BEGIN")
        try:
            yield self
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise
        finally:
            self._in_transaction = False
