from __future__ import annotations

import hashlib
import os
import re
import uuid

import pytest

from src.exceptions import BadRequestError, NotFoundError
from src.storage.base import FileRef
from src.storage.local import LocalFileStore


@pytest.fixture
def store(tmp_path):
    return LocalFileStore(workspaces_root=tmp_path)


async def test_put_bytes_writes_file_and_returns_ref(store, tmp_path):
    ref = await store.put_bytes("demo/hello.txt", b"hi")
    assert isinstance(ref, FileRef)
    assert ref.key == "demo/hello.txt"
    assert ref.size == 2
    assert ref.etag is None
    assert ref.mtime_ns > 0
    assert (tmp_path / "demo" / "hello.txt").is_file()


async def test_get_bytes_roundtrip_preserves_hash(store):
    payload = b"the quick brown fox jumps over the lazy dog"
    await store.put_bytes("roundtrip/payload.bin", payload)
    got = await store.get_bytes("roundtrip/payload.bin")
    assert hashlib.sha256(got).hexdigest() == hashlib.sha256(payload).hexdigest()


async def test_get_bytes_missing_raises_not_found(store):
    with pytest.raises(NotFoundError):
        await store.get_bytes("nope/missing.txt")


async def test_stat_missing_returns_none(store):
    assert await store.stat("absent.txt") is None


async def test_stat_present_returns_ref_without_reading_body(store):
    ref_put = await store.put_bytes("a/b.bin", b"12345")
    ref_stat = await store.stat("a/b.bin")
    assert ref_stat is not None
    assert ref_stat.key == "a/b.bin"
    assert ref_stat.size == ref_put.size == 5
    assert ref_stat.mtime_ns == ref_put.mtime_ns
    assert ref_stat.etag is None


async def test_delete_is_idempotent(store):
    await store.put_bytes("x/y.txt", b"hello")
    await store.delete("x/y.txt")
    await store.delete("x/y.txt")


async def test_list_returns_nested_files_as_posix_keys(store, tmp_path):
    await store.put_bytes("a/b.txt", b"B")
    await store.put_bytes("a/c/d.txt", b"D")
    # Manually seed a real temp residue (32 lowercase hex) — must be hidden.
    (tmp_path / "a" / "c").mkdir(parents=True, exist_ok=True)
    residue = tmp_path / "a" / "c" / f"d.txt.tmp-{uuid.uuid4().hex}"
    residue.write_bytes(b"garbage")
    # Seed a human-authored file that merely contains ".tmp-" — must be kept.
    (tmp_path / "a" / "foo.tmp-notes.md").write_bytes(b"notes")

    refs = await store.list("a")
    keys = [r.key for r in refs]
    assert keys == ["a/b.txt", "a/c/d.txt", "a/foo.tmp-notes.md"]
    assert all("\\" not in k for k in keys)


async def test_list_empty_prefix_walks_all(store):
    await store.put_bytes("a/b.txt", b"B")
    await store.put_bytes("c.txt", b"C")
    refs = await store.list("")
    keys = sorted(r.key for r in refs)
    assert keys == ["a/b.txt", "c.txt"]


async def test_key_with_backslash_rejected(store):
    with pytest.raises(BadRequestError):
        await store.put_bytes("a\\b.txt", b"x")


async def test_key_with_drive_letter_rejected(store):
    with pytest.raises(BadRequestError):
        await store.put_bytes("C:/x", b"x")


async def test_key_with_parent_traversal_rejected(store):
    with pytest.raises(BadRequestError):
        await store.put_bytes("a/../../etc/passwd", b"x")


async def test_absolute_key_rejected(store):
    with pytest.raises(BadRequestError):
        await store.put_bytes("/tmp/x", b"x")
    with pytest.raises(BadRequestError):
        await store.put_bytes("\\root\\x", b"x")


async def test_empty_segment_key_rejected(store):
    with pytest.raises(BadRequestError):
        await store.put_bytes("a//b", b"x")


async def test_put_bytes_creates_parent_dirs(store, tmp_path):
    await store.put_bytes("a/b/c/d.txt", b"deep")
    assert (tmp_path / "a" / "b" / "c" / "d.txt").is_file()


async def test_put_bytes_atomic_no_tmp_residue_on_success(store):
    await store.put_bytes("atomic/one.txt", b"one")
    await store.put_bytes("atomic/two.txt", b"two")
    refs = await store.list("")
    pat = re.compile(r"\.tmp-[0-9a-f]{32}$")
    assert not any(pat.search(r.key) for r in refs)


async def test_put_bytes_is_byte_exact_across_newlines(store):
    payload = b"a\r\nb\n"
    await store.put_bytes("nl/test.bin", payload)
    got = await store.get_bytes("nl/test.bin")
    assert got == payload


async def test_empty_payload_roundtrip(store):
    ref = await store.put_bytes("empty.bin", b"")
    assert ref.size == 0
    assert await store.get_bytes("empty.bin") == b""


async def test_normalize_key_rejects_non_string(store):
    with pytest.raises(BadRequestError):
        await store.put_bytes(123, b"x")  # type: ignore[arg-type]


async def test_normalize_key_rejects_empty(store):
    with pytest.raises(BadRequestError):
        await store.put_bytes("", b"x")


async def test_stat_on_directory_returns_none(store, tmp_path):
    (tmp_path / "dir").mkdir()
    assert await store.stat("dir") is None


async def test_delete_on_directory_raises(store, tmp_path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "inner.txt").write_bytes(b"x")
    with pytest.raises(BadRequestError):
        await store.delete("dir")


async def test_list_nonexistent_prefix_returns_empty(store):
    assert await store.list("nope/sub") == []


async def test_list_prefix_pointing_to_file(store):
    await store.put_bytes("a/b.txt", b"B")
    refs = await store.list("a/b.txt")
    assert len(refs) == 1
    assert refs[0].key == "a/b.txt"


async def test_put_escape_via_symlink_blocked(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    inside = tmp_path / "inside"
    inside.mkdir()
    # Create a symlink inside the root that points outside.
    link = inside / "escape"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform/user")
    store = LocalFileStore(workspaces_root=inside)
    with pytest.raises(BadRequestError):
        await store.put_bytes("escape/x.txt", b"x")
