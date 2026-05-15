"""DevWork iteration-note read-only projection routes (Phase 5.5).

Endpoints:
    GET /api/v1/dev-works/{dev_id}/iteration-notes
        — list notes for a DevWork; 404 if DevWork unknown.
    GET /api/v1/dev-iteration-notes/{note_id}/content
        — stream the markdown body of a single iteration note.
    GET /api/v1/dev-works/{dev_id}/context/{round_n}/content
        — stream the Step3 context markdown for a DevWork round.

Read-only by contract.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from src.exceptions import BadRequestError, NotFoundError
from src.models import DevIterationNote
from src.storage.base import normalize_key

router = APIRouter(tags=["dev-iteration-notes"])


def _row_to_note(row: dict) -> DevIterationNote:
    score_history: list[int] | None = None
    raw = row.get("score_history_json")
    if raw:
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, list):
                score_history = [int(x) for x in decoded]
        except (ValueError, TypeError):
            score_history = None
    return DevIterationNote(
        id=row["id"],
        dev_work_id=row["dev_work_id"],
        round=row["round"],
        markdown_path=row["markdown_path"],
        score_history=score_history,
        created_at=row["created_at"],
    )


def _safe_resolve_under_root(
    raw_path: str, workspaces_root: Path, workspace_slug: str,
) -> Path:
    """Compose ``<workspaces_root>/<slug>/<raw_path>`` and validate under root.

    ``normalize_key`` rejects absolute paths, backslashes, drive letters, and
    '..' traversal before composition; the ``relative_to`` check is
    defence-in-depth against symlink-based escapes.
    """
    rel = normalize_key(raw_path).as_posix()
    root = workspaces_root.resolve()
    resolved = (root / workspace_slug / rel).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BadRequestError(
            "iteration note path escapes workspaces_root"
        ) from exc
    return resolved


async def _workspace_slug_for_note(db, note_id: str) -> str | None:
    row = await db.fetchone(
        "SELECT w.slug AS slug "
        "FROM dev_iteration_notes n "
        "JOIN dev_works d ON d.id = n.dev_work_id "
        "JOIN workspaces w ON w.id = d.workspace_id "
        "WHERE n.id=?",
        (note_id,),
    )
    return row["slug"] if row else None


async def _workspace_for_dev_work(db, dev_id: str) -> dict | None:
    return await db.fetchone(
        "SELECT d.id AS dev_work_id, d.workspace_id AS workspace_id, "
        "w.slug AS slug "
        "FROM dev_works d "
        "JOIN workspaces w ON w.id = d.workspace_id "
        "WHERE d.id=?",
        (dev_id,),
    )


@router.get("/dev-works/{dev_id}/iteration-notes")
async def list_iteration_notes(
    dev_id: str, request: Request
) -> list[DevIterationNote]:
    db = request.app.state.db
    # Distinguish "unknown DevWork" (404) from "DevWork has no notes" (200 [])
    dev_row = await db.fetchone(
        "SELECT id FROM dev_works WHERE id=?", (dev_id,)
    )
    if not dev_row:
        raise NotFoundError(f"dev_work {dev_id!r} not found")

    rows = await db.fetchall(
        "SELECT * FROM dev_iteration_notes WHERE dev_work_id=? "
        "ORDER BY round ASC",
        (dev_id,),
    )
    return [_row_to_note(r) for r in rows]


@router.get("/dev-iteration-notes/{note_id}/content")
async def get_iteration_note_content(note_id: str, request: Request):
    db = request.app.state.db
    row = await db.fetchone(
        "SELECT * FROM dev_iteration_notes WHERE id=?", (note_id,)
    )
    if not row:
        raise NotFoundError(f"dev_iteration_note {note_id!r} not found")

    slug = await _workspace_slug_for_note(db, note_id)
    if slug is None:
        raise NotFoundError(
            f"workspace for dev_iteration_note {note_id!r} not found"
        )
    workspaces_root = request.app.state.settings.security.resolved_workspace_root()
    resolved = _safe_resolve_under_root(
        row["markdown_path"], workspaces_root, slug,
    )

    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(
            status_code=410,
            detail=f"dev_iteration_note {note_id!r} file is missing on disk",
        )
    return FileResponse(
        path=str(resolved),
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/dev-works/{dev_id}/context/{round_n}/content")
async def get_dev_work_context_content(
    dev_id: str, round_n: int, request: Request,
):
    if round_n < 1:
        raise BadRequestError("round_n must be >= 1")

    db = request.app.state.db
    dev_row = await _workspace_for_dev_work(db, dev_id)
    if dev_row is None:
        raise NotFoundError(f"dev_work {dev_id!r} not found")

    rel_path = f"devworks/{dev_id}/context/ctx-round-{round_n}.md"
    file_row = await db.fetchone(
        "SELECT relative_path FROM workspace_files "
        "WHERE workspace_id=? AND relative_path=? AND kind='context'",
        (dev_row["workspace_id"], rel_path),
    )
    if file_row is None:
        raise NotFoundError(
            f"Step3 context for dev_work {dev_id!r} round {round_n} not found"
        )

    workspaces_root = request.app.state.settings.security.resolved_workspace_root()
    resolved = _safe_resolve_under_root(
        file_row["relative_path"], workspaces_root, dev_row["slug"],
    )
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(
            status_code=410,
            detail=(
                f"Step3 context for dev_work {dev_id!r} round {round_n} "
                "is missing on disk"
            ),
        )
    return FileResponse(
        path=str(resolved),
        media_type="text/markdown; charset=utf-8",
    )
