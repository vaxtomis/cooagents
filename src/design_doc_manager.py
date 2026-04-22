"""DesignDoc persistence — D6 PERSIST and D7 COMPLETED side-effects.

Responsibilities:
  * Given a workspace + slug + version + markdown body, write the file to
    ``<workspace_root>/designs/DES-<slug>-<version>.md`` (creating parent
    dirs under the workspaces_root invariant).
  * Compute content_hash + byte_size and INSERT a ``design_docs`` row.
  * Publish transition: UPDATE status='published' and link
    design_works.output_design_doc_id.

All DB writes use ``db.transaction()`` so the INSERT and the pointer UPDATE
cannot diverge.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.exceptions import BadRequestError, ConflictError, NotFoundError

logger = logging.getLogger(__name__)


class DesignDocManager:
    def __init__(self, db, workspaces_root: Path | str):
        self.db = db
        self.workspaces_root = Path(workspaces_root).expanduser().resolve()

    @staticmethod
    def _new_id() -> str:
        return f"des-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _doc_path(self, workspace_row: dict, slug: str, version: str) -> Path:
        # Always derive under workspaces_root/<workspace_slug>/designs/
        slug_dir = self.workspaces_root / workspace_row["slug"]
        target = slug_dir / "designs" / f"DES-{slug}-{version}.md"
        resolved = target.resolve()
        try:
            resolved.relative_to(self.workspaces_root)
        except ValueError as exc:
            raise BadRequestError(
                f"design doc path escapes workspaces_root: {target}"
            ) from exc
        return target

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
        """D6 PERSIST — write file + INSERT design_docs (status='draft').

        Returns the new row as dict.
        """
        if await self.db.fetchone(
            "SELECT id FROM design_docs WHERE workspace_id=? AND slug=? AND version=?",
            (workspace_row["id"], slug, version),
        ):
            raise ConflictError(
                f"design doc {slug}-{version} already exists in workspace "
                f"{workspace_row['slug']}"
            )

        target = self._doc_path(workspace_row, slug, version)
        target.parent.mkdir(parents=True, exist_ok=True)

        # FS write first — schema UNIQUE prevents double-INSERT on retry.
        # Write as bytes to avoid Windows text-mode newline translation
        # (\n -> \r\n), which would make content_hash differ from the
        # SHA256 of the original string and confuse reproducibility checks.
        encoded = markdown.encode("utf-8")
        target.write_bytes(encoded)

        content_hash = hashlib.sha256(encoded).hexdigest()
        byte_size = len(encoded)
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
                    str(target),
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
                target.unlink(missing_ok=True)
            except OSError:
                logger.exception("failed to clean design doc at %s", target)
            raise

        return {
            "id": did,
            "workspace_id": workspace_row["id"],
            "slug": slug,
            "version": version,
            "path": str(target),
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
        """D7 COMPLETED — UPDATE status=published + link from design_work.

        Both writes in one transaction to avoid a published doc without a
        DesignWork pointer (which would leak in Phase 6 UI lookups).
        """
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
