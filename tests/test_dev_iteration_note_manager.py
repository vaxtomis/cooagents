"""Phase 4: DevIterationNoteManager unit tests."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.database import Database
from src.dev_iteration_note_manager import DevIterationNoteManager
from src.exceptions import BadRequestError
from src.workspace_manager import WorkspaceManager


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    ws_root = tmp_path / "ws"
    wm = WorkspaceManager(db, project_root=tmp_path, workspaces_root=ws_root)
    ws = await wm.create_with_scaffold(title="T", slug="t")
    # design_doc row is required for FK insertion of dev_works.
    await db.execute(
        "INSERT INTO design_docs(id, workspace_id, slug, version, path, "
        "status, created_at) VALUES(?,?,?,?,?,?,?)",
        ("des-abc", ws["id"], "demo", "1.0.0", "/tmp/x.md", "published", "t"),
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
    mgr = DevIterationNoteManager(db, workspaces_root=ws_root)
    yield dict(db=db, ws=ws, mgr=mgr, ws_root=ws_root)
    await db.close()


async def test_path_for_under_root(env):
    p = env["mgr"].path_for(env["ws"], "dev-xxx", 1)
    assert p.name == "iteration-round-1.md"
    assert str(p).startswith(str(env["ws_root"]))


async def test_path_for_rejects_escape(env):
    ws_bad = dict(env["ws"])
    ws_bad["slug"] = "../escape"
    with pytest.raises(BadRequestError):
        env["mgr"].path_for(ws_bad, "dev-xxx", 1)


async def test_record_round_inserts_row(env):
    p = env["mgr"].path_for(env["ws"], "dev-xxx", 1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# note", encoding="utf-8")
    row = await env["mgr"].record_round(
        workspace_row=env["ws"],
        dev_work_id="dev-xxx",
        round_n=1,
        markdown_path=str(p),
    )
    assert row["id"].startswith("note-")
    got = await env["db"].fetchone(
        "SELECT * FROM dev_iteration_notes WHERE id=?", (row["id"],)
    )
    assert got["round"] == 1
    assert got["markdown_path"] == str(p)


async def test_record_round_unique_per_round(env):
    p = env["mgr"].path_for(env["ws"], "dev-xxx", 2)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# note", encoding="utf-8")
    await env["mgr"].record_round(
        workspace_row=env["ws"],
        dev_work_id="dev-xxx",
        round_n=2,
        markdown_path=str(p),
    )
    # Second call with same (dev_work_id, round) must violate UNIQUE.
    with pytest.raises(sqlite3.IntegrityError):
        await env["mgr"].record_round(
            workspace_row=env["ws"],
            dev_work_id="dev-xxx",
            round_n=2,
            markdown_path=str(p),
        )


async def test_latest_for_returns_max_round(env):
    for r in (1, 2, 3):
        p = env["mgr"].path_for(env["ws"], "dev-xxx", r)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#", encoding="utf-8")
        await env["mgr"].record_round(
            workspace_row=env["ws"],
            dev_work_id="dev-xxx",
            round_n=r,
            markdown_path=str(p),
        )
    latest = await env["mgr"].latest_for("dev-xxx")
    assert latest["round"] == 3


async def test_append_score_accumulates(env):
    p = env["mgr"].path_for(env["ws"], "dev-xxx", 1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#", encoding="utf-8")
    row = await env["mgr"].record_round(
        workspace_row=env["ws"],
        dev_work_id="dev-xxx",
        round_n=1,
        markdown_path=str(p),
    )
    await env["mgr"].append_score(row["id"], 50)
    await env["mgr"].append_score(row["id"], 85)
    got = await env["db"].fetchone(
        "SELECT score_history_json FROM dev_iteration_notes WHERE id=?",
        (row["id"],),
    )
    assert json.loads(got["score_history_json"]) == [50, 85]
