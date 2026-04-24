"""Integration tests for ``OSSFileStore`` against a real Aliyun OSS bucket.

Skipped when required env vars are absent so the default CI lane stays
hermetic. Each test shares a fixture-scoped unique prefix so parallel
lanes against the same bucket do not collide and so failed cleanups do
not bleed into later runs.

Env contract (all required except ``OSS_RUN_SLOW``):
- ``OSS_BUCKET``
- ``OSS_ENDPOINT`` (e.g. ``https://oss-cn-hangzhou.aliyuncs.com``)
- ``OSS_REGION`` (e.g. ``cn-hangzhou``)
- ``OSS_ACCESS_KEY_ID`` / ``OSS_ACCESS_KEY_SECRET``
- ``OSS_RUN_SLOW=1`` — additionally exercises the 1010-key paginator test
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import AsyncIterator

import pytest

import alibabacloud_oss_v2 as oss

from src.exceptions import BadRequestError, NotFoundError
from src.storage import EtagMismatch, FileRef, FileStore, OSSFileStore

REQUIRED_ENV = (
    "OSS_ACCESS_KEY_ID",
    "OSS_ACCESS_KEY_SECRET",
    "OSS_REGION",
    "OSS_ENDPOINT",
    "OSS_BUCKET",
)


def _missing_env() -> list[str]:
    return [v for v in REQUIRED_ENV if not os.environ.get(v)]


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        bool(_missing_env()),
        reason=(
            "OSS integration tests skipped — missing env vars: "
            f"{', '.join(_missing_env())}"
        ),
    ),
]


def _make_store(prefix: str) -> OSSFileStore:
    provider = oss.credentials.StaticCredentialsProvider(
        access_key_id=os.environ["OSS_ACCESS_KEY_ID"],
        access_key_secret=os.environ["OSS_ACCESS_KEY_SECRET"],
    )
    return OSSFileStore(
        bucket=os.environ["OSS_BUCKET"],
        region=os.environ["OSS_REGION"],
        endpoint=os.environ["OSS_ENDPOINT"],
        credentials_provider=provider,
        prefix=prefix,
    )


@pytest.fixture
async def store() -> AsyncIterator[OSSFileStore]:
    prefix = f"cooagents-test/{uuid.uuid4().hex}/"
    s = _make_store(prefix)
    assert isinstance(s, FileStore)
    try:
        yield s
    finally:
        try:
            refs = await s.list("")
            for r in refs:
                try:
                    await s.delete(r.key)
                except Exception:
                    pass
        finally:
            await s.close()


async def test_put_bytes_returns_etag_and_normalized(store: OSSFileStore) -> None:
    ref = await store.put_bytes("a/b.txt", b"hi")
    assert isinstance(ref, FileRef)
    assert ref.key == "a/b.txt"
    assert ref.size == 2
    assert ref.etag is not None
    hex_part = ref.etag.split("-", 1)[0]
    assert hex_part == hex_part.lower()


async def test_get_bytes_roundtrip_byte_exact(store: OSSFileStore) -> None:
    await store.put_bytes("r/line.bin", b"a\r\nb\n")
    got = await store.get_bytes("r/line.bin")
    assert got == b"a\r\nb\n"


async def test_get_bytes_missing_raises_not_found(store: OSSFileStore) -> None:
    with pytest.raises(NotFoundError):
        await store.get_bytes("nonexistent/ghost.txt")


async def test_stat_present_returns_ref_with_etag(store: OSSFileStore) -> None:
    put_ref = await store.put_bytes("s/hello.txt", b"hello")
    stat_ref = await store.stat("s/hello.txt")
    assert stat_ref is not None
    assert stat_ref.size == 5
    assert stat_ref.etag == put_ref.etag
    assert stat_ref.mtime_ns > 0
    assert stat_ref.key == "s/hello.txt"


async def test_stat_missing_returns_none(store: OSSFileStore) -> None:
    assert await store.stat("ghost.txt") is None


async def test_delete_is_idempotent(store: OSSFileStore) -> None:
    await store.put_bytes("d/once.txt", b"x")
    await store.delete("d/once.txt")
    await store.delete("d/once.txt")  # must not raise


async def test_list_returns_keys_without_prefix(store: OSSFileStore) -> None:
    await store.put_bytes("a/b.txt", b"1")
    await store.put_bytes("a/c/d.txt", b"2")
    refs = await store.list("a")
    keys = [r.key for r in refs]
    assert keys == ["a/b.txt", "a/c/d.txt"]


async def test_list_empty_prefix_walks_all(store: OSSFileStore) -> None:
    await store.put_bytes("x.txt", b"1")
    await store.put_bytes("y/z.txt", b"2")
    await store.put_bytes("y/w/q.txt", b"3")
    refs = await store.list("")
    keys = sorted(r.key for r in refs)
    assert keys == ["x.txt", "y/w/q.txt", "y/z.txt"]


async def test_list_smoke_50_keys(store: OSSFileStore) -> None:
    async def _put(i: int) -> None:
        await store.put_bytes(f"p/{i:04d}.bin", b"")

    await asyncio.gather(*(_put(i) for i in range(50)))
    refs = await store.list("p")
    assert len(refs) == 50


@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get("OSS_RUN_SLOW") != "1",
    reason="slow test gated by OSS_RUN_SLOW=1",
)
async def test_list_paginates_beyond_1000(store: OSSFileStore) -> None:
    total = 1010
    batch = 50

    async def _put(i: int) -> None:
        await store.put_bytes(f"big/{i:05d}.bin", b"")

    for start in range(0, total, batch):
        await asyncio.gather(
            *(_put(i) for i in range(start, min(start + batch, total)))
        )
    refs = await store.list("big")
    assert len(refs) == total


async def test_put_bytes_rejects_backslash_key(store: OSSFileStore) -> None:
    with pytest.raises(BadRequestError):
        await store.put_bytes("a\\b.txt", b"x")


async def test_put_bytes_rejects_absolute_key(store: OSSFileStore) -> None:
    with pytest.raises(BadRequestError):
        await store.put_bytes("/x", b"x")


# Phase 5's register() calls put_bytes_conditional directly.
async def test_put_with_if_none_match_first_create_succeeds(
    store: OSSFileStore,
) -> None:
    ref = await store.put_bytes_conditional(
        "cond/new.txt", b"one", if_none_match="*"
    )
    assert ref.etag is not None


async def test_put_with_if_none_match_existing_key_raises_etag_mismatch(
    store: OSSFileStore,
) -> None:
    await store.put_bytes("cond/exists.txt", b"one")
    with pytest.raises(EtagMismatch):
        await store.put_bytes_conditional(
            "cond/exists.txt", b"two", if_none_match="*"
        )


async def test_put_with_if_match_succeeds_on_etag_match(
    store: OSSFileStore,
) -> None:
    first = await store.put_bytes("cond/cas.txt", b"v1")
    assert first.etag is not None
    second = await store.put_bytes_conditional(
        "cond/cas.txt", b"v2", if_match=first.etag
    )
    assert second.etag is not None
    assert second.etag != first.etag


async def test_put_with_if_match_fails_on_etag_mismatch(
    store: OSSFileStore,
) -> None:
    await store.put_bytes("cond/nope.txt", b"v1")
    with pytest.raises(EtagMismatch):
        await store.put_bytes_conditional(
            "cond/nope.txt", b"v2", if_match="deadbeef" * 4
        )


async def test_etag_format_is_lowercase_hex_no_quotes(
    store: OSSFileStore,
) -> None:
    ref = await store.put_bytes("fmt/etag.bin", b"abc")
    assert ref.etag is not None
    allowed = set("0123456789abcdef-")
    assert set(ref.etag) <= allowed


async def test_concurrent_puts_to_distinct_keys_dont_corrupt_each_other(
    store: OSSFileStore,
) -> None:
    async def _put(i: int) -> bytes:
        payload = bytes([i])
        await store.put_bytes(f"k/{i:02d}.bin", payload)
        return payload

    await asyncio.gather(*(_put(i) for i in range(20)))
    for i in range(20):
        got = await store.get_bytes(f"k/{i:02d}.bin")
        assert got == bytes([i])


async def test_close_is_idempotent(store: OSSFileStore) -> None:
    await store.close()
    await store.close()


def test_constructor_rejects_missing_provider() -> None:
    with pytest.raises(BadRequestError):
        OSSFileStore(
            bucket=os.environ.get("OSS_BUCKET", "x"),
            region=os.environ.get("OSS_REGION", "cn-hangzhou"),
            endpoint=os.environ.get("OSS_ENDPOINT", ""),
            credentials_provider=None,
        )


def test_constructor_rejects_prefix_without_trailing_slash() -> None:
    provider = oss.credentials.StaticCredentialsProvider(
        access_key_id=os.environ.get("OSS_ACCESS_KEY_ID", "x"),
        access_key_secret=os.environ.get("OSS_ACCESS_KEY_SECRET", "x"),
    )
    with pytest.raises(BadRequestError):
        OSSFileStore(
            bucket=os.environ.get("OSS_BUCKET", "x"),
            region=os.environ.get("OSS_REGION", "cn-hangzhou"),
            endpoint=os.environ.get("OSS_ENDPOINT", ""),
            credentials_provider=provider,
            prefix="a",
        )


def test_constructor_rejects_empty_bucket() -> None:
    provider = oss.credentials.StaticCredentialsProvider(
        access_key_id=os.environ.get("OSS_ACCESS_KEY_ID", "x"),
        access_key_secret=os.environ.get("OSS_ACCESS_KEY_SECRET", "x"),
    )
    with pytest.raises(BadRequestError):
        OSSFileStore(
            bucket="",
            region=os.environ.get("OSS_REGION", "cn-hangzhou"),
            endpoint=os.environ.get("OSS_ENDPOINT", ""),
            credentials_provider=provider,
        )
