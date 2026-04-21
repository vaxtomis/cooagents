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
