"""Shared test fixtures.

Provides ``FakeOSSStore`` — an in-memory duck-type of ``OSSFileStore`` used
by registry / OSS-aware tests. Avoids hitting a real OSS bucket from unit
tests; real OSS stays covered by the integration suite under
``tests/integration/``.
"""
from __future__ import annotations

from time import time_ns

import pytest

from src.exceptions import NotFoundError
from src.storage.base import FileRef


class FakeOSSStore:
    """In-memory duck-type of ``OSSFileStore`` for registry tests.

    Matches the OSSFileStore contract surface in use:
      * ``put_bytes``, ``get_bytes``, ``stat``, ``delete``, ``list``
      * ``close()`` (idempotent)
    """

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}
        self._etag_counter = 0
        self._head_calls = 0
        self._get_calls = 0

    def _next_etag(self) -> str:
        self._etag_counter += 1
        return f"etag{self._etag_counter:032x}"

    async def put_bytes(self, key: str, data: bytes) -> FileRef:
        etag = self._next_etag()
        self._objects[key] = (data, etag)
        return FileRef(key=key, size=len(data), mtime_ns=time_ns(), etag=etag)

    async def get_bytes(self, key: str) -> bytes:
        self._get_calls += 1
        if key not in self._objects:
            raise NotFoundError(f"key not found: {key!r}")
        return self._objects[key][0]

    async def stat(self, key: str) -> FileRef | None:
        self._head_calls += 1
        if key not in self._objects:
            return None
        data, etag = self._objects[key]
        return FileRef(key=key, size=len(data), mtime_ns=0, etag=etag)

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    async def list(self, prefix: str) -> list[FileRef]:
        refs: list[FileRef] = []
        for k, (d, etag) in self._objects.items():
            if prefix == "" or k.startswith(prefix):
                refs.append(
                    FileRef(key=k, size=len(d), mtime_ns=0, etag=etag)
                )
        return sorted(refs, key=lambda r: r.key)

    async def close(self) -> None:
        self._objects.clear()


@pytest.fixture
def fake_oss_store() -> FakeOSSStore:
    return FakeOSSStore()
