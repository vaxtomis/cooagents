"""OSS → local-FS hydration for the agent worker.

For each row in the workspace_files index that is missing locally or whose
hash diverges, fetch the bytes from OSS, atomically write to the local
working tree, and re-verify SHA-256. Any mismatch fails closed.
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

logger = logging.getLogger(__name__)


class _ByteSource(Protocol):
    """Minimal contract — just enough to read OSS object bytes by key.

    The worker uses ``OSSFileStore`` from the main repo; tests substitute a
    ``LocalFileStore`` over a temp dir.
    """

    async def get_bytes(self, key: str) -> bytes: ...


@dataclass(frozen=True)
class MaterializeReport:
    pulled: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


async def materialize(
    *,
    store: _ByteSource,
    workspace_root: Path,
    slug: str,
    files_index: Iterable[dict[str, Any]],
    paths_to_pull: Iterable[str] | None = None,
) -> MaterializeReport:
    """Pull the listed paths from OSS into the local working tree.

    ``paths_to_pull`` lets the recovery scan narrow the work to the rows
    that actually need pulling. When ``None``, every row in ``files_index``
    is hydrated (used by first-time setup).
    """
    slug_root = (workspace_root / slug).resolve()
    index_by_path = {row["relative_path"]: row for row in files_index}
    targets = (
        list(paths_to_pull) if paths_to_pull is not None
        else list(index_by_path)
    )
    report_pulled: list[str] = []
    report_skipped: list[str] = []
    report_failed: dict[str, str] = {}
    for rel in targets:
        row = index_by_path.get(rel)
        if row is None:
            report_failed[rel] = "not_in_index"
            continue
        # Compose store key the same way the cooagents registry does.
        store_key = f"{slug}/{rel}"
        try:
            data = await store.get_bytes(store_key)
        except Exception as exc:
            logger.exception("materialize: get_bytes failed for %s", store_key)
            report_failed[rel] = f"get_bytes_failed: {type(exc).__name__}"
            continue
        expected_hash = row.get("content_hash")
        if expected_hash:
            actual_hash = hashlib.sha256(data).hexdigest()
            if actual_hash != expected_hash:
                report_failed[rel] = (
                    f"hash_mismatch: oss={actual_hash} db={expected_hash}"
                )
                continue
        target = (slug_root / rel).resolve()
        try:
            target.relative_to(slug_root)
        except ValueError:
            report_failed[rel] = "path_escapes_slug_root"
            continue
        try:
            _atomic_write(target, data)
        except Exception as exc:
            logger.exception("materialize: local write failed for %s", target)
            report_failed[rel] = f"write_failed: {type(exc).__name__}"
            continue
        report_pulled.append(rel)

    # Anything in the index that wasn't pulled and wasn't an explicit target
    # is a no-op (already-on-disk-and-matching) — record it as skipped.
    if paths_to_pull is None:
        report_skipped = []
    else:
        explicit = set(targets)
        report_skipped = [
            rel for rel in index_by_path if rel not in explicit
        ]
    return MaterializeReport(
        pulled=sorted(report_pulled),
        skipped=sorted(report_skipped),
        failed=dict(sorted(report_failed.items())),
    )
