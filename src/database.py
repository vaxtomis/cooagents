"""
Async SQLite database wrapper using aiosqlite.
"""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

# Project root: two levels up from this file (src/database.py -> src/ -> project root)
ROOT = Path(__file__).resolve().parents[1]


class Database:
    """Async SQLite database wrapper."""

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
        self._conn = await aiosqlite.connect(self._db_path)
        # Return rows as sqlite3.Row so they support both index and key access
        self._conn.row_factory = sqlite3.Row
        await self._conn.execute("PRAGMA foreign_keys=ON")
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

    async def _apply_compat_migrations(self) -> None:
        conn = self._ensure_connected()
        if not await self._column_exists("jobs", "timeout_sec"):
            await conn.execute("ALTER TABLE jobs ADD COLUMN timeout_sec INTEGER")
        if not await self._column_exists("jobs", "running_started_at"):
            await conn.execute("ALTER TABLE jobs ADD COLUMN running_started_at TEXT")

    async def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int | None:
        """Execute a write statement and return lastrowid.

        When called inside a ``transaction()`` context manager the change is
        NOT committed immediately — the caller is responsible for the commit/
        rollback at the end of the transaction block.
        """
        conn = self._ensure_connected()
        async with conn.execute(sql, params or ()) as cursor:
            lastrowid = cursor.lastrowid
        # Auto-commit only when we are NOT inside an explicit transaction block.
        if not self._in_transaction:
            await conn.commit()
        return lastrowid

    async def fetchone(self, sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
        """Execute a query and return the first row as a dict, or None."""
        conn = self._ensure_connected()
        async with conn.execute(sql, params or ()) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return dict(row)

    async def fetchall(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        """Execute a query and return all rows as a list of dicts."""
        conn = self._ensure_connected()
        async with conn.execute(sql, params or ()) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

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
