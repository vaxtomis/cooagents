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

    def __init__(self, db_path: str | Path, schema_path: str | Path, on_trace_event=None) -> None:
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
        self._on_trace_event = on_trace_event

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
        await self._apply_compat_migrations()
        # Enable WAL journal mode for better concurrency
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.commit()

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _ensure_connected(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._conn

    async def _column_exists(self, table: str, column: str) -> bool:
        conn = self._ensure_connected()
        async with conn.execute(f"PRAGMA table_info({table})") as cursor:
            rows = await cursor.fetchall()
        return any(row["name"] == column for row in rows)

    async def _table_exists(self, table: str) -> bool:
        conn = self._ensure_connected()
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ) as cursor:
            row = await cursor.fetchone()
        return row is not None

    async def _apply_compat_migrations(self) -> None:
        # Phase 1 workspace-driven refactor dropped the legacy tables (runs,
        # events, jobs, ...). The compat migrations below only apply when those
        # legacy tables are still present; skip gracefully when the schema has
        # been rebuilt for the new workspace model.
        conn = self._ensure_connected()
        if await self._table_exists("jobs"):
            if not await self._column_exists("jobs", "timeout_sec"):
                await conn.execute("ALTER TABLE jobs ADD COLUMN timeout_sec INTEGER")
            if not await self._column_exists("jobs", "running_started_at"):
                await conn.execute("ALTER TABLE jobs ADD COLUMN running_started_at TEXT")

        if await self._table_exists("events"):
            trace_cols = {
                "trace_id": "TEXT",
                "job_id": "TEXT",
                "span_type": "TEXT DEFAULT 'system'",
                "level": "TEXT DEFAULT 'info'",
                "duration_ms": "INTEGER",
                "error_detail": "TEXT",
                "source": "TEXT",
            }
            for col, col_type in trace_cols.items():
                if not await self._column_exists("events", col):
                    await conn.execute(f"ALTER TABLE events ADD COLUMN {col} {col_type}")

            async with conn.execute("PRAGMA table_info(events)") as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                if row["name"] == "run_id" and row["notnull"] == 1:
                    await self._migrate_events_nullable_run_id(conn)
                    break

            await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_span ON events(span_type)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_level ON events(level)")

        if await self._table_exists("runs"):
            if not await self._column_exists("runs", "design_agent"):
                await conn.execute("ALTER TABLE runs ADD COLUMN design_agent TEXT DEFAULT 'claude'")
            if not await self._column_exists("runs", "dev_agent"):
                await conn.execute("ALTER TABLE runs ADD COLUMN dev_agent TEXT DEFAULT 'claude'")

        # Phase 3 (U7): design_works gains 5 runtime columns. Keep these
        # nullable in both schema.sql and the ALTER path — SQLite cannot add
        # a NOT NULL column to an existing table without a default, and a
        # default would lie about rows created before Phase 3.
        if await self._table_exists("design_works"):
            for col, col_type in (
                ("title", "TEXT"),
                ("sub_slug", "TEXT"),
                ("version", "TEXT"),
                ("output_path", "TEXT"),
                ("gates_json", "TEXT"),
            ):
                if not await self._column_exists("design_works", col):
                    await conn.execute(
                        f"ALTER TABLE design_works ADD COLUMN {col} {col_type}"
                    )

    async def _migrate_events_nullable_run_id(self, conn) -> None:
        """Rebuild events table to make run_id nullable."""
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS events_new (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       TEXT REFERENCES runs(id),
                event_type   TEXT NOT NULL,
                payload_json TEXT,
                created_at   TEXT NOT NULL,
                trace_id     TEXT,
                job_id       TEXT,
                span_type    TEXT DEFAULT 'system',
                level        TEXT DEFAULT 'info',
                duration_ms  INTEGER,
                error_detail TEXT,
                source       TEXT
            )
        """)
        existing_cols = ["id", "run_id", "event_type", "payload_json", "created_at"]
        for col in ["trace_id", "job_id", "span_type", "level", "duration_ms", "error_detail", "source"]:
            if await self._column_exists("events", col):
                existing_cols.append(col)
        cols = ", ".join(existing_cols)
        await conn.execute(f"INSERT INTO events_new ({cols}) SELECT {cols} FROM events")
        await conn.execute("DROP TABLE events")
        await conn.execute("ALTER TABLE events_new RENAME TO events")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id)")

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
                if self._on_trace_event:
                    self._on_trace_event(
                        "db.lock_retry",
                        {"attempt": attempt + 1, "max_attempts": attempts},
                        "warning",
                        str(exc),
                    )
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
