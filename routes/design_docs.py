"""DesignDoc read-only projection routes (Phase 5.5).

Endpoints:
    GET /api/v1/design-docs                 — list by workspace_id (REQUIRED)
    GET /api/v1/design-docs/{doc_id}        — fetch one row, 404 if missing
    GET /api/v1/design-docs/{doc_id}/content — stream markdown body

Read-only: NO writes, NO state machine ticks, NO event emission.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from src.exceptions import BadRequestError, NotFoundError
from src.models import DesignDoc
from src.storage.base import normalize_key

router = APIRouter(tags=["design-docs"])

_VALID_STATUSES = {"draft", "published", "superseded"}


def _row_to_design_doc(row: dict) -> DesignDoc:
    return DesignDoc(
        id=row["id"],
        workspace_id=row["workspace_id"],
        slug=row["slug"],
        version=row["version"],
        path=row["path"],
        parent_version=row.get("parent_version"),
        needs_frontend_mockup=bool(row.get("needs_frontend_mockup")),
        rubric_threshold=row["rubric_threshold"],
        status=row["status"],
        content_hash=row.get("content_hash"),
        byte_size=row.get("byte_size"),
        created_at=row["created_at"],
        published_at=row.get("published_at"),
    )


def _safe_resolve_under_root(
    raw_path: str, workspaces_root: Path, workspace_slug: str,
) -> Path:
    """Compose ``<workspaces_root>/<slug>/<raw_path>`` and validate under root.

    Phase 3: ``raw_path`` is workspace-relative POSIX. ``normalize_key``
    rejects absolute paths, backslashes, drive letters, and '..' traversal
    before composition; the ``relative_to`` check is defence-in-depth against
    symlink-based escapes.
    """
    rel = normalize_key(raw_path).as_posix()
    root = workspaces_root.resolve()
    resolved = (root / workspace_slug / rel).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BadRequestError(
            "design doc path escapes workspaces_root"
        ) from exc
    return resolved


@router.get("/design-docs")
async def list_design_docs(
    request: Request,
    workspace_id: str,
    status: str | None = None,
) -> list[DesignDoc]:
    if status is not None and status not in _VALID_STATUSES:
        raise BadRequestError(
            f"status must be one of {sorted(_VALID_STATUSES)}"
        )
    db = request.app.state.db
    if status is None:
        rows = await db.fetchall(
            "SELECT * FROM design_docs WHERE workspace_id=? "
            "ORDER BY created_at DESC",
            (workspace_id,),
        )
    else:
        rows = await db.fetchall(
            "SELECT * FROM design_docs WHERE workspace_id=? AND status=? "
            "ORDER BY created_at DESC",
            (workspace_id, status),
        )
    return [_row_to_design_doc(r) for r in rows]


@router.get("/design-docs/{doc_id}")
async def get_design_doc(doc_id: str, request: Request) -> DesignDoc:
    db = request.app.state.db
    row = await db.fetchone(
        "SELECT * FROM design_docs WHERE id=?", (doc_id,)
    )
    if not row:
        raise NotFoundError(f"design_doc {doc_id!r} not found")
    return _row_to_design_doc(row)


@router.get("/design-docs/{doc_id}/content")
async def get_design_doc_content(doc_id: str, request: Request):
    db = request.app.state.db
    row = await db.fetchone(
        "SELECT path, workspace_id FROM design_docs WHERE id=?", (doc_id,)
    )
    if not row:
        raise NotFoundError(f"design_doc {doc_id!r} not found")
    ws = await db.fetchone(
        "SELECT slug FROM workspaces WHERE id=?", (row["workspace_id"],)
    )
    if not ws:
        raise NotFoundError(
            f"workspace {row['workspace_id']!r} not found for design_doc"
        )

    workspaces_root = request.app.state.settings.security.resolved_workspace_root()
    resolved = _safe_resolve_under_root(
        row["path"], workspaces_root, ws["slug"]
    )

    if not resolved.exists() or not resolved.is_file():
        # Distinguish "no such DB row" (404) from "row points at a deleted file"
        # so the UI can prompt a reconcile.
        raise HTTPException(
            status_code=410,
            detail=f"design_doc {doc_id!r} file is missing on disk",
        )
    return FileResponse(
        path=str(resolved),
        media_type="text/markdown; charset=utf-8",
    )
