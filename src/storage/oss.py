"""Async ``FileStore`` backed by Aliyun OSS via the official
``alibabacloud-oss-v2`` SDK.

Surface-compatible with ``LocalFileStore`` — same five async methods, same
``FileRef`` shape, same ``BadRequestError`` / ``NotFoundError`` vocabulary.
Raises ``EtagMismatch`` (defined in ``src.storage.base`` as a plain
``Exception`` subclass) when a conditional PUT fails its ETag precondition
(412 Precondition Failed). Phase 5's ``register()`` narrows on
``EtagMismatch`` to drive retry semantics.

Credentials are externally injected: the constructor accepts any
SDK-compatible ``CredentialsProvider`` (static, env-backed, STS, …),
typed as ``Any`` to avoid leaking SDK types into our public surface.
Phase 6 constructs the provider from ``settings.storage.oss`` or env vars.

Expected env var names (documented; values injected externally in Phase 6):
``OSS_ACCESS_KEY_ID``, ``OSS_ACCESS_KEY_SECRET``, ``OSS_REGION``,
``OSS_ENDPOINT``, ``OSS_BUCKET``.

PRD: ``.claude/PRPs/prds/oss-file-storage-upgrade.prd.md`` — Phase 4.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from time import time_ns
from typing import Any

import alibabacloud_oss_v2 as oss
import alibabacloud_oss_v2.aio as oss_aio
from alibabacloud_oss_v2 import exceptions as oss_exceptions

from src.exceptions import BadRequestError, NotFoundError
from src.storage.base import EtagMismatch, FileRef, normalize_key

logger = logging.getLogger(__name__)


# Backward-compat re-export: Phase 4 integration tests import EtagMismatch
# from this module. The canonical definition lives in src.storage.base.
__all__ = ["EtagMismatch", "OSSFileStore"]


def _normalize_etag(raw: str | None) -> str | None:
    """Strip surrounding whitespace/quotes and lowercase the hex.

    OSS sometimes wraps the ETag in literal quotes and uses uppercase hex;
    lowercasing gives stable cross-client string equality for CAS tokens.
    Multipart ETags include a ``-N`` suffix — the hex prefix is lowercased
    and the ``-N`` suffix is preserved as-is.
    """
    if raw is None:
        return None
    s = raw.strip().strip('"')
    if "-" in s:
        head, _, tail = s.partition("-")
        return f"{head.lower()}-{tail}"
    return s.lower()


def _to_mtime_ns(dt: _dt.datetime | None) -> int:
    if dt is None:
        return 0
    # OSS Last-Modified is always UTC. If the SDK ever surfaces a naive
    # datetime, .timestamp() would apply the local offset — force UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _unwrap_service_error(
    exc: BaseException,
) -> oss_exceptions.ServiceError | None:
    """Return the underlying ServiceError, or None if not one.

    The async client wraps all failures in ``OperationError``; the real
    error is the ``error`` attribute / ``unwrap()`` return value.
    """
    if isinstance(exc, oss_exceptions.ServiceError):
        return exc
    if isinstance(exc, oss_exceptions.OperationError):
        inner = exc.unwrap()
        if isinstance(inner, oss_exceptions.ServiceError):
            return inner
    return None


def _is_not_found(err: oss_exceptions.ServiceError) -> bool:
    return err.status_code == 404 or err.code == "NoSuchKey"


def _is_precondition_failed(err: oss_exceptions.ServiceError) -> bool:
    return err.status_code == 412 or err.code == "PreconditionFailed"


class OSSFileStore:
    """Async FileStore backed by Aliyun OSS via the official SDK.

    Keys are workspace-relative POSIX strings (e.g. ``demo/designs/DES-x.md``).
    The remote object key is ``f"{prefix}{key}"``; ``prefix`` is configured at
    construction (Phase 6 maps ``settings.storage.oss.prefix`` here) and may
    be empty.
    """

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        endpoint: str,
        credentials_provider: Any,
        prefix: str = "",
    ) -> None:
        if not bucket:
            raise BadRequestError("OSSFileStore: bucket must not be empty")
        if not region:
            raise BadRequestError("OSSFileStore: region must not be empty")
        if credentials_provider is None:
            raise BadRequestError(
                "OSSFileStore: credentials_provider must not be None"
            )
        if prefix:
            if prefix.startswith("/"):
                raise BadRequestError(
                    f"OSSFileStore: prefix must not start with '/': {prefix!r}"
                )
            if not prefix.endswith("/"):
                raise BadRequestError(
                    f"OSSFileStore: prefix must end with '/': {prefix!r}"
                )
        if endpoint and not endpoint.startswith(("http://", "https://")):
            raise BadRequestError(
                f"OSSFileStore: endpoint must be http(s) URL: {endpoint!r}"
            )

        cfg = oss.config.load_default()
        cfg.credentials_provider = credentials_provider
        cfg.region = region
        if endpoint:
            cfg.endpoint = endpoint

        self._bucket = bucket
        self._cfg = cfg
        self._prefix = prefix
        self._client: oss_aio.AsyncClient | None = None
        self._client_lock: asyncio.Lock | None = None

    def _build_object_key(self, key: str) -> tuple[str, str]:
        norm = normalize_key(key)
        norm_str = norm.as_posix()
        object_key = f"{self._prefix}{norm_str}" if self._prefix else norm_str
        if self._prefix and not object_key.startswith(self._prefix):
            raise BadRequestError(
                f"key escapes configured OSS prefix: {object_key!r}"
            )
        return norm_str, object_key

    async def _get_client(self) -> oss_aio.AsyncClient:
        if self._client is not None:
            return self._client
        if self._client_lock is None:
            # Create the lock lazily in the running loop so the store is
            # safe to construct outside an async context (including sync
            # fixture helpers) without binding to the wrong loop.
            self._client_lock = asyncio.Lock()
        async with self._client_lock:
            if self._client is None:
                self._client = oss_aio.AsyncClient(self._cfg)
        return self._client

    async def close(self) -> None:
        client = self._client
        if client is not None:
            self._client = None
            await client.close()

    async def put_bytes(self, key: str, data: bytes) -> FileRef:
        return await self.put_bytes_conditional(key, data)

    async def put_bytes_conditional(
        self,
        key: str,
        data: bytes,
        *,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> FileRef:
        norm_str, oss_key = self._build_object_key(key)
        client = await self._get_client()
        headers: dict[str, str] = {}
        if if_match is not None:
            headers["If-Match"] = if_match
        if if_none_match is not None:
            headers["If-None-Match"] = if_none_match
        req_kwargs: dict[str, Any] = {
            "bucket": self._bucket,
            "key": oss_key,
            "body": data,
        }
        if headers:
            req_kwargs["headers"] = headers
        try:
            result = await client.put_object(oss.PutObjectRequest(**req_kwargs))
        except Exception as exc:
            svc = _unwrap_service_error(exc)
            if svc is not None and _is_precondition_failed(svc):
                logger.warning(
                    "oss conditional PUT 412 for key=%s if_match_set=%s if_none_match_set=%s",
                    norm_str,
                    if_match is not None,
                    if_none_match is not None,
                )
                raise EtagMismatch(
                    f"conditional PUT failed for {norm_str!r}: {svc.code}"
                ) from exc
            raise
        etag = _normalize_etag(result.etag)
        logger.debug(
            "oss put_bytes: %s (%d bytes) -> etag=%s", norm_str, len(data), etag
        )
        return FileRef(
            key=norm_str,
            size=len(data),
            mtime_ns=time_ns(),
            etag=etag,
        )

    async def get_bytes(self, key: str) -> bytes:
        norm_str, oss_key = self._build_object_key(key)
        client = await self._get_client()
        try:
            result = await client.get_object(
                oss.GetObjectRequest(bucket=self._bucket, key=oss_key)
            )
        except Exception as exc:
            svc = _unwrap_service_error(exc)
            if svc is not None and _is_not_found(svc):
                raise NotFoundError(f"key not found: {norm_str!r}") from exc
            raise
        return await result.body.read()

    async def stat(self, key: str) -> FileRef | None:
        norm_str, oss_key = self._build_object_key(key)
        client = await self._get_client()
        try:
            result = await client.head_object(
                oss.HeadObjectRequest(bucket=self._bucket, key=oss_key)
            )
        except Exception as exc:
            svc = _unwrap_service_error(exc)
            if svc is not None and _is_not_found(svc):
                return None
            raise
        return FileRef(
            key=norm_str,
            size=int(result.content_length or 0),
            mtime_ns=_to_mtime_ns(result.last_modified),
            etag=_normalize_etag(result.etag),
        )

    async def delete(self, key: str) -> None:
        norm_str, oss_key = self._build_object_key(key)
        client = await self._get_client()
        try:
            await client.delete_object(
                oss.DeleteObjectRequest(bucket=self._bucket, key=oss_key)
            )
        except Exception as exc:
            svc = _unwrap_service_error(exc)
            if svc is not None and _is_not_found(svc):
                return
            raise
        logger.debug("oss delete: %s", norm_str)

    async def list(self, prefix: str) -> list[FileRef]:
        if prefix == "":
            query_prefix = self._prefix
        else:
            _, query_prefix = self._build_object_key(prefix)
        client = await self._get_client()
        results: list[FileRef] = []
        continuation_token: str | None = None
        while True:
            req_kwargs: dict[str, Any] = {
                "bucket": self._bucket,
                "max_keys": 1000,
            }
            if query_prefix:
                req_kwargs["prefix"] = query_prefix
            if continuation_token:
                req_kwargs["continuation_token"] = continuation_token
            page = await client.list_objects_v2(
                oss.ListObjectsV2Request(**req_kwargs)
            )
            for obj in page.contents or []:
                obj_key = obj.key or ""
                if self._prefix:
                    if not obj_key.startswith(self._prefix):
                        continue
                    rel = obj_key[len(self._prefix):]
                else:
                    rel = obj_key
                if not rel:
                    continue
                results.append(
                    FileRef(
                        key=rel,
                        size=int(obj.size or 0),
                        mtime_ns=_to_mtime_ns(obj.last_modified),
                        etag=_normalize_etag(obj.etag),
                    )
                )
            if not page.is_truncated:
                break
            continuation_token = page.next_continuation_token
            if not continuation_token:
                break
        results.sort(key=lambda r: r.key)
        return results
