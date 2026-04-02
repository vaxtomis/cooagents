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


async def test_events_table_has_trace_columns(db):
    """After connect, events table should have tracing columns."""
    row = await db.fetchone(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='events'"
    )
    sql = row["sql"]
    assert "trace_id" in sql
    assert "job_id" in sql
    assert "span_type" in sql
    assert "level" in sql
    assert "duration_ms" in sql
    assert "error_detail" in sql
    assert "source" in sql


async def test_events_run_id_nullable(db):
    """run_id should be nullable for request-level events."""
    await db.execute(
        "INSERT INTO events(run_id,event_type,payload_json,created_at,trace_id,span_type,level,source) "
        "VALUES(NULL,'request.received',NULL,datetime('now'),'abc123','request','info','middleware')"
    )
    row = await db.fetchone("SELECT * FROM events WHERE trace_id='abc123'")
    assert row is not None
    assert row["run_id"] is None


async def test_database_on_trace_event_callback(db):
    """Database should accept and call on_trace_event callback."""
    calls = []
    def on_event(event_type, payload, level, error_detail):
        calls.append((event_type, payload, level, error_detail))

    db2 = Database(db_path=db._db_path, schema_path="db/schema.sql", on_trace_event=on_event)
    await db2.connect()
    assert db2._on_trace_event is on_event
    await db2.close()


async def test_runs_has_agent_columns(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    async with d._ensure_connected().execute("PRAGMA table_info(runs)") as cursor:
        rows = await cursor.fetchall()
    cols = [r["name"] for r in rows]
    assert "design_agent" in cols
    assert "dev_agent" in cols
    await d.close()
