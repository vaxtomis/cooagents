"""Read-only inspector for the repo registry (Phase 3, repo-registry).

Operates against the bare clone produced by :class:`RepoFetcher` (Phase 2):
``branches`` / ``tree`` / ``blob`` / ``log`` / ``rev_parse``. **Pure read** —
no remote fetch, no DB writes. Callers must ensure the repo is healthy
(``fetch_status='healthy'``) before invoking; otherwise the inspector
returns ``ConflictError`` so the caller can prompt for a fetch.

Output is bounded by module-level defaults; caller-supplied larger values
are silently clamped down (single layer — no separate hard cap rejection)
because this is an internal API and the caps exist to protect the
process, not to enforce a contract.

Bare-clone path discovery prefers ``repos.bare_clone_path`` (the column
the Phase 2 health loop writes on success) over ``fetcher.bare_path()``
so the loop remains the single source of truth on disk layout. The
fallback is only there so a freshly-fetched row missing the column does
not look broken.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.git_utils import run_git
from src.models import (
    RepoBlob,
    RepoBranches,
    RepoLog,
    RepoLogEntry,
    RepoTree,
    RepoTreeEntry,
)

if TYPE_CHECKING:  # pragma: no cover
    from src.repos.fetcher import RepoFetcher
    from src.repos.registry import RepoRegistryRepo

logger = logging.getLogger(__name__)


# Output caps — single layer of defaults. Anything larger is silently
# clamped except BLOB_SIZE_CAP_BYTES, which must reject before reading
# bytes off the object database.
DEFAULT_TREE_DEPTH = 2
DEFAULT_TREE_ENTRIES = 200
DEFAULT_LOG_LIMIT = 50
BLOB_SIZE_CAP_BYTES = 1 * 1024 * 1024  # 1 MiB


# Branch / ref / commit-sha allowlist. Rejects shell metacharacters and
# leading dashes (so ``ref="--upload-pack=evil"`` cannot reach git as an
# option flag — argument confusion guard, mirrors src.git_utils._BRANCH_RE).
_REF_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9/_.\-]{0,199}$")

# Path validation uses a deny-list (not an allowlist) so that real-world
# segments like ``@types``, ``(deprecated)``, or ``+page.svelte`` are not
# rejected. Args reach git via ``create_subprocess_exec`` (no shell), so
# the only attack we need to guard against here is git-option-confusion
# from a leading ``-`` and traversal via ``.`` / ``..``.
_PATH_MAX = 4096


def _validate_ref(ref: str) -> None:
    if not isinstance(ref, str) or not _REF_RE.match(ref):
        raise BadRequestError(
            f"invalid ref {ref!r}: must match [a-zA-Z0-9][a-zA-Z0-9/_.-]{{0,199}}"
        )


def _validate_path(path: str) -> None:
    if path == "":
        return
    if not isinstance(path, str):
        raise BadRequestError("path must be a string")
    if "\x00" in path:
        raise BadRequestError("path must not contain NUL")
    if len(path) > _PATH_MAX:
        raise BadRequestError(f"path exceeds {_PATH_MAX} chars")
    if path.startswith("/"):
        raise BadRequestError("path must not start with '/'")
    if path.startswith("-"):
        raise BadRequestError("path must not start with '-'")
    for seg in path.split("/"):
        if seg == "" or seg == "." or seg == "..":
            raise BadRequestError(f"invalid path segment in {path!r}")


class RepoInspector:
    """Pure-read inspector over a Phase 2 bare clone."""

    def __init__(
        self,
        fetcher: "RepoFetcher",
        registry: "RepoRegistryRepo",
        *,
        timeout_s: float | None = 30,
    ) -> None:
        self._fetcher = fetcher
        self._registry = registry
        self.timeout_s = timeout_s

    async def _resolve_bare(
        self, repo_id: str, *, row: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], Path]:
        if row is None:
            row = await self._registry.get(repo_id)
        if row is None:
            raise NotFoundError(f"repo not found: {repo_id!r}")
        status = row.get("fetch_status", "unknown")
        bare_str = row.get("bare_clone_path")
        if bare_str:
            bare = Path(bare_str)
        else:
            # Defensive fallback: a row written before bare_clone_path was
            # populated may still have a usable on-disk bare clone. Trust
            # the column over inference; only fall back when missing.
            bare = self._fetcher.bare_path(repo_id)
        # The inspector is read-only over a known-good clone, so we refuse
        # to serve a row that the health loop has not stamped 'healthy'.
        # 'error' rows would let stale data slip through silently; 'unknown'
        # rows (no fetch yet) just look broken to the caller.
        if status != "healthy":
            raise ConflictError(
                f"repo {repo_id!r} is not healthy "
                f"(fetch_status={status!r}); "
                f"call POST /api/v1/repos/{repo_id}/fetch first"
            )
        if not bare.exists():
            raise ConflictError(
                f"repo {repo_id!r} has no bare clone yet "
                f"(fetch_status={status!r}); "
                f"call POST /api/v1/repos/{repo_id}/fetch first"
            )
        return row, bare

    async def _git(
        self, *args: str, check: bool = True,
    ) -> tuple[str, str, int]:
        """Run a git command with the inspector's timeout, mapping the
        timeout-as-RuntimeError contract from RepoFetcher."""
        try:
            return await run_git(*args, check=check, timeout=self.timeout_s)
        except asyncio.TimeoutError as exc:  # pragma: no cover - timing-sensitive
            raise RuntimeError(
                f"git operation exceeded {self.timeout_s}s timeout"
            ) from exc

    async def branches(self, repo_id: str) -> RepoBranches:
        row, bare = await self._resolve_bare(repo_id)
        out, _, _ = await self._git(
            "--git-dir", str(bare),
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads/",
        )
        branches = [b for b in out.splitlines() if b.strip()]
        if not branches:
            # The request was well-formed; the repo is in an unusable state.
            # 409 matches the "no bare clone yet" case in _resolve_bare.
            raise ConflictError(
                f"repo {repo_id!r} has no branches in refs/heads/"
            )
        default = row.get("default_branch") or "main"
        # Default first if present; remaining sorted lexicographically.
        rest = sorted(b for b in branches if b != default)
        ordered = ([default] if default in branches else []) + rest
        return RepoBranches(default_branch=default, branches=ordered)

    async def rev_parse(
        self,
        repo_id: str,
        ref: str,
        *,
        _row: dict[str, Any] | None = None,
    ) -> str | None:
        """Return the commit sha for ``ref`` or ``None`` when unknown.

        Phase 4's creation-time validator relies on the ``None``-on-missing
        contract (treats it as "branch does not exist"). Don't change to
        raise on missing ref without auditing those callers.

        ``_row`` is an optional already-fetched ``repos`` row; passing it
        avoids a duplicate registry lookup when the caller has just done
        existence + health checks.
        """
        _validate_ref(ref)
        _, bare = await self._resolve_bare(repo_id, row=_row)
        out, _, rc = await self._git(
            "--git-dir", str(bare),
            "rev-parse", "--verify", f"{ref}^{{commit}}",
            check=False,
        )
        if rc != 0:
            return None
        sha = out.strip()
        return sha or None

    async def tree(
        self,
        repo_id: str,
        *,
        ref: str,
        path: str = "",
        depth: int = DEFAULT_TREE_DEPTH,
        max_entries: int = DEFAULT_TREE_ENTRIES,
    ) -> RepoTree:
        _validate_ref(ref)
        _validate_path(path)
        if depth is None or depth <= 0:
            depth = DEFAULT_TREE_DEPTH
        if max_entries is None or max_entries <= 0:
            max_entries = DEFAULT_TREE_ENTRIES
        # Defensive upper bound — clamp silently per PRD (no rejection).
        depth = min(int(depth), 16)
        max_entries = min(int(max_entries), 5000)

        # Verify ref before walking — surfaces a clean 400 instead of a
        # raw RuntimeError from ls-tree later.
        sha = await self.rev_parse(repo_id, ref)
        if sha is None:
            raise BadRequestError(f"ref not found in repo: {ref!r}")
        _, bare = await self._resolve_bare(repo_id)

        entries: list[RepoTreeEntry] = []
        truncated = False
        # BFS walk: queue holds (subpath_relative_to_root, current_depth).
        queue: deque[tuple[str, int]] = deque([(path, 1)])
        while queue:
            sub, level = queue.popleft()
            spec = f"{ref}:{sub}" if sub else f"{ref}:"
            try:
                out, _, _ = await self._git(
                    "--git-dir", str(bare),
                    "ls-tree", "-l", "-z", spec,
                )
            except RuntimeError as exc:
                # ``git ls-tree`` failed — most likely because ``sub`` is
                # not a tree (e.g. caller asked for a blob path). Surface
                # 400 rather than 500.
                raise BadRequestError(
                    f"could not list tree at {sub!r}: {exc}"
                ) from exc
            for raw in [r for r in out.split("\0") if r]:
                if len(entries) >= max_entries:
                    truncated = True
                    break
                # ``<mode> SP <type> SP <hash> [SP <size>]\t<path>``
                head, _, name = raw.partition("\t")
                parts = head.split()
                if len(parts) < 3:
                    continue
                mode, etype = parts[0], parts[1]
                size_token = parts[3] if len(parts) >= 4 else "-"
                size: int | None
                if size_token == "-" or etype != "blob":
                    size = None
                else:
                    try:
                        size = int(size_token)
                    except ValueError:
                        size = None
                if etype not in ("blob", "tree"):
                    continue
                full_path = f"{sub}/{name}" if sub else name
                entries.append(RepoTreeEntry(
                    path=full_path,
                    type=etype,  # type: ignore[arg-type]
                    mode=mode,
                    size=size,
                ))
                if etype == "tree" and level < depth:
                    queue.append((full_path, level + 1))
            if truncated:
                break
        return RepoTree(ref=ref, path=path, entries=entries, truncated=truncated)

    async def blob(
        self, repo_id: str, *, ref: str, path: str,
    ) -> RepoBlob:
        _validate_ref(ref)
        _validate_path(path)
        if not path:
            raise BadRequestError("blob path must not be empty")
        _, bare = await self._resolve_bare(repo_id)
        spec = f"{ref}:{path}"
        # Resolve the object id, then peek at type+size before reading
        # bytes — keeps the cap above the response and avoids buffering
        # multi-MiB blobs for nothing. ``rev-parse`` failure means the
        # object does not exist at that ref+path.
        oid_out, oid_err, rc = await self._git(
            "--git-dir", str(bare),
            "rev-parse", spec,
            check=False,
        )
        if rc != 0 or not oid_out.strip():
            raise BadRequestError(
                f"path not found at ref: {path!r} @ {ref!r}"
            )
        oid = oid_out.strip().splitlines()[0]
        type_out, _, _ = await self._git(
            "--git-dir", str(bare),
            "cat-file", "-t", oid,
        )
        otype = type_out.strip()
        if otype != "blob":
            raise BadRequestError(
                f"path is a {otype!r}, not a blob: {path!r}"
            )
        size_out, _, _ = await self._git(
            "--git-dir", str(bare),
            "cat-file", "-s", oid,
        )
        try:
            size = int(size_out.strip())
        except ValueError as exc:  # pragma: no cover - git contract
            raise RuntimeError(
                f"cat-file -s returned non-integer: {size_out!r}"
            ) from exc
        if size > BLOB_SIZE_CAP_BYTES:
            raise BadRequestError(
                f"blob exceeds {BLOB_SIZE_CAP_BYTES} byte cap "
                f"(actual={size}); refusing to read"
            )
        # ``run_git`` decodes + strips, which would corrupt binary blobs and
        # eat trailing newlines on text. Read raw bytes ourselves and apply
        # the same timeout-as-RuntimeError contract as ``_git``.
        raw = await self._cat_file_bytes(bare, oid)
        # NUL byte in the first 8KiB ⇒ binary. Catches images / archives
        # that happen to be valid UTF-8 but are not human-readable text.
        binary = b"\x00" in raw[:8192]
        if binary:
            text = None
        else:
            text = raw.decode("utf-8", errors="replace")
        return RepoBlob(
            ref=ref,
            path=path,
            size=size,
            binary=binary,
            content=text,
        )

    async def _cat_file_bytes(self, bare: Path, oid: str) -> bytes:
        """Read the raw bytes of object ``oid`` from ``bare``.

        Mirrors the timeout-as-RuntimeError contract that ``_git`` enforces
        for the rest of the inspector — so callers get one error type for
        any timeout regardless of which git command produced it.
        """
        proc = await asyncio.create_subprocess_exec(
            "git", "--git-dir", str(bare), "cat-file", "blob", oid,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            raw, err = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_s,
            )
        except asyncio.TimeoutError as exc:  # pragma: no cover - timing
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise RuntimeError(
                f"git operation exceeded {self.timeout_s}s timeout"
            ) from exc
        except asyncio.CancelledError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise
        if proc.returncode != 0:
            raise RuntimeError(
                f"git cat-file blob {oid} failed: "
                f"{err.decode('utf-8', errors='replace').strip()}"
            )
        return raw

    async def log(
        self,
        repo_id: str,
        *,
        ref: str,
        path: str | None = None,
        limit: int = DEFAULT_LOG_LIMIT,
        offset: int = 0,
    ) -> RepoLog:
        _validate_ref(ref)
        if path is not None and path != "":
            _validate_path(path)
        if limit is None or limit <= 0:
            limit = DEFAULT_LOG_LIMIT
        limit = min(int(limit), 500)
        if offset < 0:
            offset = 0
        _, bare = await self._resolve_bare(repo_id)
        args: list[str] = [
            "--git-dir", str(bare),
            "log",
            f"--skip={offset}",
            f"-n{limit}",
            "--pretty=format:%H%x09%an%x09%ae%x09%cI%x09%s",
            ref,
        ]
        if path:
            args += ["--", path]
        try:
            out, _, _ = await self._git(*args)
        except RuntimeError as exc:
            raise BadRequestError(
                f"could not log {ref!r}: {exc}"
            ) from exc
        entries: list[RepoLogEntry] = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            sha, author, email, committed, subject = parts[0], parts[1], parts[2], parts[3], "\t".join(parts[4:])
            entries.append(RepoLogEntry(
                sha=sha,
                author=author,
                email=email,
                committed_at=committed,
                subject=subject,
            ))
        return RepoLog(ref=ref, path=path, entries=entries)

    async def log_count(
        self,
        repo_id: str,
        *,
        ref: str,
        path: str | None = None,
    ) -> int:
        _validate_ref(ref)
        if path is not None and path != "":
            _validate_path(path)
        _, bare = await self._resolve_bare(repo_id)
        args: list[str] = [
            "--git-dir", str(bare),
            "rev-list",
            "--count",
            ref,
        ]
        if path:
            args += ["--", path]
        try:
            out, _, _ = await self._git(*args)
        except RuntimeError as exc:
            raise BadRequestError(
                f"could not count log entries for {ref!r}: {exc}"
            ) from exc
        try:
            return int(out.strip() or "0")
        except ValueError as exc:  # pragma: no cover - git contract
            raise RuntimeError(
                f"git rev-list --count returned non-integer: {out!r}"
            ) from exc
