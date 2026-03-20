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

async def test_connect_creates_tables(db):
    row = await db.fetchone("SELECT name FROM sqlite_master WHERE type='table' AND name='runs'")
    assert row is not None

async def test_connect_enables_foreign_keys(db):
    row = await db.fetchone("PRAGMA foreign_keys")
    assert row["foreign_keys"] == 1


async def test_connect_sets_busy_timeout(db):
    row = await db.fetchone("PRAGMA busy_timeout")
    assert next(iter(row.values())) == 5000

async def test_insert_and_fetch_run(db):
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r1", "T-1", "/repo", "running", "INIT", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
    )
    run = await db.fetchone("SELECT * FROM runs WHERE id=?", ("r1",))
    assert run["ticket"] == "T-1"

async def test_transaction_rollback(db):
    try:
        async with db.transaction():
            await db.execute(
                "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("r2", "T-2", "/repo", "running", "INIT", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
            )
            raise RuntimeError("test rollback")
    except RuntimeError:
        pass
    row = await db.fetchone("SELECT * FROM runs WHERE id=?", ("r2",))
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
