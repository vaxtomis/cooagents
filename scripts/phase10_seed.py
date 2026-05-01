#!/usr/bin/env python3
"""Phase 10 real-mode seed helper.

Runs **inside the cooagents venv** on the operator host. Seeds the
fixtures the real-mode harness needs:

  - 1 workspace (slug = phase10)
  - 1 published design_doc (uses the same fixture as the test suite)
  - 2 git repos with bare clones (frontend + backend)
  - registers both repos in the registry as ``healthy``

Prints ``WS_ID DD_ID FE_ID BE_ID`` on stdout (one per line, key=val
form). The wrapper SSH command captures these and feeds them to the
DevWork POST.

Idempotent: if ``slug=phase10`` already exists, reuses it.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Project root must come first so ``src.*`` resolves before any installed
# package shadows it.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from src.database import Database
from src.design_doc_manager import DesignDocManager
from src.git_utils import run_git
from src.repos.registry import RepoRegistryRepo
from src.storage import LocalFileStore
from src.storage.registry import WorkspaceFileRegistry, WorkspaceFilesRepo
from src.workspace_manager import WorkspaceManager

DESIGN_FIXTURE = _ROOT / "tests" / "fixtures" / "design" / "perfect" / "round1.md"


async def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    await run_git("init", cwd=str(path))
    await run_git("config", "user.email", "phase10@example.com", cwd=str(path))
    await run_git("config", "user.name", "phase10-seed", cwd=str(path))
    await run_git("checkout", "-b", "main", cwd=str(path), check=False)
    (path / "README.md").write_text("# phase10 demo repo\n")
    await run_git("add", "README.md", cwd=str(path))
    await run_git("commit", "-m", "init", cwd=str(path))


async def _seed_repo(
    db: Database, ws_root: Path, repo_dir: Path, *,
    repo_id: str, role: str,
) -> None:
    bare_dir = ws_root / ".coop" / "registry" / "repos" / f"{repo_id}.git"
    bare_dir.parent.mkdir(parents=True, exist_ok=True)
    if not bare_dir.exists():
        await run_git("clone", "--bare", str(repo_dir), str(bare_dir))
    rr = RepoRegistryRepo(db)
    existing = await rr.get(repo_id)
    if existing is None:
        await rr.upsert(
            id=repo_id, name=repo_id, url=str(repo_dir),
            default_branch="main", bare_clone_path=str(bare_dir),
            role=role,
        )
    await rr.update_fetch_status(
        repo_id, status="healthy", bare_clone_path=str(bare_dir),
    )


async def main() -> int:
    db_path = os.environ.get("COOAGENTS_DB_PATH", ".coop/state.db")
    ws_root_env = os.environ.get(
        "COOAGENTS_WORKSPACES_ROOT",
        str(_ROOT / ".coop" / "workspaces"),
    )
    ws_root = Path(ws_root_env)
    ws_root.mkdir(parents=True, exist_ok=True)

    db = Database(db_path=Path(db_path), schema_path=str(_ROOT / "db" / "schema.sql"))
    await db.connect()
    try:
        store = LocalFileStore(workspaces_root=ws_root)
        repo = WorkspaceFilesRepo(db)
        registry = WorkspaceFileRegistry(store=store, repo=repo)
        wm = WorkspaceManager(
            db, project_root=_ROOT, workspaces_root=ws_root,
            registry=registry,
        )
        ddm = DesignDocManager(db, registry=registry)

        # Workspace.
        existing = await db.fetchone(
            "SELECT * FROM workspaces WHERE slug=?", ("phase10",)
        )
        if existing is None:
            ws = await wm.create_with_scaffold(title="phase10", slug="phase10")
        else:
            ws = dict(existing)

        # Design doc.
        dd_existing = await db.fetchone(
            "SELECT * FROM design_docs WHERE workspace_id=? AND slug=? "
            "AND version=?",
            (ws["id"], "demo", "1.0.0"),
        )
        if dd_existing is None:
            design_text = DESIGN_FIXTURE.read_text(encoding="utf-8")
            dd = await ddm.persist(
                workspace_row=ws, slug="demo", version="1.0.0",
                markdown=design_text, parent_version=None,
                needs_frontend_mockup=False, rubric_threshold=85,
            )
            await db.execute(
                "UPDATE design_docs SET status='published', published_at=? "
                "WHERE id=?",
                ("2026-04-30T00:00:00+00:00", dd["id"]),
            )
        else:
            dd = dict(dd_existing)
            if dd["status"] != "published":
                await db.execute(
                    "UPDATE design_docs SET status='published', "
                    "published_at=? WHERE id=?",
                    ("2026-04-30T00:00:00+00:00", dd["id"]),
                )

        # Two repos with bare clones.
        repo_fe_dir = _ROOT / ".coop" / "phase10-src" / "repo_fe"
        repo_be_dir = _ROOT / ".coop" / "phase10-src" / "repo_be"
        for d in (repo_fe_dir, repo_be_dir):
            if not (d / ".git").exists():
                await _init_repo(d)
        fe_id = "repo-phase10fe01"
        be_id = "repo-phase10be01"
        await _seed_repo(db, ws_root, repo_fe_dir,
                         repo_id=fe_id, role="frontend")
        await _seed_repo(db, ws_root, repo_be_dir,
                         repo_id=be_id, role="backend")

        print(f"WS_ID={ws['id']}")
        print(f"DD_ID={dd['id']}")
        print(f"FE_ID={fe_id}")
        print(f"BE_ID={be_id}")
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
