"""DesignDoc persistence — D6 PERSIST and D7 COMPLETED side-effects.

Responsibilities:
  * Given a workspace + slug + version + markdown body, route the bytes + DB
    metadata through ``WorkspaceFileRegistry`` so the file lands at
    ``<workspaces_root>/<slug>/designs/DES-<slug>-<version>.md`` and a row in
    ``workspace_files`` tracks its ``content_hash`` / ``byte_size`` /
    ``local_mtime_ns``.
  * INSERT a ``design_docs`` row with the workspace-relative ``path``.
  * Publish transition: UPDATE status='published' and link
    design_works.output_design_doc_id.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from src.exceptions import ConflictError, NotFoundError
from src.storage.registry import WorkspaceFileRegistry

logger = logging.getLogger(__name__)


class DesignDocManager:
    def __init__(self, db, registry: WorkspaceFileRegistry) -> None:
        self.db = db
        self.registry = registry

    @staticmethod
    def _new_id() -> str:
        return f"des-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _relative_path(slug: str, version: str) -> str:
        return f"designs/DES-{slug}-{version}.md"

    async def persist(
        self,
        *,
        workspace_row: dict,
        slug: str,
        version: str,
        markdown: str,
        parent_version: str | None,
        needs_frontend_mockup: bool,
        rubric_threshold: int,
    ) -> dict:
        """D6 PERSIST — write file via registry + INSERT design_docs (draft)."""
        if await self.db.fetchone(
            "SELECT id FROM design_docs WHERE workspace_id=? AND slug=? AND version=?",
            (workspace_row["id"], slug, version),
        ):
            raise ConflictError(
                f"design doc {slug}-{version} already exists in workspace "
                f"{workspace_row['slug']}"
            )

        rel = self._relative_path(slug, version)
        # Registry handles: encode UTF-8, compute sha256, write via FileStore,
        # upsert workspace_files row with content_hash/byte_size/mtime_ns.
        wf_row = await self.registry.put_markdown(
            workspace_row=workspace_row,
            relative_path=rel,
            text=markdown,
            kind="design_doc",
        )
        content_hash = wf_row["content_hash"]
        byte_size = wf_row["byte_size"]

        did = self._new_id()
        now = self._now()
        try:
            await self.db.execute(
                """INSERT INTO design_docs
                   (id, workspace_id, slug, version, path, parent_version,
                    needs_frontend_mockup, rubric_threshold, status,
                    content_hash, byte_size, created_at, published_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                (
                    did,
                    workspace_row["id"],
                    slug,
                    version,
                    rel,
                    parent_version,
                    1 if needs_frontend_mockup else 0,
                    rubric_threshold,
                    "draft",
                    content_hash,
                    byte_size,
                    now,
                ),
            )
        except Exception:
            try:
                await self.registry.delete(
                    workspace_row=workspace_row, relative_path=rel,
                )
            except Exception:
                logger.exception(
                    "failed to roll back design doc at %s/%s",
                    workspace_row["slug"], rel,
                )
            raise

        return {
            "id": did,
            "workspace_id": workspace_row["id"],
            "slug": slug,
            "version": version,
            "path": rel,
            "parent_version": parent_version,
            "needs_frontend_mockup": needs_frontend_mockup,
            "rubric_threshold": rubric_threshold,
            "status": "draft",
            "content_hash": content_hash,
            "byte_size": byte_size,
            "created_at": now,
            "published_at": None,
        }

    async def publish(self, design_doc_id: str, design_work_id: str) -> None:
        """D7 COMPLETED — UPDATE status=published + link from design_work."""
        now = self._now()
        async with self.db.transaction():
            updated = await self.db.execute_rowcount(
                "UPDATE design_docs SET status='published', published_at=? "
                "WHERE id=? AND status='draft'",
                (now, design_doc_id),
            )
            if updated == 0:
                raise NotFoundError(
                    f"design_doc {design_doc_id} not found or already published"
                )
            await self.db.execute(
                "UPDATE design_works SET output_design_doc_id=?, updated_at=? "
                "WHERE id=?",
                (design_doc_id, now, design_work_id),
            )
