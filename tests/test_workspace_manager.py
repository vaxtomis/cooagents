from pathlib import Path

import pytest

from src.database import Database
from src.workspace_manager import WorkspaceManager


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
async def wm(db, tmp_path):
    return WorkspaceManager(db, project_root=tmp_path)


async def test_create_and_get(wm):
    wid = await wm.create(title="Feature X", slug="feature-x", root_path="/tmp/ws/feature-x")
    assert wid.startswith("ws-")
    w = await wm.get(wid)
    assert w is not None
    assert w["title"] == "Feature X"
    assert w["status"] == "active"
    assert w["slug"] == "feature-x"
    assert w["root_path"] == "/tmp/ws/feature-x"


async def test_get_by_slug(wm):
    wid = await wm.create(title="Feature Y", slug="feature-y", root_path="/tmp/ws/feature-y")
    w = await wm.get_by_slug("feature-y")
    assert w is not None
    assert w["id"] == wid


async def test_get_missing_returns_none(wm):
    assert await wm.get("ws-missing") is None
    assert await wm.get_by_slug("not-here") is None


async def test_list_filters_status(wm):
    w1 = await wm.create(title="A", slug="a", root_path="/tmp/a")
    w2 = await wm.create(title="B", slug="b", root_path="/tmp/b")
    await wm.archive(w2)

    active_ids = {w["id"] for w in await wm.list(status="active")}
    archived_ids = {w["id"] for w in await wm.list(status="archived")}
    all_ids = {w["id"] for w in await wm.list()}

    assert active_ids == {w1}
    assert archived_ids == {w2}
    assert all_ids == {w1, w2}


async def test_archive_idempotent(wm):
    wid = await wm.create(title="Z", slug="z", root_path="/tmp/z")
    assert await wm.archive(wid) == 1
    assert await wm.archive(wid) == 0


async def test_archive_unknown_id_returns_zero(wm):
    assert await wm.archive("ws-nope") == 0


async def test_duplicate_slug_raises(wm):
    await wm.create(title="Dup", slug="dup", root_path="/tmp/dup")
    with pytest.raises(Exception):
        await wm.create(title="Dup2", slug="dup", root_path="/tmp/dup2")


# === Phase 2 additions ===


@pytest.fixture
async def wm_fs(db, tmp_path):
    return WorkspaceManager(
        db, project_root=tmp_path, workspaces_root=tmp_path / "ws"
    )


async def test_create_with_scaffold_writes_fs(wm_fs, tmp_path):
    ws = await wm_fs.create_with_scaffold(title="Demo", slug="demo")
    assert ws["id"].startswith("ws-")
    slug_dir = tmp_path / "ws" / "demo"
    assert slug_dir.is_dir()
    assert (slug_dir / "designs").is_dir()
    assert (slug_dir / "devworks").is_dir()
    md = (slug_dir / "workspace.md").read_text(encoding="utf-8")
    assert md.startswith("---\n")
    assert "id: " + ws["id"] in md
    assert "status: active" in md


@pytest.mark.parametrize(
    "bad_slug",
    [
        "BAD_SLUG",
        "-leading-dash",
        "trailing-dash-",
        "double--dash",
        "",
        "UPPER",
        "has space",
        "a" * 64,
    ],
)
async def test_create_with_scaffold_invalid_slug(wm_fs, bad_slug):
    from src.exceptions import BadRequestError
    with pytest.raises(BadRequestError):
        await wm_fs.create_with_scaffold(title="X", slug=bad_slug)


@pytest.mark.parametrize(
    "good_slug",
    [
        "a",
        "feature-x",
        "a1",
        "1a",
        "ab-cd-ef",
        "a" * 63,
    ],
)
async def test_create_with_scaffold_valid_slug(wm_fs, good_slug):
    ws = await wm_fs.create_with_scaffold(title="ok", slug=good_slug)
    assert ws["slug"] == good_slug


async def test_create_with_scaffold_conflict_on_existing_dir(wm_fs, tmp_path):
    (tmp_path / "ws" / "dup").mkdir(parents=True)
    from src.exceptions import ConflictError
    with pytest.raises(ConflictError):
        await wm_fs.create_with_scaffold(title="X", slug="dup")


async def test_create_with_scaffold_db_slug_precheck_blocks_before_fs_write(
    wm_fs, tmp_path
):
    """DB-row slug pre-check should fire before any FS scaffolding, so a
    lingering DB row (with the FS already cleaned up) does not cause the
    scaffold to be partially re-created."""
    from src.exceptions import ConflictError
    await wm_fs.create_with_scaffold(title="A", slug="same-slug")
    slug_dir = tmp_path / "ws" / "same-slug"
    assert slug_dir.is_dir()
    import shutil
    shutil.rmtree(slug_dir)
    with pytest.raises(ConflictError):
        await wm_fs.create_with_scaffold(title="B", slug="same-slug")
    assert not slug_dir.exists()


async def test_fs_rollback_on_db_failure(wm_fs, tmp_path, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("simulated db failure")
    monkeypatch.setattr(wm_fs.db, "execute", boom)
    with pytest.raises(RuntimeError):
        await wm_fs.create_with_scaffold(title="X", slug="rollback-test")
    assert not (tmp_path / "ws" / "rollback-test").exists()


async def test_archive_with_scaffold_updates_md(wm_fs, tmp_path):
    ws = await wm_fs.create_with_scaffold(title="Arch", slug="arch")
    changed = await wm_fs.archive_with_scaffold(ws["id"])
    assert changed is True
    md = (tmp_path / "ws" / "arch" / "workspace.md").read_text(encoding="utf-8")
    assert "status: archived" in md
    assert await wm_fs.archive_with_scaffold(ws["id"]) is False


async def test_archive_with_scaffold_missing_raises(wm_fs):
    from src.exceptions import NotFoundError
    with pytest.raises(NotFoundError):
        await wm_fs.archive_with_scaffold("ws-nope")


async def test_archive_with_scaffold_missing_md_warns_not_raises(
    wm_fs, tmp_path, caplog
):
    ws = await wm_fs.create_with_scaffold(title="A", slug="a-miss")
    (tmp_path / "ws" / "a-miss" / "workspace.md").unlink()
    with caplog.at_level("WARNING", logger="src.workspace_manager"):
        assert await wm_fs.archive_with_scaffold(ws["id"]) is True
    assert any("workspace.md missing" in r.message for r in caplog.records)


async def test_reconcile_fs_only_inserts_db(wm_fs, tmp_path):
    manual = tmp_path / "ws" / "manual"
    (manual / "designs").mkdir(parents=True)
    (manual / "devworks").mkdir()
    (manual / "workspace.md").write_text(
        "---\nid: ws-manual12345\ntitle: Manual\nslug: manual\n"
        "created_at: 2026-01-01T00:00:00+00:00\nstatus: active\n---\n",
        encoding="utf-8",
    )
    report = await wm_fs.reconcile()
    assert "manual" in report["fs_only"]
    row = await wm_fs.get_by_slug("manual")
    assert row is not None
    assert row["id"] == "ws-manual12345"


async def test_reconcile_db_only_archives(wm_fs, tmp_path):
    await wm_fs.create_with_scaffold(title="Ghost", slug="ghost")
    import shutil
    shutil.rmtree(tmp_path / "ws" / "ghost")
    report = await wm_fs.reconcile()
    row = await wm_fs.get_by_slug("ghost")
    assert row["status"] == "archived"
    assert row["id"] in report["db_only"]


async def test_reconcile_skips_non_conforming_dirs(wm_fs, tmp_path, caplog):
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ws" / "NotASlug_BAD").mkdir()
    with caplog.at_level("WARNING", logger="src.workspace_manager"):
        report = await wm_fs.reconcile()
    assert "NotASlug_BAD" not in report["fs_only"]
    assert any("non-conforming dir" in r.message for r in caplog.records)


async def test_reconcile_in_sync(wm_fs):
    ws = await wm_fs.create_with_scaffold(title="Z", slug="z")
    report = await wm_fs.reconcile()
    assert ws["id"] in report["in_sync"]
    assert report["fs_only"] == []
    assert report["db_only"] == []


# ---- Phase 3 additions: refresh_workspace_md ----


async def test_refresh_workspace_md_shows_active_design_work(wm_fs):
    ws = await wm_fs.create_with_scaffold(title="R", slug="r")
    await wm_fs.db.execute(
        "INSERT INTO design_works(id, workspace_id, mode, current_state, loop, "
        "agent, sub_slug, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            "desw-1", ws["id"], "new", "PROMPT_COMPOSE", 1, "claude",
            "login", "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    await wm_fs.refresh_workspace_md(ws["id"])
    md = (Path(ws["root_path"]) / "workspace.md").read_text(encoding="utf-8")
    assert "design_work desw-1" in md
    assert "state=PROMPT_COMPOSE" in md


async def test_refresh_workspace_md_lists_published_design_doc(wm_fs):
    ws = await wm_fs.create_with_scaffold(title="R2", slug="r2")
    await wm_fs.db.execute(
        "INSERT INTO design_docs(id, workspace_id, slug, version, path, "
        "status, created_at) VALUES(?,?,?,?,?,?,?)",
        (
            "des-1", ws["id"], "login", "1.0.0", "/tmp/x.md",
            "published", "2026-01-01T00:00:00+00:00",
        ),
    )
    await wm_fs.refresh_workspace_md(ws["id"])
    md = (Path(ws["root_path"]) / "workspace.md").read_text(encoding="utf-8")
    assert "DES-login-1.0.0.md" in md
    assert "published" in md


async def test_refresh_workspace_md_idempotent(wm_fs):
    ws = await wm_fs.create_with_scaffold(title="R3", slug="r3")
    await wm_fs.refresh_workspace_md(ws["id"])
    first = (Path(ws["root_path"]) / "workspace.md").read_text(encoding="utf-8")
    await wm_fs.refresh_workspace_md(ws["id"])
    second = (Path(ws["root_path"]) / "workspace.md").read_text(encoding="utf-8")
    assert first == second
