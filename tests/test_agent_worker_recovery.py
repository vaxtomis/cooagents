"""Phase 8b: ``recovery_scan`` drift classifier."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.agent_worker.recovery import recovery_scan


def _hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def slug_root(tmp_path: Path) -> Path:
    """Create ``<tmp>/ws/slug/`` and return the slug-level root."""
    root = tmp_path / "ws" / "slug"
    root.mkdir(parents=True)
    return root


def _write(slug_root: Path, rel: str, data: bytes) -> None:
    target = slug_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def test_pristine_workspace_no_drift(slug_root: Path):
    _write(slug_root, "a.md", b"v1")
    index = [{"relative_path": "a.md", "content_hash": _hash(b"v1")}]
    rep = recovery_scan(
        workspace_root=slug_root.parent,
        workspace_id="ws-1", slug="slug",
        files_index=index,
    )
    assert rep.local_only == []
    assert rep.db_only_missing == []
    assert rep.hash_mismatch == []
    assert rep.has_blocking_drift is False


def test_local_only_listed_but_not_blocking(slug_root: Path):
    _write(slug_root, "a.md", b"v1")
    _write(slug_root, "extra.md", b"surprise")
    index = [{"relative_path": "a.md", "content_hash": _hash(b"v1")}]
    rep = recovery_scan(
        workspace_root=slug_root.parent,
        workspace_id="ws-1", slug="slug",
        files_index=index,
    )
    assert rep.local_only == ["extra.md"]
    assert rep.has_blocking_drift is False


def test_db_only_missing_listed(slug_root: Path):
    # b.md is in the DB index but not on disk.
    index = [
        {"relative_path": "a.md", "content_hash": None},
        {"relative_path": "b.md", "content_hash": _hash(b"v1")},
    ]
    rep = recovery_scan(
        workspace_root=slug_root.parent,
        workspace_id="ws-1", slug="slug",
        files_index=index,
    )
    assert "b.md" in rep.db_only_missing
    assert rep.has_blocking_drift is False


def test_hash_mismatch_blocks(slug_root: Path):
    _write(slug_root, "a.md", b"v_local")
    index = [{"relative_path": "a.md", "content_hash": _hash(b"v_db")}]
    rep = recovery_scan(
        workspace_root=slug_root.parent,
        workspace_id="ws-1", slug="slug",
        files_index=index,
    )
    assert rep.hash_mismatch == ["a.md"]
    assert rep.has_blocking_drift is True


def test_null_content_hash_treated_as_match(slug_root: Path):
    """Pre-Phase 8 rows where content_hash was never written must not
    trigger hash_mismatch on first scan."""
    _write(slug_root, "a.md", b"anything")
    index = [{"relative_path": "a.md", "content_hash": None}]
    rep = recovery_scan(
        workspace_root=slug_root.parent,
        workspace_id="ws-1", slug="slug",
        files_index=index,
    )
    assert rep.hash_mismatch == []


def test_subdirectories_traversed(slug_root: Path):
    _write(slug_root, "designs/d.md", b"x")
    _write(slug_root, "notes/n.md", b"y")
    index: list = []
    rep = recovery_scan(
        workspace_root=slug_root.parent,
        workspace_id="ws-1", slug="slug",
        files_index=index,
    )
    assert sorted(rep.local_only) == ["designs/d.md", "notes/n.md"]
