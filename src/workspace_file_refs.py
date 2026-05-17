"""Workspace file reference helpers.

``workspace_files`` is the authoritative file index. This module owns the
small policy layer for files that DesignWork / DevWork may explicitly cite.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.exceptions import BadRequestError
from src.storage.base import normalize_key

SELECTABLE_WORKSPACE_FILE_KINDS: frozenset[str] = frozenset({
    "attachment",
    "image",
    "context",
    "artifact",
    "feedback",
    "other",
})
PROTECTED_WORKSPACE_FILE_KINDS: frozenset[str] = frozenset({
    "workspace_md",
    "design_doc",
    "design_input",
    "prompt",
    "iteration_note",
})
VALID_REFERRER_KINDS: frozenset[str] = frozenset({"design_work", "dev_work"})
TEXT_PROMPT_SUFFIXES: frozenset[str] = frozenset({".md", ".txt"})


@dataclass(frozen=True)
class WorkspacePromptFile:
    relative_path: str
    kind: str
    byte_size: int | None
    absolute_path: str
    content: str | None = None
    truncated: bool = False
    original_chars: int | None = None


def normalize_workspace_file_refs(paths: Sequence[str] | None) -> list[str]:
    """Normalize, deduplicate, and preserve caller order."""
    if not paths:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for path in paths:
        rel = normalize_key(path).as_posix()
        if rel in seen:
            continue
        normalized.append(rel)
        seen.add(rel)
    return normalized


def merge_workspace_file_ref_paths(*groups: Sequence[str] | None) -> list[str]:
    merged: list[str] = []
    for group in groups:
        merged.extend(group or [])
    return normalize_workspace_file_refs(merged)


def is_selectable_workspace_file_kind(kind: str) -> bool:
    return kind in SELECTABLE_WORKSPACE_FILE_KINDS


def is_protected_workspace_file_kind(kind: str) -> bool:
    return kind in PROTECTED_WORKSPACE_FILE_KINDS


async def validate_workspace_file_refs(
    db: Any, *, workspace_id: str, paths: Sequence[str] | None
) -> list[dict[str, Any]]:
    """Return matching workspace_files rows or raise a request error."""
    normalized = normalize_workspace_file_refs(paths)
    if not normalized:
        return []
    placeholders = ",".join("?" for _ in normalized)
    rows = await db.fetchall(
        "SELECT * FROM workspace_files "
        f"WHERE workspace_id=? AND relative_path IN ({placeholders})",
        (workspace_id, *normalized),
    )
    by_path = {row["relative_path"]: row for row in rows}
    missing = [path for path in normalized if path not in by_path]
    if missing:
        raise BadRequestError(
            f"workspace_file_refs not found in workspace: {missing}"
        )
    blocked = [
        f"{path} ({by_path[path]['kind']})"
        for path in normalized
        if not is_selectable_workspace_file_kind(by_path[path]["kind"])
    ]
    if blocked:
        raise BadRequestError(
            "workspace_file_refs entries must be selectable workspace files; "
            f"blocked: {blocked}"
        )
    return [by_path[path] for path in normalized]


async def insert_workspace_file_refs(
    db: Any,
    *,
    workspace_id: str,
    referrer_kind: str,
    referrer_id: str,
    relative_paths: Sequence[str],
    created_at: str | None = None,
) -> None:
    if referrer_kind not in VALID_REFERRER_KINDS:
        raise BadRequestError(f"invalid referrer_kind={referrer_kind!r}")
    now = created_at or datetime.now(timezone.utc).isoformat()
    for rel in normalize_workspace_file_refs(relative_paths):
        await db.execute(
            "INSERT OR IGNORE INTO workspace_file_refs "
            "(id, workspace_id, relative_path, referrer_kind, referrer_id, "
            "created_at) VALUES(?,?,?,?,?,?)",
            (
                f"wfr-{uuid.uuid4().hex[:12]}",
                workspace_id,
                rel,
                referrer_kind,
                referrer_id,
                now,
            ),
        )


async def delete_workspace_file_refs_for_referrer(
    db: Any, *, referrer_kind: str, referrer_id: str
) -> None:
    await db.execute(
        "DELETE FROM workspace_file_refs "
        "WHERE referrer_kind=? AND referrer_id=?",
        (referrer_kind, referrer_id),
    )


async def list_workspace_file_ref_rows(
    db: Any, *, referrer_kind: str, referrer_id: str
) -> list[dict[str, Any]]:
    return await db.fetchall(
        "SELECT wf.* FROM workspace_file_refs wfr "
        "JOIN workspace_files wf ON wf.workspace_id=wfr.workspace_id "
        "AND wf.relative_path=wfr.relative_path "
        "WHERE wfr.referrer_kind=? AND wfr.referrer_id=? "
        "ORDER BY wfr.created_at, wfr.relative_path",
        (referrer_kind, referrer_id),
    )


async def list_workspace_file_ref_paths(
    db: Any, *, referrer_kind: str, referrer_id: str
) -> list[str]:
    rows = await list_workspace_file_ref_rows(
        db, referrer_kind=referrer_kind, referrer_id=referrer_id
    )
    return [row["relative_path"] for row in rows]


async def list_workspace_file_ref_paths_batch(
    db: Any, *, referrer_kind: str, referrer_ids: Sequence[str]
) -> dict[str, list[str]]:
    if not referrer_ids:
        return {}
    placeholders = ",".join("?" for _ in referrer_ids)
    rows = await db.fetchall(
        "SELECT referrer_id, relative_path FROM workspace_file_refs "
        f"WHERE referrer_kind=? AND referrer_id IN ({placeholders}) "
        "ORDER BY referrer_id, created_at, relative_path",
        (referrer_kind, *referrer_ids),
    )
    grouped: dict[str, list[str]] = {referrer_id: [] for referrer_id in referrer_ids}
    for row in rows:
        grouped.setdefault(row["referrer_id"], []).append(row["relative_path"])
    return grouped


async def list_references_to_workspace_file(
    db: Any, *, workspace_id: str, relative_path: str
) -> list[dict[str, Any]]:
    rel = normalize_key(relative_path).as_posix()
    return await db.fetchall(
        "SELECT referrer_kind, referrer_id, created_at "
        "FROM workspace_file_refs "
        "WHERE workspace_id=? AND relative_path=? "
        "ORDER BY created_at, referrer_kind, referrer_id",
        (workspace_id, rel),
    )


async def load_workspace_prompt_files(
    *,
    registry: Any,
    workspace_row: dict[str, Any],
    file_rows: Iterable[dict[str, Any]],
    abs_for: Any,
    max_each_chars: int,
    max_total_chars: int,
) -> list[WorkspacePromptFile]:
    remaining = max_total_chars
    prompt_files: list[WorkspacePromptFile] = []
    for row in file_rows:
        rel = row["relative_path"]
        content: str | None = None
        truncated = False
        original_chars: int | None = None
        suffix = Path(rel).suffix.lower()
        if suffix in TEXT_PROMPT_SUFFIXES and remaining > 0:
            try:
                raw = await registry.read_text(
                    workspace_slug=workspace_row["slug"],
                    relative_path=rel,
                )
                original_chars = len(raw)
                allowed = min(max_each_chars, remaining)
                content = raw[:allowed]
                truncated = len(raw) > allowed
                remaining -= len(content)
            except UnicodeDecodeError:
                content = None
            except Exception:
                content = f"[Workspace file {rel!r} could not be read.]"
        prompt_files.append(
            WorkspacePromptFile(
                relative_path=rel,
                kind=row["kind"],
                byte_size=row.get("byte_size"),
                absolute_path=abs_for(workspace_row, rel),
                content=content,
                truncated=truncated,
                original_chars=original_chars,
            )
        )
    return prompt_files
