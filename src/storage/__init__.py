from __future__ import annotations

# OSSFileStore is intentionally NOT re-exported here: importing it pulls in
# alibabacloud_oss_v2, which costs ~100ms of startup time. LocalFileStore-only
# deployments avoid that cost. Callers that need OSSFileStore directly (the
# integration test suite) import it from src.storage.oss.
from src.storage.base import FileRef, FileStore, normalize_key
from src.storage.factory import build_file_store
from src.storage.local import LocalFileStore
from src.storage.registry import WorkspaceFileRegistry, WorkspaceFilesRepo

__all__ = [
    "FileRef",
    "FileStore",
    "LocalFileStore",
    "WorkspaceFileRegistry",
    "WorkspaceFilesRepo",
    "build_file_store",
    "normalize_key",
]
