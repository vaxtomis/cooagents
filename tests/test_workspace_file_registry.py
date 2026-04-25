"""Integration tests for WorkspaceFileRegistry."""
from __future__ import annotations

import hashlib
import json

import pytest

from src.database import Database
from src.exceptions import BadRequestError, NotFoundError
from src.storage import LocalFileStore
from src.storage.registry import WorkspaceFileRegistry, WorkspaceFilesRepo


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    root = tmp_path / "ws"
    root.mkdir()
    store = LocalFileStore(workspaces_root=root)
    repo = WorkspaceFilesRepo(db)
    registry = WorkspaceFileRegistry(store=store, repo=repo)
    # seed a workspace row for FK
    await db.execute(
        "INSERT INTO workspaces(id,title,slug,status,root_path,created_at,"
        "updated_at) VALUES(?,?,?,?,?,?,?)",
        ("ws-a", "Alpha", "alpha", "active", str(root / "alpha"),
         "2026-04-24T00:00:00Z", "2026-04-24T00:00:00Z"),
    )
    ws_row = {"id": "ws-a", "slug": "alpha"}
    yield dict(
        db=db, store=store, repo=repo, registry=registry,
        root=root, ws=ws_row,
    )
    await db.close()


async def test_put_markdown_writes_file_and_row(env):
    reg = env["registry"]
    ws = env["ws"]
    row = await reg.put_markdown(
        workspace_row=ws, relative_path="designs/a.md",
        text="hello\n", kind="design_doc",
    )
    f = env["root"] / "alpha" / "designs" / "a.md"
    assert f.read_bytes() == b"hello\n"
    assert row["content_hash"] == hashlib.sha256(b"hello\n").hexdigest()
    assert row["byte_size"] == 6
    assert row["kind"] == "design_doc"
    assert row["relative_path"] == "designs/a.md"


async def test_put_markdown_is_byte_exact_on_crlf(env):
    reg = env["registry"]
    ws = env["ws"]
    payload = "a\r\nb\n"
    await reg.put_markdown(
        workspace_row=ws, relative_path="x.md", text=payload, kind="other",
    )
    read_back = await reg.read_text(workspace_slug="alpha", relative_path="x.md")
    assert read_back == payload
    f = env["root"] / "alpha" / "x.md"
    assert f.read_bytes() == payload.encode("utf-8")


async def test_put_markdown_twice_updates_row_not_insert(env):
    reg = env["registry"]
    ws = env["ws"]
    await reg.put_markdown(
        workspace_row=ws, relative_path="a.md", text="one", kind="other",
    )
    await reg.put_markdown(
        workspace_row=ws, relative_path="a.md", text="two", kind="other",
    )
    rows = await env["repo"].list_for_workspace("ws-a")
    assert len(rows) == 1
    assert rows[0]["content_hash"] == hashlib.sha256(b"two").hexdigest()


async def test_read_text_roundtrips(env):
    reg = env["registry"]
    ws = env["ws"]
    await reg.put_markdown(
        workspace_row=ws, relative_path="r.md", text="round-trip",
        kind="other",
    )
    txt = await reg.read_text(workspace_slug="alpha", relative_path="r.md")
    assert txt == "round-trip"


async def test_read_text_missing_raises_not_found(env):
    reg = env["registry"]
    with pytest.raises(NotFoundError):
        await reg.read_text(workspace_slug="alpha", relative_path="nope.md")


async def test_delete_removes_file_and_row(env):
    reg = env["registry"]
    ws = env["ws"]
    await reg.put_markdown(
        workspace_row=ws, relative_path="d.md", text="x", kind="other",
    )
    f = env["root"] / "alpha" / "d.md"
    assert f.exists()
    await reg.delete(workspace_row=ws, relative_path="d.md")
    assert not f.exists()
    assert await env["repo"].get("ws-a", "d.md") is None


async def test_db_failure_leaves_fs_write(env, monkeypatch):
    # register() = local atomic write → DB upsert. A DB failure after the
    # local write leaves the on-disk file behind; the next successful
    # register() of the same path overwrites it.
    reg = env["registry"]
    ws = env["ws"]

    async def boom(**kwargs):
        raise RuntimeError("db boom")

    monkeypatch.setattr(env["repo"], "upsert", boom)
    with pytest.raises(RuntimeError):
        await reg.put_markdown(
            workspace_row=ws, relative_path="fail.md",
            text="data", kind="other",
        )
    f = env["root"] / "alpha" / "fail.md"
    assert f.exists()


async def test_rejects_absolute_relative_path(env):
    reg = env["registry"]
    ws = env["ws"]
    with pytest.raises(BadRequestError):
        await reg.put_markdown(
            workspace_row=ws, relative_path="/etc/passwd",
            text="x", kind="other",
        )


async def test_rejects_slug_containing_slash(env):
    reg = env["registry"]
    with pytest.raises(BadRequestError):
        reg._compose_key("bad/slug", "a.md")
    with pytest.raises(BadRequestError):
        reg._compose_key("bad\\slug", "a.md")


async def test_kind_enum_enforced(env):
    reg = env["registry"]
    ws = env["ws"]
    with pytest.raises(BadRequestError):
        await reg.put_markdown(
            workspace_row=ws, relative_path="k.md",
            text="x", kind="bogus",
        )
    # kind validation precedes the FS write, so nothing is on disk.
    f = env["root"] / "alpha" / "k.md"
    assert not f.exists()


async def test_put_json_serialises_and_registers_as_artifact(env):
    reg = env["registry"]
    ws = env["ws"]
    payload = {"a": 1, "nested": [1, 2, 3]}
    row = await reg.put_json(
        workspace_row=ws,
        relative_path="devworks/dev-1/artifacts/f.json",
        payload=payload, kind="artifact",
    )
    f = env["root"] / "alpha" / "devworks" / "dev-1" / "artifacts" / "f.json"
    assert json.loads(f.read_text(encoding="utf-8")) == payload
    expected = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    assert row["content_hash"] == hashlib.sha256(expected).hexdigest()
    assert row["kind"] == "artifact"


async def test_index_existing_reads_and_registers(env):
    reg = env["registry"]
    ws = env["ws"]
    # bypass the registry — write directly through the store
    store_key = reg._compose_key("alpha", "devworks/dev-1/context/ctx-round-1.md")
    await env["store"].put_bytes(store_key, b"raw")
    row = await reg.index_existing(
        workspace_row=ws,
        relative_path="devworks/dev-1/context/ctx-round-1.md",
        kind="context",
    )
    assert row["content_hash"] == hashlib.sha256(b"raw").hexdigest()
    assert row["byte_size"] == 3
    assert row["kind"] == "context"


async def test_index_existing_raises_when_file_missing(env):
    reg = env["registry"]
    ws = env["ws"]
    with pytest.raises(NotFoundError):
        await reg.index_existing(
            workspace_row=ws, relative_path="nope.md", kind="context",
        )


async def test_index_existing_idempotent(env):
    reg = env["registry"]
    ws = env["ws"]
    store_key = reg._compose_key("alpha", "i.md")
    await env["store"].put_bytes(store_key, b"hi")
    first = await reg.index_existing(
        workspace_row=ws, relative_path="i.md", kind="other",
    )
    second = await reg.index_existing(
        workspace_row=ws, relative_path="i.md", kind="other",
    )
    assert first["id"] == second["id"]
    assert first["created_at"] == second["created_at"]
    rows = await env["repo"].list_for_workspace("ws-a")
    assert len(rows) == 1
