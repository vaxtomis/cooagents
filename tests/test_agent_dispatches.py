"""Phase 8a: end-to-end test that SMs persist agent_host_id and write
agent_dispatches lifecycle rows.

Uses the existing DesignWorkStateMachine fixture style with a stub
executor that sees and records the host_id kwarg.
"""
from __future__ import annotations

import pytest

from src.acpx_executor import AcpxExecutor
from src.agent_hosts.repo import AgentDispatchRepo, AgentHostRepo
from src.database import Database
from src.dev_work_sm import DevWorkStateMachine
from src.storage.local import LocalFileStore
from src.storage.registry import WorkspaceFileRegistry, WorkspaceFilesRepo
from src.workspace_manager import WorkspaceManager
from src.design_doc_manager import DesignDocManager
from src.dev_iteration_note_manager import DevIterationNoteManager


# Reuse the dev_work_sm test scaffolding minimal subset.
class CapturingExecutor(AcpxExecutor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[dict] = []

    async def run_once(self, agent_type, worktree, timeout_sec,
                       task_file=None, prompt=None, *, host_id="local",
                       workspace_id=None, correlation_id=None):
        self.calls.append({
            "agent_type": agent_type, "host_id": host_id,
            "workspace_id": workspace_id, "correlation_id": correlation_id,
        })
        return ("", 0)


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
    yield {
        "db": db, "wm": wm, "ws": ws, "registry": registry,
        "host_repo": AgentHostRepo(db),
        "dispatch_repo": AgentDispatchRepo(db),
    }
    await db.close()


async def test_schema_default_agent_host_id_is_local(env):
    """The schema-level DEFAULT 'local' fills the column when omitted."""
    # Seed minimal design_doc so dev_works FK resolves.
    await env["db"].execute(
        "INSERT INTO design_docs(id,workspace_id,slug,version,path,status,"
        "created_at,published_at) "
        "VALUES('des-1', ?, 'd', '1.0.0', 'designs/d.md', 'published', 't', 't')",
        (env["ws"]["id"],),
    )
    await env["db"].execute(
        "INSERT INTO dev_works(id,workspace_id,design_doc_id,repo_path,prompt,"
        "current_step,iteration_rounds,agent,created_at,updated_at) "
        "VALUES('dw-1', ?, 'des-1', '/r', 'p', 'INIT', 0, 'codex', 't', 't')",
        (env["ws"]["id"],),
    )
    row = await env["db"].fetchone(
        "SELECT agent_host_id FROM dev_works WHERE id='dw-1'"
    )
    assert row["agent_host_id"] == "local"


async def test_pick_host_returns_remote_when_repo_routes_there(env):
    sm = DevWorkStateMachine(
        db=env["db"], workspaces=env["wm"],
        design_docs=None, iteration_notes=None,
        executor=CapturingExecutor(db=None, webhook_notifier=None),
        config=type("C", (), {"devwork": type("D", (), {})()})(),
        registry=env["registry"],
        agent_host_repo=env["host_repo"],
    )
    await env["host_repo"].upsert(id="ah-fast", host="dev@h", agent_type="codex")
    await env["host_repo"].update_health("ah-fast", status="healthy")
    chosen = await sm._pick_host("codex")
    assert chosen == "ah-fast"


async def test_open_close_dispatch_creates_lifecycle_row(env):
    sm = DevWorkStateMachine(
        db=env["db"], workspaces=env["wm"],
        design_docs=None, iteration_notes=None,
        executor=CapturingExecutor(db=None, webhook_notifier=None),
        config=type("C", (), {"devwork": type("D", (), {})()})(),
        registry=env["registry"],
        agent_dispatch_repo=env["dispatch_repo"],
    )
    ad_id = await sm._open_dispatch(
        host_id="local", workspace_id=env["ws"]["id"],
        correlation_id="dw-x", correlation_kind="dev_work",
    )
    assert ad_id is not None
    row = await env["dispatch_repo"].get(ad_id)
    assert row["state"] == "running"
    await sm._close_dispatch(ad_id, state="succeeded", exit_code=0)
    row = await env["dispatch_repo"].get(ad_id)
    assert row["state"] == "succeeded"
    assert row["exit_code"] == 0


async def test_open_dispatch_returns_none_without_repo(env):
    sm = DevWorkStateMachine(
        db=env["db"], workspaces=env["wm"],
        design_docs=None, iteration_notes=None,
        executor=CapturingExecutor(db=None, webhook_notifier=None),
        config=type("C", (), {"devwork": type("D", (), {})()})(),
        registry=env["registry"],
    )
    ad_id = await sm._open_dispatch(
        host_id="local", workspace_id=env["ws"]["id"],
        correlation_id="dw-x", correlation_kind="dev_work",
    )
    assert ad_id is None
