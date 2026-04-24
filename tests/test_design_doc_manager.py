"""Phase 3: DesignDocManager tests."""
import hashlib

import pytest

from src.database import Database
from src.design_doc_manager import DesignDocManager
from src.exceptions import ConflictError, NotFoundError
from src.storage import LocalFileStore
from src.storage.registry import WorkspaceFileRegistry, WorkspaceFilesRepo
from src.workspace_manager import WorkspaceManager


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    store = LocalFileStore(workspaces_root=ws_root)
    repo = WorkspaceFilesRepo(db)
    registry = WorkspaceFileRegistry(store=store, repo=repo)
    wm = WorkspaceManager(
        db, project_root=tmp_path, workspaces_root=ws_root, registry=registry,
    )
    ws = await wm.create_with_scaffold(title="Demo", slug="demo")
    ddm = DesignDocManager(db, registry=registry)
    yield dict(
        db=db, wm=wm, ws=ws, ddm=ddm, registry=registry, root=tmp_path,
    )
    await db.close()


async def test_persist_writes_file_and_row(env):
    md = "# hi\n\nbody\n"
    row = await env["ddm"].persist(
        workspace_row=env["ws"], slug="login", version="1.0.0",
        markdown=md, parent_version=None,
        needs_frontend_mockup=False, rubric_threshold=85,
    )
    assert row["status"] == "draft"
    assert row["path"] == "designs/DES-login-1.0.0.md"
    target = env["root"] / "ws" / "demo" / "designs" / "DES-login-1.0.0.md"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == md
    # content_hash matches
    assert row["content_hash"] == hashlib.sha256(md.encode("utf-8")).hexdigest()
    assert row["byte_size"] == len(md.encode("utf-8"))
    # workspace_files row registered
    wf = await env["registry"].repo.get(
        env["ws"]["id"], "designs/DES-login-1.0.0.md",
    )
    assert wf is not None
    assert wf["kind"] == "design_doc"


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
            "T", "a", "1.0.0", "designs/DES-a-1.0.0.md",
            "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
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
            "T", "b", "1.0.0", "designs/DES-b-1.0.0.md",
            "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
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
    # workspace_files row for the rolled-back doc must not survive
    wf_rows = await env["db"].fetchall(
        "SELECT * FROM workspace_files WHERE relative_path='designs/DES-rb-1.0.0.md'"
    )
    assert wf_rows == []
