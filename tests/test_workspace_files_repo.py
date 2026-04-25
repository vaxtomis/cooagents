"""Unit tests for WorkspaceFilesRepo."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.database import Database
from src.exceptions import BadRequestError
from src.storage.registry import WorkspaceFilesRepo


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    repo = WorkspaceFilesRepo(db)
    yield dict(db=db, repo=repo, tmp=tmp_path)
    await db.close()


async def _seed_workspace(db, ws_id: str, slug: str) -> None:
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,"
        "updated_at) VALUES(?,?,?,?,?,?,?)",
        (ws_id, f"t-{slug}", slug, "active", f"/tmp/{slug}",
         "2026-04-24T00:00:00Z", "2026-04-24T00:00:00Z"),
    )


async def test_upsert_inserts_new_row(env):
    repo = env["repo"]
    await _seed_workspace(env["db"], "ws-a", "alpha")
    row = await repo.upsert(
        workspace_id="ws-a",
        relative_path="designs/a.md",
        kind="design_doc",
        content_hash="h1",
        byte_size=3,
        local_mtime_ns=1,
    )
    assert row["relative_path"] == "designs/a.md"
    assert row["kind"] == "design_doc"
    assert row["content_hash"] == "h1"
    assert row["byte_size"] == 3
    assert row["local_mtime_ns"] == 1
    # DB row mirrors return value
    db_row = await repo.get("ws-a", "designs/a.md")
    assert db_row is not None
    assert db_row["content_hash"] == "h1"


async def test_upsert_updates_existing_row(env):
    repo = env["repo"]
    await _seed_workspace(env["db"], "ws-a", "alpha")
    first = await repo.upsert(
        workspace_id="ws-a", relative_path="designs/a.md",
        kind="design_doc", content_hash="h1", byte_size=3, local_mtime_ns=1,
    )
    second = await repo.upsert(
        workspace_id="ws-a", relative_path="designs/a.md",
        kind="design_doc", content_hash="h2", byte_size=7, local_mtime_ns=2,
    )
    assert second["id"] == first["id"]
    assert second["created_at"] == first["created_at"]
    assert second["content_hash"] == "h2"
    assert second["byte_size"] == 7
    assert second["local_mtime_ns"] == 2
    # still one row
    rows = await repo.list_for_workspace("ws-a")
    assert len(rows) == 1


async def test_upsert_rejects_bad_kind(env):
    repo = env["repo"]
    await _seed_workspace(env["db"], "ws-a", "alpha")
    with pytest.raises(BadRequestError):
        await repo.upsert(
            workspace_id="ws-a", relative_path="designs/a.md",
            kind="bogus", content_hash="h", byte_size=1, local_mtime_ns=1,
        )
    rows = await repo.list_for_workspace("ws-a")
    assert rows == []


async def test_upsert_rejects_absolute_path(env):
    repo = env["repo"]
    await _seed_workspace(env["db"], "ws-a", "alpha")
    with pytest.raises(BadRequestError):
        await repo.upsert(
            workspace_id="ws-a", relative_path="/etc/passwd",
            kind="other", content_hash="h", byte_size=1, local_mtime_ns=1,
        )


async def test_upsert_rejects_backslash_path(env):
    repo = env["repo"]
    await _seed_workspace(env["db"], "ws-a", "alpha")
    with pytest.raises(BadRequestError):
        await repo.upsert(
            workspace_id="ws-a", relative_path="a\\b.md",
            kind="other", content_hash="h", byte_size=1, local_mtime_ns=1,
        )


async def test_get_returns_none_for_missing(env):
    repo = env["repo"]
    await _seed_workspace(env["db"], "ws-a", "alpha")
    assert await repo.get("ws-a", "nope.md") is None


async def test_list_for_workspace_sorted_by_path(env):
    repo = env["repo"]
    await _seed_workspace(env["db"], "ws-a", "alpha")
    for rel in ("c/c.md", "a/a.md", "b/b.md"):
        await repo.upsert(
            workspace_id="ws-a", relative_path=rel,
            kind="other", content_hash="h", byte_size=1, local_mtime_ns=1,
        )
    rows = await repo.list_for_workspace("ws-a")
    assert [r["relative_path"] for r in rows] == ["a/a.md", "b/b.md", "c/c.md"]


async def test_delete_is_idempotent(env):
    repo = env["repo"]
    await _seed_workspace(env["db"], "ws-a", "alpha")
    await repo.upsert(
        workspace_id="ws-a", relative_path="a.md",
        kind="other", content_hash="h", byte_size=1, local_mtime_ns=1,
    )
    await repo.delete("ws-a", "a.md")
    await repo.delete("ws-a", "a.md")  # no raise
    assert await repo.get("ws-a", "a.md") is None


async def test_unique_per_workspace(env):
    repo = env["repo"]
    await _seed_workspace(env["db"], "ws-a", "alpha")
    await _seed_workspace(env["db"], "ws-b", "beta")
    rel = "designs/DES-x-1.0.0.md"
    await repo.upsert(
        workspace_id="ws-a", relative_path=rel,
        kind="design_doc", content_hash="h", byte_size=1, local_mtime_ns=1,
    )
    await repo.upsert(
        workspace_id="ws-b", relative_path=rel,
        kind="design_doc", content_hash="h", byte_size=1, local_mtime_ns=1,
    )
    assert await repo.get("ws-a", rel) is not None
    assert await repo.get("ws-b", rel) is not None


def _parse_check_kinds(schema_sql: str) -> set[str]:
    """Extract the CHECK(kind IN (...)) literals from schema.sql."""
    # Find the workspace_files ... kind CHECK clause.
    m = re.search(
        r"kind\s+TEXT\s+NOT\s+NULL\s+CHECK\(kind\s+IN\s*\(([^)]+)\)\)",
        schema_sql,
        flags=re.DOTALL,
    )
    assert m, "could not parse kind CHECK clause from schema.sql"
    return set(re.findall(r"'([^']+)'", m.group(1)))


async def test_valid_kinds_matches_schema():
    schema_path = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")
    parsed = _parse_check_kinds(schema_sql)
    assert parsed == set(WorkspaceFilesRepo._VALID_KINDS), (
        f"drift between SQL CHECK ({parsed}) and "
        f"WorkspaceFilesRepo._VALID_KINDS ({set(WorkspaceFilesRepo._VALID_KINDS)})"
    )
