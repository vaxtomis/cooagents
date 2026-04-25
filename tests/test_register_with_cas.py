"""Phase 8b: ``WorkspaceFileRegistry.register`` with the optional
``expected_prior_hash`` CAS predicate.

* ``NOT_SET`` (default) — Phase 7b behaviour, no preconditions.
* ``None`` — first write; collides with an existing row.
* ``"<hex>"`` — overwrite of a known version; mismatch raises.
"""
from __future__ import annotations

import pytest

from src.database import Database
from src.exceptions import EtagMismatch
from src.storage.local import LocalFileStore
from src.storage.registry import (
    NOT_SET, WorkspaceFileRegistry, WorkspaceFilesRepo,
)


@pytest.fixture
async def env(tmp_path):
    db = Database(db_path=tmp_path / "cas.db", schema_path="db/schema.sql")
    await db.connect()
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    store = LocalFileStore(workspaces_root=ws_root)
    repo = WorkspaceFilesRepo(db)
    registry = WorkspaceFileRegistry(store=store, repo=repo)

    # Minimal workspace row — registry only reads slug + id.
    now = "2026-04-25T00:00:00+00:00"
    await db.execute(
        "INSERT INTO workspaces(id, slug, title, root_path, status, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
        ("ws-cas", "cas", "T", str(ws_root / "cas"), "active", now, now),
    )
    workspace_row = {"id": "ws-cas", "slug": "cas"}
    yield {"db": db, "registry": registry, "ws": workspace_row, "repo": repo}
    await db.close()


async def test_default_no_cas_overwrites_silently(env):
    """``expected_prior_hash`` absent — Phase 7b parity, no CAS guards."""
    await env["registry"].register(
        workspace_row=env["ws"], relative_path="a.md",
        data=b"v1", kind="other",
    )
    row = await env["registry"].register(
        workspace_row=env["ws"], relative_path="a.md",
        data=b"v2", kind="other",
    )
    # Second write succeeded and updated the row.
    import hashlib
    assert row["content_hash"] == hashlib.sha256(b"v2").hexdigest()


async def test_first_write_with_none_predicate_succeeds(env):
    row = await env["registry"].register(
        workspace_row=env["ws"], relative_path="b.md",
        data=b"v1", kind="other", expected_prior_hash=None,
    )
    import hashlib
    assert row["content_hash"] == hashlib.sha256(b"v1").hexdigest()


async def test_first_write_with_none_collides_when_row_exists(env):
    await env["registry"].register(
        workspace_row=env["ws"], relative_path="c.md",
        data=b"v1", kind="other",
    )
    with pytest.raises(EtagMismatch) as exc:
        await env["registry"].register(
            workspace_row=env["ws"], relative_path="c.md",
            data=b"v2", kind="other", expected_prior_hash=None,
        )
    assert exc.value.expected_hash is None
    assert exc.value.current_hash is not None


async def test_overwrite_with_correct_prior_hash_succeeds(env):
    first = await env["registry"].register(
        workspace_row=env["ws"], relative_path="d.md",
        data=b"v1", kind="other",
    )
    second = await env["registry"].register(
        workspace_row=env["ws"], relative_path="d.md",
        data=b"v2", kind="other",
        expected_prior_hash=first["content_hash"],
    )
    import hashlib
    assert second["content_hash"] == hashlib.sha256(b"v2").hexdigest()


async def test_overwrite_with_stale_prior_hash_rejects(env):
    first = await env["registry"].register(
        workspace_row=env["ws"], relative_path="e.md",
        data=b"v1", kind="other",
    )
    # Some other writer mutates the file (no CAS).
    await env["registry"].register(
        workspace_row=env["ws"], relative_path="e.md",
        data=b"v2", kind="other",
    )
    # Now the original prior_hash is stale.
    with pytest.raises(EtagMismatch) as exc:
        await env["registry"].register(
            workspace_row=env["ws"], relative_path="e.md",
            data=b"v3", kind="other",
            expected_prior_hash=first["content_hash"],
        )
    assert exc.value.expected_hash == first["content_hash"]
    # current_hash reflects the v2 we wrote between the two CAS calls.
    import hashlib
    assert exc.value.current_hash == hashlib.sha256(b"v2").hexdigest()


async def test_overwrite_when_row_missing_rejects(env):
    with pytest.raises(EtagMismatch) as exc:
        await env["registry"].register(
            workspace_row=env["ws"], relative_path="f.md",
            data=b"v1", kind="other",
            expected_prior_hash="0" * 64,
        )
    assert exc.value.current_hash is None
    assert exc.value.expected_hash == "0" * 64


async def test_repo_upsert_not_set_sentinel_skips_cas(env):
    """``NOT_SET`` sentinel must keep cooagents-internal callers untouched."""
    await env["registry"].register(
        workspace_row=env["ws"], relative_path="g.md",
        data=b"v1", kind="other",
    )
    # Sanity: passing NOT_SET reaches the no-CAS path even with a row present.
    row = await env["repo"].upsert(
        workspace_id="ws-cas", relative_path="g.md", kind="other",
        content_hash="ff", byte_size=1, local_mtime_ns=0,
        expected_prior_hash=NOT_SET,
    )
    assert row["content_hash"] == "ff"
