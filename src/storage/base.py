from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable

from src.exceptions import BadRequestError


@dataclass(frozen=True)
class FileRef:
    """Metadata describing a single blob in a FileStore.

    Intentionally omits content_hash: stat/list must stay O(1) per file.
    Callers that need a hash should read via get_bytes and hash the payload
    themselves (see phase-1 plan Notes).
    """

    key: str
    size: int
    mtime_ns: int
    etag: str | None = None


@runtime_checkable
class FileStore(Protocol):
    async def get_bytes(self, key: str) -> bytes: ...
    async def put_bytes(self, key: str, data: bytes) -> FileRef: ...
    async def stat(self, key: str) -> FileRef | None: ...
    async def delete(self, key: str) -> None: ...
    async def list(self, prefix: str) -> list[FileRef]: ...


def normalize_key(key: str) -> PurePosixPath:
    """Validate a workspace-relative POSIX key and return its PurePosixPath.

    Rejects absolute paths, backslashes, Windows drive letters, empty segments,
    and '..' traversal. Empty string is allowed only by LocalFileStore.list as
    a "whole root" prefix and is handled by the caller, not here.
    """
    if not isinstance(key, str):
        raise BadRequestError(f"key must be str, got {type(key).__name__}")
    if not key:
        raise BadRequestError("key must not be empty")
    if key.startswith("/"):
        raise BadRequestError(f"key must be relative POSIX path, got {key!r}")
    if "\\" in key:
        raise BadRequestError(
            f"key must be POSIX-style (no '\\' or drive letters): {key!r}"
        )
    if len(key) > 1 and key[1] == ":":
        raise BadRequestError(
            f"key must be POSIX-style (no '\\' or drive letters): {key!r}"
        )
    parts = key.split("/")
    for part in parts:
        if part in ("", ".."):
            raise BadRequestError(
                f"key must not contain empty or '..' segments: {key!r}"
            )
    return PurePosixPath(key)
