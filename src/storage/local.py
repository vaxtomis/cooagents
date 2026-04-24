from __future__ import annotations

import logging
import os
import re
import stat as stat_mod
import uuid
from pathlib import Path

from src.exceptions import BadRequestError, NotFoundError
from src.storage.base import FileRef, normalize_key

logger = logging.getLogger(__name__)

_TEMP_SUFFIX_RE = re.compile(r"\.tmp-[0-9a-f]{32}$")


class LocalFileStore:
    """Async FileStore backed by the local filesystem under workspaces_root.

    Keys are workspace-relative POSIX strings (e.g. "demo/designs/DES-x-1.0.0.md").
    Every operation re-asserts the path stays under workspaces_root; see PRD
    Risk L281 for the cross-platform rationale.
    """

    def __init__(self, workspaces_root: Path | str) -> None:
        self.workspaces_root = Path(workspaces_root).expanduser().resolve()

    def _resolve(self, key: str) -> tuple[str, Path]:
        norm = normalize_key(key)
        norm_str = norm.as_posix()
        absolute = (self.workspaces_root / norm_str).resolve()
        root = self.workspaces_root.resolve()
        try:
            absolute.relative_to(root)
        except ValueError as exc:
            raise BadRequestError(
                f"path escapes workspaces_root: {absolute}"
            ) from exc
        return norm_str, absolute

    async def put_bytes(self, key: str, data: bytes) -> FileRef:
        norm_str, path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        st = os.stat(path)
        logger.debug("put_bytes: wrote %d bytes to %s", st.st_size, norm_str)
        return FileRef(
            key=norm_str, size=st.st_size, mtime_ns=st.st_mtime_ns, etag=None
        )

    async def get_bytes(self, key: str) -> bytes:
        norm_str, path = self._resolve(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise NotFoundError(f"key not found: {norm_str!r}") from exc

    async def stat(self, key: str) -> FileRef | None:
        norm_str, path = self._resolve(key)
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return None
        if not stat_mod.S_ISREG(st.st_mode):
            return None
        return FileRef(
            key=norm_str, size=st.st_size, mtime_ns=st.st_mtime_ns, etag=None
        )

    async def delete(self, key: str) -> None:
        _, path = self._resolve(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except IsADirectoryError as exc:
            raise BadRequestError(f"key is a directory: {key!r}") from exc
        except PermissionError as exc:
            # Windows raises PermissionError when unlinking a directory.
            if path.is_dir():
                raise BadRequestError(f"key is a directory: {key!r}") from exc
            raise

    async def list(self, prefix: str) -> list[FileRef]:
        # Empty prefix walks the whole workspaces_root and bypasses
        # normalize_key. Internal-only (reconciliation, tests); never
        # forward an unsanitised request value here.
        if prefix == "":
            base = self.workspaces_root
        else:
            _, base = self._resolve(prefix)
        if not base.exists():
            return []
        results: list[FileRef] = []
        if base.is_file():
            st = os.stat(base)
            rel = base.relative_to(self.workspaces_root).as_posix()
            results.append(
                FileRef(key=rel, size=st.st_size, mtime_ns=st.st_mtime_ns, etag=None)
            )
            return results
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if _TEMP_SUFFIX_RE.search(p.name):
                continue
            st = os.stat(p)
            rel = p.relative_to(self.workspaces_root).as_posix()
            results.append(
                FileRef(key=rel, size=st.st_size, mtime_ns=st.st_mtime_ns, etag=None)
            )
        results.sort(key=lambda r: r.key)
        return results
