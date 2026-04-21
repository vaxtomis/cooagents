"""Workspace CRUD — Phase 1 DB layer only.

Filesystem scaffolding (workspace.md, designs/, devworks/) lands in Phase 2.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path


class WorkspaceManager:
    def __init__(self, db, project_root=None):
        self.db = db
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]

    @staticmethod
    def _new_id() -> str:
        return f"ws-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def create(self, title: str, slug: str, root_path: str) -> str:
        wid = self._new_id()
        now = self._now()
        await self.db.execute(
            """INSERT INTO workspaces(id, title, slug, status, root_path, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (wid, title, slug, "active", root_path, now, now),
        )
        return wid

    async def get(self, workspace_id: str) -> dict | None:
        return await self.db.fetchone(
            "SELECT * FROM workspaces WHERE id=?", (workspace_id,)
        )

    async def get_by_slug(self, slug: str) -> dict | None:
        return await self.db.fetchone(
            "SELECT * FROM workspaces WHERE slug=?", (slug,)
        )

    async def list(self, status: str | None = None) -> list[dict]:
        if status:
            return await self.db.fetchall(
                "SELECT * FROM workspaces WHERE status=? ORDER BY created_at DESC",
                (status,),
            )
        return await self.db.fetchall(
            "SELECT * FROM workspaces ORDER BY created_at DESC"
        )

    async def archive(self, workspace_id: str) -> int:
        now = self._now()
        return await self.db.execute_rowcount(
            "UPDATE workspaces SET status='archived', updated_at=? WHERE id=? AND status='active'",
            (now, workspace_id),
        )
