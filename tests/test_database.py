import asyncio
import sqlite3
from unittest.mock import AsyncMock

import pytest
from src.database import Database


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


async def test_connect_creates_workspace_tables(db):
    row = await db.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='workspaces'"
    )
    assert row is not None


async def test_connect_enables_foreign_keys(db):
    row = await db.fetchone("PRAGMA foreign_keys")
    assert row["foreign_keys"] == 1


async def test_connect_sets_busy_timeout(db):
    row = await db.fetchone("PRAGMA busy_timeout")
    assert next(iter(row.values())) == 5000


async def test_insert_and_fetch_workspace(db):
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("ws-1", "Test", "test", "active", "/tmp/ws-1",
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    ws = await db.fetchone("SELECT * FROM workspaces WHERE id=?", ("ws-1",))
    assert ws["title"] == "Test"


async def test_transaction_rollback(db):
    try:
        async with db.transaction():
            await db.execute(
                "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("ws-2", "T2", "t2", "active", "/tmp/ws-2",
                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            raise RuntimeError("test rollback")
    except RuntimeError:
        pass
    row = await db.fetchone("SELECT * FROM workspaces WHERE id=?", ("ws-2",))
    assert row is None


async def test_retry_locked_operation_retries_until_success(db):
    attempts = 0

    async def flaky_operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    with pytest.MonkeyPatch.context() as mp:
        sleep_mock = AsyncMock()
        mp.setattr(asyncio, "sleep", sleep_mock)
        result = await db._retry_locked_operation(flaky_operation)

    assert result == "ok"
    assert attempts == 2
    sleep_mock.assert_awaited_once()


async def test_no_legacy_tables(db):
    """Phase 1 dropped runs/jobs/events/agent_hosts wholesale; fresh schema must not
    resurrect them."""
    for tbl in ("runs", "jobs", "events", "agent_hosts", "merge_queue", "approvals",
                "steps", "artifacts", "turns"):
        row = await db.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
        )
        assert row is None, f"legacy table {tbl} must not exist in fresh schema"


async def test_no_compat_migration_hooks():
    """Phase 7 deletes the compat-migration scaffolding entirely."""
    for attr in (
        "_apply_compat_migrations",
        "_migrate_events_nullable_run_id",
        "_table_exists",
        "_column_exists",
    ):
        assert not hasattr(Database, attr), f"compat hook {attr} must be deleted"
