"""Pre-execution drift scan: compare local FS against the cooagents
``workspace_files`` index.

Three categories:

* ``local_only``       — file exists under ``<root>/<slug>/`` but no DB row.
* ``db_only_missing``  — DB row exists but file is missing locally → must be
  rematerialised before acpx runs.
* ``hash_mismatch``    — both sides exist but local hash diverges from the
  DB row. The worker treats this as a fail-closed condition (exit 2) so we
  never silently overwrite operator changes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class RecoveryReport:
    workspace_id: str
    slug: str
    local_only: list[str] = field(default_factory=list)
    db_only_missing: list[str] = field(default_factory=list)
    hash_mismatch: list[str] = field(default_factory=list)

    @property
    def has_blocking_drift(self) -> bool:
        """Hash mismatch is the only category that blocks worker startup.

        ``local_only`` is informational (operator may have dropped extra
        files); ``db_only_missing`` is healed by materialize.
        """
        return bool(self.hash_mismatch)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def recovery_scan(
    *,
    workspace_root: Path,
    workspace_id: str,
    slug: str,
    files_index: Iterable[dict[str, Any]],
) -> RecoveryReport:
    """Walk ``<workspace_root>/<slug>/`` and classify every path against the
    DB-side index.

    ``files_index`` is a sequence of ``workspace_files`` rows (each a dict
    with at least ``relative_path`` and ``content_hash``).
    """
    slug_root = (workspace_root / slug).resolve()
    index_by_path: dict[str, dict[str, Any]] = {
        row["relative_path"]: row for row in files_index
    }
    local_only: list[str] = []
    hash_mismatch: list[str] = []
    seen_in_index: set[str] = set()

    if slug_root.exists():
        for entry in slug_root.rglob("*"):
            if not entry.is_file():
                continue
            rel = entry.relative_to(slug_root).as_posix()
            row = index_by_path.get(rel)
            if row is None:
                local_only.append(rel)
                continue
            seen_in_index.add(rel)
            expected_hash = row.get("content_hash")
            if expected_hash is None:
                # Pre-Phase 8 row that was never hashed; treat as match.
                continue
            actual_hash = _hash_file(entry)
            if actual_hash != expected_hash:
                hash_mismatch.append(rel)

    db_only_missing = [
        rel for rel in index_by_path if rel not in seen_in_index
    ]
    return RecoveryReport(
        workspace_id=workspace_id,
        slug=slug,
        local_only=sorted(local_only),
        db_only_missing=sorted(db_only_missing),
        hash_mismatch=sorted(hash_mismatch),
    )
