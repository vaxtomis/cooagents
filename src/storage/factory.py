"""Build the right FileStore backend per settings (Phase 6).

Dispatches on ``settings.storage.oss.enabled``. The OSS branch imports
``alibabacloud_oss_v2`` lazily so LocalFileStore-only deployments avoid
the SDK import cost entirely.
"""
from __future__ import annotations

import logging
from pathlib import Path

from src.config import Settings
from src.storage.base import FileStore
from src.storage.local import LocalFileStore

logger = logging.getLogger(__name__)


def build_file_store(
    settings: Settings, workspaces_root: Path,
) -> FileStore:
    """Return the configured FileStore backend.

    ``settings.storage.oss.enabled=False`` → ``LocalFileStore`` (default).
    ``settings.storage.oss.enabled=True`` → ``OSSFileStore`` with a
    ``StaticCredentialsProvider`` built from
    ``settings.storage.oss.access_key_{id,secret}``.

    Assumes ``load_settings`` has already validated that required fields
    are present when OSS is enabled. Does NOT re-validate here; the
    downstream ``OSSFileStore.__init__`` catches the remaining shape
    issues (empty bucket, malformed endpoint, etc.).
    """
    oss_cfg = settings.storage.oss
    if not oss_cfg.enabled:
        logger.info(
            "storage factory: LocalFileStore(workspaces_root=%s)",
            workspaces_root,
        )
        return LocalFileStore(workspaces_root=workspaces_root)

    # Deferred SDK import — LocalFileStore deployments never touch it.
    import alibabacloud_oss_v2 as oss
    from src.storage.oss import OSSFileStore

    provider = oss.credentials.StaticCredentialsProvider(
        access_key_id=oss_cfg.access_key_id,
        access_key_secret=oss_cfg.access_key_secret,
    )
    logger.info(
        "storage factory: OSSFileStore(bucket=%s region=%s endpoint=%s "
        "prefix=%r)",
        oss_cfg.bucket, oss_cfg.region, oss_cfg.endpoint, oss_cfg.prefix,
    )
    return OSSFileStore(
        bucket=oss_cfg.bucket,
        region=oss_cfg.region,
        endpoint=oss_cfg.endpoint,
        credentials_provider=provider,
        prefix=oss_cfg.prefix,
    )
