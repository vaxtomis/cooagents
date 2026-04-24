"""Phase 4: DevIterationNoteManager unit tests."""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.database import Database
from src.dev_iteration_note_manager import DevIterationNoteManager
from src.exceptions import BadRequestError
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
    ws = await wm.create_with_scaffold(title="T", slug="t")
    # design_doc row is required for FK insertion of dev_works.
    await db.execute(
        "INSERT INTO design_docs(id, workspace_id, slug, version, path, "
        "status, created_at) VALUES(?,?,?,?,?,?,?)",
        ("des-abc", ws["id"], "demo", "1.0.0",
         "designs/DES-demo-1.0.0.md", "published", "t"),
    )
    # dev_works row so FK on dev_iteration_notes.dev_work_id succeeds.
    await db.execute(
        "INSERT INTO dev_works(id, workspace_id, design_doc_id, repo_path, "
        "prompt, current_step, iteration_rounds, agent, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "dev-xxx", ws["id"], "des-abc", "/tmp/repo", "p",
            "INIT", 0, "claude", "t", "t",
        ),
    )
    mgr = DevIterationNoteManager(db)
    yield dict(
        db=db, ws=ws, mgr=mgr, ws_root=ws_root, registry=registry,
    )
    await db.close()


async def test_relative_for_is_workspace_relative(env):
    rel = env["mgr"].relative_for("dev-xxx", 1)
    assert rel == "devworks/dev-xxx/iteration-round-1.md"


async def test_record_round_rejects_absolute_path(env):
    with pytest.raises(BadRequestError):
        await env["mgr"].record_round(
            workspace_row=env["ws"],
            dev_work_id="dev-xxx",
            round_n=1,
            markdown_path="/etc/passwd",
        )


async def test_record_round_inserts_row(env):
    rel = env["mgr"].relative_for("dev-xxx", 1)
    await env["registry"].put_markdown(
        workspace_row=env["ws"], relative_path=rel,
        text="# note", kind="iteration_note",
    )
    row = await env["mgr"].record_round(
        workspace_row=env["ws"],
        dev_work_id="dev-xxx",
        round_n=1,
        markdown_path=rel,
    )
    assert row["id"].startswith("note-")
    got = await env["db"].fetchone(
        "SELECT * FROM dev_iteration_notes WHERE id=?", (row["id"],)
    )
    assert got["round"] == 1
    assert got["markdown_path"] == rel


async def test_record_round_unique_per_round(env):
    rel = env["mgr"].relative_for("dev-xxx", 2)
    await env["registry"].put_markdown(
        workspace_row=env["ws"], relative_path=rel,
        text="# note", kind="iteration_note",
    )
    await env["mgr"].record_round(
        workspace_row=env["ws"],
        dev_work_id="dev-xxx",
        round_n=2,
        markdown_path=rel,
    )
    # Second call with same (dev_work_id, round) must violate UNIQUE.
    with pytest.raises(sqlite3.IntegrityError):
        await env["mgr"].record_round(
            workspace_row=env["ws"],
            dev_work_id="dev-xxx",
            round_n=2,
            markdown_path=rel,
        )


async def test_latest_for_returns_max_round(env):
    for r in (1, 2, 3):
        rel = env["mgr"].relative_for("dev-xxx", r)
        await env["registry"].put_markdown(
            workspace_row=env["ws"], relative_path=rel,
            text="#", kind="iteration_note",
        )
        await env["mgr"].record_round(
            workspace_row=env["ws"],
            dev_work_id="dev-xxx",
            round_n=r,
            markdown_path=rel,
        )
    latest = await env["mgr"].latest_for("dev-xxx")
    assert latest["round"] == 3


async def test_append_score_accumulates(env):
    rel = env["mgr"].relative_for("dev-xxx", 1)
    await env["registry"].put_markdown(
        workspace_row=env["ws"], relative_path=rel,
        text="#", kind="iteration_note",
    )
    row = await env["mgr"].record_round(
        workspace_row=env["ws"],
        dev_work_id="dev-xxx",
        round_n=1,
        markdown_path=rel,
    )
    await env["mgr"].append_score(row["id"], 50)
    await env["mgr"].append_score(row["id"], 85)
    got = await env["db"].fetchone(
        "SELECT score_history_json FROM dev_iteration_notes WHERE id=?",
        (row["id"],),
    )
    assert json.loads(got["score_history_json"]) == [50, 85]
