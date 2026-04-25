"""Phase 8b: agent_worker.materialize round-trip via a fake byte source."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.agent_worker.materialize import materialize


class InMemoryStore:
    def __init__(self, objects: dict[str, bytes]):
        self._objects = dict(objects)

    async def get_bytes(self, key: str) -> bytes:
        if key not in self._objects:
            raise FileNotFoundError(key)
        return self._objects[key]


def _hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


async def test_pulls_only_requested_paths(workspace_root: Path):
    store = InMemoryStore({
        "demo/a.md": b"AA",
        "demo/b.md": b"BB",
    })
    index = [
        {"relative_path": "a.md", "content_hash": _hash(b"AA")},
        {"relative_path": "b.md", "content_hash": _hash(b"BB")},
    ]
    rep = await materialize(
        store=store,
        workspace_root=workspace_root,
        slug="demo",
        files_index=index,
        paths_to_pull=["a.md"],
    )
    assert rep.pulled == ["a.md"]
    assert rep.skipped == ["b.md"]
    assert (workspace_root / "demo" / "a.md").read_bytes() == b"AA"
    assert not (workspace_root / "demo" / "b.md").exists()


async def test_pulls_everything_when_paths_to_pull_none(workspace_root: Path):
    store = InMemoryStore({
        "demo/a.md": b"AA",
        "demo/b.md": b"BB",
    })
    index = [
        {"relative_path": "a.md", "content_hash": _hash(b"AA")},
        {"relative_path": "b.md", "content_hash": _hash(b"BB")},
    ]
    rep = await materialize(
        store=store,
        workspace_root=workspace_root,
        slug="demo",
        files_index=index,
    )
    assert sorted(rep.pulled) == ["a.md", "b.md"]
    assert not rep.failed


async def test_hash_mismatch_marks_failed(workspace_root: Path):
    store = InMemoryStore({"demo/a.md": b"corrupt"})
    index = [{"relative_path": "a.md", "content_hash": _hash(b"clean")}]
    rep = await materialize(
        store=store,
        workspace_root=workspace_root,
        slug="demo",
        files_index=index,
        paths_to_pull=["a.md"],
    )
    assert "a.md" in rep.failed
    assert rep.failed["a.md"].startswith("hash_mismatch")
    # Failed pull must not produce a local file.
    assert not (workspace_root / "demo" / "a.md").exists()


async def test_missing_oss_object_marks_failed(workspace_root: Path):
    store = InMemoryStore({})
    index = [{"relative_path": "a.md", "content_hash": _hash(b"x")}]
    rep = await materialize(
        store=store,
        workspace_root=workspace_root,
        slug="demo",
        files_index=index,
        paths_to_pull=["a.md"],
    )
    assert "a.md" in rep.failed
    assert "get_bytes_failed" in rep.failed["a.md"]


async def test_creates_subdirectories(workspace_root: Path):
    store = InMemoryStore({"demo/designs/d.md": b"X"})
    index = [{"relative_path": "designs/d.md", "content_hash": _hash(b"X")}]
    rep = await materialize(
        store=store,
        workspace_root=workspace_root,
        slug="demo",
        files_index=index,
        paths_to_pull=["designs/d.md"],
    )
    assert rep.pulled == ["designs/d.md"]
    assert (workspace_root / "demo" / "designs" / "d.md").read_bytes() == b"X"


async def test_null_db_hash_skips_verification(workspace_root: Path):
    """Legacy rows with content_hash=NULL must materialize without raising."""
    store = InMemoryStore({"demo/a.md": b"AA"})
    index = [{"relative_path": "a.md", "content_hash": None}]
    rep = await materialize(
        store=store,
        workspace_root=workspace_root,
        slug="demo",
        files_index=index,
        paths_to_pull=["a.md"],
    )
    assert rep.pulled == ["a.md"]
