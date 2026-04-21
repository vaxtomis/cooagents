import pytest

from src.database import Database


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


EXPECTED_TABLES = {
    "workspaces",
    "design_works",
    "design_docs",
    "dev_works",
    "dev_iteration_notes",
    "reviews",
    "workspace_events",
}

REMOVED_TABLES = {
    "runs",
    "steps",
    "events",
    "approvals",
    "artifacts",
    "jobs",
    "merge_queue",
    "turns",
}

NOW = "2026-01-01T00:00:00Z"


async def test_expected_tables_exist(db):
    rows = await db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    names = {r["name"] for r in rows}
    missing = EXPECTED_TABLES - names
    assert not missing, f"Missing tables: {missing}"


async def test_removed_tables_absent(db):
    rows = await db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    names = {r["name"] for r in rows}
    leftover = names & REMOVED_TABLES
    assert not leftover, f"Old tables still present: {leftover}"


async def test_dev_works_indicator_columns(db):
    rows = await db.fetchall("PRAGMA table_info(dev_works)")
    cols = {r["name"] for r in rows}
    assert {
        "iteration_rounds",
        "first_pass_success",
        "last_score",
        "last_problem_category",
    }.issubset(cols)


async def test_design_docs_unique_workspace_slug_version(db):
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("ws-1", "W1", "w1", "active", "/tmp/w1", NOW, NOW),
    )
    await db.execute(
        "INSERT INTO design_docs(id,workspace_id,slug,version,path,created_at) VALUES(?,?,?,?,?,?)",
        ("des-1", "ws-1", "abc123def456", "1.0.0", "designs/DES-abc123def456-1.0.0.md", NOW),
    )
    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO design_docs(id,workspace_id,slug,version,path,created_at) VALUES(?,?,?,?,?,?)",
            ("des-2", "ws-1", "abc123def456", "1.0.0", "designs/dup.md", NOW),
        )


async def test_reviews_xor_constraint(db):
    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO reviews(id,round,created_at) VALUES(?,?,?)",
            ("rev-null", 1, NOW),
        )


async def test_workspace_events_event_id_unique(db):
    await db.execute(
        "INSERT INTO workspace_events(event_id,event_name,ts) VALUES(?,?,?)",
        ("evt-1", "workspace.created", NOW),
    )
    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO workspace_events(event_id,event_name,ts) VALUES(?,?,?)",
            ("evt-1", "workspace.created", NOW),
        )
