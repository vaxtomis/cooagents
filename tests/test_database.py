import asyncio
import json
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
    """Phase 1 dropped runs/jobs/events wholesale; fresh schema must not
    resurrect them.

    ``agent_hosts`` was reintroduced in Phase 8a with a different shape and
    is no longer considered a 'legacy' name.
    """
    for tbl in ("runs", "jobs", "events", "merge_queue", "approvals",
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


async def test_migrate_renames_legacy_repos_columns(tmp_path):
    """Phase 3 collapses ``credential_ref`` → ``ssh_key_path`` and drops
    ``vendor`` / ``labels_json``. Existing DBs created on the old schema
    must be migrated forward on the next ``connect()`` so deployed envs
    don't break against the new code.
    """
    db_path = tmp_path / "legacy.db"
    # Seed a legacy ``repos`` table directly (mimics a Phase 1 / 2 DB).
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        "CREATE TABLE repos ("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, url TEXT NOT NULL, "
        "vendor TEXT, default_branch TEXT NOT NULL DEFAULT 'main', "
        "credential_ref TEXT, bare_clone_path TEXT, "
        "labels_json TEXT NOT NULL DEFAULT '[]', "
        "fetch_status TEXT NOT NULL DEFAULT 'unknown' "
        "CHECK(fetch_status IN ('unknown','healthy','stale','error')), "
        "last_fetched_at TEXT, last_fetch_err TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO repos(id,name,url,vendor,credential_ref,labels_json,"
        "fetch_status,created_at,updated_at) "
        "VALUES('repo-old','old','git@example:o/r.git','github',"
        "'/keys/id_rsa','[\"a\"]','stale','2026-04-26','2026-04-26')"
    )
    legacy.commit()
    legacy.close()

    # Connect with current schema — migration runs idempotently.
    d = Database(db_path=db_path, schema_path="db/schema.sql")
    await d.connect()
    try:
        cols = {
            r["name"] for r in await d.fetchall("PRAGMA table_info(repos)")
        }
        assert "credential_ref" not in cols
        assert "ssh_key_path" in cols
        assert "local_path" in cols
        assert "vendor" not in cols
        assert "labels_json" not in cols
        # Legacy 'stale' rows are normalized to 'unknown'.
        row = await d.fetchone(
            "SELECT ssh_key_path, fetch_status FROM repos WHERE id=?",
            ("repo-old",),
        )
        assert row["ssh_key_path"] == "/keys/id_rsa"
        assert row["fetch_status"] == "unknown"
    finally:
        await d.close()


async def test_migrate_adds_repos_local_path_unique_index(tmp_path):
    db_path = tmp_path / "legacy-local-path.db"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        "CREATE TABLE repos ("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, url TEXT NOT NULL, "
        "default_branch TEXT NOT NULL DEFAULT 'main', ssh_key_path TEXT, "
        "bare_clone_path TEXT, role TEXT NOT NULL DEFAULT 'other', "
        "fetch_status TEXT NOT NULL DEFAULT 'unknown', "
        "last_fetched_at TEXT, last_fetch_err TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO repos(id,name,url,created_at,updated_at) "
        "VALUES('repo-old','old','git@example:o/r.git','2026-04-26',"
        "'2026-04-26')"
    )
    legacy.commit()
    legacy.close()

    d = Database(db_path=db_path, schema_path="db/schema.sql")
    await d.connect()
    try:
        cols = {
            r["name"] for r in await d.fetchall("PRAGMA table_info(repos)")
        }
        assert "local_path" in cols
        indexes = await d.fetchall("PRAGMA index_list(repos)")
        assert any(
            r["name"] == "uniq_repos_local_path" and r["unique"]
            for r in indexes
        )
    finally:
        await d.close()


async def test_migrate_adds_dev_work_recommended_tech_stack(tmp_path):
    db_path = tmp_path / "legacy-devwork-stack.db"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        "CREATE TABLE dev_works ("
        "id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, "
        "design_doc_id TEXT NOT NULL, prompt TEXT NOT NULL, "
        "current_step TEXT NOT NULL DEFAULT 'INIT', "
        "iteration_rounds INTEGER NOT NULL DEFAULT 0, "
        "agent TEXT NOT NULL DEFAULT 'claude', gates_json TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO dev_works(id,workspace_id,design_doc_id,prompt,"
        "current_step,iteration_rounds,agent,gates_json,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "dev-old",
            "ws-old",
            "des-old",
            "p",
            "INIT",
            0,
            "claude",
            json.dumps({"recommended_tech_stack": " React 18 + FastAPI "}),
            "2026-04-26",
            "2026-04-26",
        ),
    )
    legacy.commit()
    legacy.close()

    d = Database(db_path=db_path, schema_path="db/schema.sql")
    await d.connect()
    try:
        cols = {
            r["name"] for r in await d.fetchall("PRAGMA table_info(dev_works)")
        }
        assert "recommended_tech_stack" in cols
        row = await d.fetchone(
            "SELECT recommended_tech_stack FROM dev_works WHERE id=?",
            ("dev-old",),
        )
        assert row["recommended_tech_stack"] == "React 18 + FastAPI"
    finally:
        await d.close()


async def test_migrate_is_idempotent_on_fresh_db(tmp_path):
    """Running ``connect()`` twice on a fresh DB must not error on
    already-migrated columns."""
    d = Database(db_path=tmp_path / "fresh.db", schema_path="db/schema.sql")
    await d.connect()
    await d.close()
    # Reconnect — the migration should detect the modern column layout
    # and no-op rather than re-renaming.
    await d.connect()
    try:
        cols = {
            r["name"] for r in await d.fetchall("PRAGMA table_info(repos)")
        }
        assert "ssh_key_path" in cols
        assert "credential_ref" not in cols
    finally:
        await d.close()
