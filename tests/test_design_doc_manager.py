"""Phase 3: DesignDocManager tests."""
import hashlib

import pytest

from src.database import Database
from src.design_doc_manager import DesignDocManager
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.workspace_manager import WorkspaceManager


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    wm = WorkspaceManager(
        db, project_root=tmp_path, workspaces_root=tmp_path / "ws"
    )
    ws = await wm.create_with_scaffold(title="Demo", slug="demo")
    ddm = DesignDocManager(db, workspaces_root=tmp_path / "ws")
    yield dict(db=db, wm=wm, ws=ws, ddm=ddm, root=tmp_path)
    await db.close()


async def test_persist_writes_file_and_row(env):
    md = "# hi\n\nbody\n"
    row = await env["ddm"].persist(
        workspace_row=env["ws"], slug="login", version="1.0.0",
        markdown=md, parent_version=None,
        needs_frontend_mockup=False, rubric_threshold=85,
    )
    assert row["status"] == "draft"
    target = env["root"] / "ws" / "demo" / "designs" / "DES-login-1.0.0.md"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == md
    # content_hash matches
    assert row["content_hash"] == hashlib.sha256(md.encode("utf-8")).hexdigest()
    assert row["byte_size"] == len(md.encode("utf-8"))


async def test_persist_conflict_on_dup_version(env):
    await env["ddm"].persist(
        workspace_row=env["ws"], slug="a", version="1.0.0", markdown="x",
        parent_version=None, needs_frontend_mockup=False, rubric_threshold=80,
    )
    with pytest.raises(ConflictError):
        await env["ddm"].persist(
            workspace_row=env["ws"], slug="a", version="1.0.0", markdown="y",
            parent_version=None, needs_frontend_mockup=False, rubric_threshold=80,
        )


async def test_publish_links_to_design_work(env):
    # Seed a minimal design_work row
    await env["db"].execute(
        "INSERT INTO design_works(id, workspace_id, mode, current_state, "
        "loop, agent, title, sub_slug, version, output_path, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "desw-1", env["ws"]["id"], "new", "PERSIST", 0, "claude",
            "T", "a", "1.0.0", "/tmp/x", "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    row = await env["ddm"].persist(
        workspace_row=env["ws"], slug="a", version="1.0.0", markdown="x",
        parent_version=None, needs_frontend_mockup=False, rubric_threshold=80,
    )
    await env["ddm"].publish(row["id"], "desw-1")
    pub = await env["db"].fetchone(
        "SELECT status, published_at FROM design_docs WHERE id=?", (row["id"],)
    )
    assert pub["status"] == "published"
    assert pub["published_at"] is not None
    linked = await env["db"].fetchone(
        "SELECT output_design_doc_id FROM design_works WHERE id='desw-1'"
    )
    assert linked["output_design_doc_id"] == row["id"]


async def test_publish_twice_raises(env):
    await env["db"].execute(
        "INSERT INTO design_works(id, workspace_id, mode, current_state, "
        "loop, agent, title, sub_slug, version, output_path, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "desw-2", env["ws"]["id"], "new", "PERSIST", 0, "claude",
            "T", "b", "1.0.0", "/tmp/x", "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    row = await env["ddm"].persist(
        workspace_row=env["ws"], slug="b", version="1.0.0", markdown="x",
        parent_version=None, needs_frontend_mockup=False, rubric_threshold=80,
    )
    await env["ddm"].publish(row["id"], "desw-2")
    with pytest.raises(NotFoundError):
        await env["ddm"].publish(row["id"], "desw-2")


async def test_rollback_fs_on_db_error(env, monkeypatch):
    target = env["root"] / "ws" / "demo" / "designs" / "DES-rb-1.0.0.md"

    original_execute = env["db"].execute

    async def boom(sql, *a, **kw):
        if "INSERT INTO design_docs" in sql:
            raise RuntimeError("simulated DB failure")
        return await original_execute(sql, *a, **kw)

    monkeypatch.setattr(env["db"], "execute", boom)
    with pytest.raises(RuntimeError):
        await env["ddm"].persist(
            workspace_row=env["ws"], slug="rb", version="1.0.0", markdown="x",
            parent_version=None, needs_frontend_mockup=False, rubric_threshold=80,
        )
    assert not target.exists()


async def test_doc_path_escape_blocked(env):
    bad_ws = dict(env["ws"])
    bad_ws["slug"] = "..\\..\\outside"  # attempts to escape root
    with pytest.raises(BadRequestError):
        env["ddm"]._doc_path(bad_ws, "x", "1.0.0")
