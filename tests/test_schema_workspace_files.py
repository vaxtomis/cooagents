import sqlite3

import pytest
from src.database import Database


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


async def test_workspace_files_table_exists(db):
    row = await db.fetchone(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='workspace_files'"
    )
    assert row is not None


async def test_workspace_files_column_set(db):
    cols = await db.fetchall("PRAGMA table_info(workspace_files)")
    by_name = {c["name"]: c for c in cols}
    expected = {
        "id", "workspace_id", "relative_path", "kind",
        "content_hash", "byte_size",
        "local_mtime_ns", "created_at", "updated_at",
    }
    assert set(by_name) == expected
    # NOT NULL invariants from PRD
    assert by_name["id"]["pk"] == 1
    for nn in ("workspace_id", "relative_path", "kind", "created_at", "updated_at"):
        assert by_name[nn]["notnull"] == 1, f"{nn} must be NOT NULL"
    # Nullable metadata
    for nullable in ("content_hash", "byte_size", "local_mtime_ns"):
        assert by_name[nullable]["notnull"] == 0


async def test_workspace_files_kind_check_rejects_unknown(db):
    await _seed_workspace(db, "ws-kk", "kk")
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(
            "INSERT INTO workspace_files(id,workspace_id,relative_path,kind,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("wf-1", "ws-kk", "a.txt", "bogus",
             "2026-04-24T00:00:00Z", "2026-04-24T00:00:00Z"),
        )


@pytest.mark.parametrize("kind", [
    "design_doc", "design_input", "iteration_note",
    "prompt", "image", "workspace_md",
    "context", "artifact", "attachment", "feedback", "other",
])
async def test_workspace_files_kind_check_accepts_all_allowed(db, kind):
    await _seed_workspace(db, f"ws-{kind}", kind)
    await db.execute(
        "INSERT INTO workspace_files(id,workspace_id,relative_path,kind,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?)",
        (f"wf-{kind}", f"ws-{kind}", f"x/{kind}.md", kind,
         "2026-04-24T00:00:00Z", "2026-04-24T00:00:00Z"),
    )


async def test_workspace_files_kind_check_rejects_old_value_index(db):
    """Guard against regression: 'index' was the PRD draft name; the live
    schema renames it to 'workspace_md'. A caller re-reading the PRD must
    still be blocked."""
    await _seed_workspace(db, "ws-ix", "ix")
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(
            "INSERT INTO workspace_files(id,workspace_id,relative_path,kind,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("wf-ix", "ws-ix", "index.md", "index",
             "2026-04-24T00:00:00Z", "2026-04-24T00:00:00Z"),
        )


async def test_workspace_files_unique_workspace_relpath(db):
    await _seed_workspace(db, "ws-u", "u")
    await db.execute(
        "INSERT INTO workspace_files(id,workspace_id,relative_path,kind,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?)",
        ("wf-a", "ws-u", "designs/DES-x-1.0.0.md", "design_doc",
         "2026-04-24T00:00:00Z", "2026-04-24T00:00:00Z"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(
            "INSERT INTO workspace_files(id,workspace_id,relative_path,kind,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("wf-b", "ws-u", "designs/DES-x-1.0.0.md", "design_doc",
             "2026-04-24T00:00:00Z", "2026-04-24T00:00:00Z"),
        )


async def test_workspace_files_fk_rejects_unknown_workspace(db):
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(
            "INSERT INTO workspace_files(id,workspace_id,relative_path,kind,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("wf-f", "ws-does-not-exist", "a.md", "other",
             "2026-04-24T00:00:00Z", "2026-04-24T00:00:00Z"),
        )


async def test_workspace_files_indexes_present(db):
    rows = await db.fetchall(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='workspace_files'"
    )
    names = {r["name"] for r in rows}
    for expected in (
        "idx_workspace_files_workspace",
        "idx_workspace_files_kind",
    ):
        assert expected in names, f"missing index {expected}"


async def _seed_workspace(db, ws_id: str, slug: str) -> None:
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (ws_id, f"t-{slug}", slug, "active", f"/tmp/{slug}",
         "2026-04-24T00:00:00Z", "2026-04-24T00:00:00Z"),
    )
