"""Helpers for DesignWork supplemental attachments."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from src.exceptions import BadRequestError
from src.storage.base import normalize_key

MAX_DESIGN_ATTACHMENTS = 10
ALLOWED_DESIGN_ATTACHMENT_EXTENSIONS = {
    "jpg",
    "jpeg",
    "md",
    "pdf",
    "png",
    "xls",
    "xlsx",
}

_SAFE_STEM_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_attachment_stem(filename: str) -> str:
    """Return a filesystem-friendly stem derived from an uploaded filename."""
    stem = Path(filename or "attachment").stem.strip()
    safe = _SAFE_STEM_RE.sub("-", stem).strip(".-_")
    return safe[:80] or "attachment"


def validate_attachment_path(relative_path: str) -> str:
    """Normalize and constrain a DesignWork attachment path.

    Attachments must live under ``attachments/``. DesignWork creation accepts
    paths instead of raw file bodies, so this guard prevents arbitrary
    workspace files from being pulled into LLM prompts.
    """
    rel = normalize_key(relative_path).as_posix()
    suffix = Path(rel).suffix.lstrip(".").lower()
    if (
        not rel.startswith("attachments/")
        or suffix not in ALLOWED_DESIGN_ATTACHMENT_EXTENSIONS
    ):
        raise BadRequestError(
            "attachment_paths entries must be supported files under attachments/"
        )
    return rel


def validate_attachment_paths(paths: Sequence[str] | None) -> list[str]:
    if not paths:
        return []
    if len(paths) > MAX_DESIGN_ATTACHMENTS:
        raise BadRequestError(
            f"at most {MAX_DESIGN_ATTACHMENTS} attachments are allowed"
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for path in paths:
        rel = validate_attachment_path(path)
        if rel in seen:
            continue
        normalized.append(rel)
        seen.add(rel)
    return normalized
