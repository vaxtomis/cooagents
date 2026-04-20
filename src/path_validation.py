"""Validators for user-supplied repo paths and URLs.

Why: the API accepts `repo_path` (filesystem) and `repo_url` (clone source)
from clients. Without restriction these enable arbitrary filesystem writes and
SSRF via `git clone http://169.254.169.254/...` or `file://`. This module
centralises the allowlists declared in `config.security`.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


class RepoPathError(ValueError):
    pass


class RepoUrlError(ValueError):
    pass


def validate_repo_path(repo_path: str, workspace_root: Path) -> Path:
    """Resolve repo_path and require it to sit under workspace_root.

    Accepts both existing and non-existing paths (git clone may create them).
    Rejects symlinks or traversal that escape the workspace.
    """
    if not repo_path:
        raise RepoPathError("repo_path is required")
    candidate = Path(repo_path).expanduser()
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise RepoPathError(f"repo_path cannot be resolved: {exc}") from exc
    root = workspace_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RepoPathError(
            f"repo_path must live under workspace_root ({root}); got {resolved}"
        ) from exc
    return resolved


def validate_repo_url(
    repo_url: str,
    allowed_hosts: list[str],
    allowed_schemes: list[str],
) -> str:
    """Accept only clone URLs whose host is in the allowlist.

    Handles three shapes:
        https://github.com/owner/repo.git
        ssh://git@github.com/owner/repo.git
        git@github.com:owner/repo.git    (scp-like, no urlparse support)
    """
    if not repo_url:
        raise RepoUrlError("repo_url is required")

    url = repo_url.strip()

    if "://" not in url and url.count(":") == 1 and "@" in url.split(":", 1)[0]:
        # scp-like: user@host:path
        user_host, path = url.split(":", 1)
        host = user_host.split("@", 1)[1] if "@" in user_host else user_host
        host = host.lower()
        # Defence: `git@github.com:@evil.com/repo` would otherwise pass the host
        # check but some git versions reinterpret the path as a new host. Ban
        # chars that could start a second URL or host reference.
        if any(ch in path for ch in ("@", "://", "\n", "\r", "\t", " ")):
            raise RepoUrlError("scp-like repo path must not contain @, ://, or whitespace")
        if any(ch in user_host for ch in ("\n", "\r", "\t", " ")):
            raise RepoUrlError("scp-like user/host must not contain whitespace")
        if host not in {h.lower() for h in allowed_hosts}:
            raise RepoUrlError(f"host {host!r} is not in allowlist {allowed_hosts}")
        return url

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {s.lower() for s in allowed_schemes}:
        raise RepoUrlError(f"scheme {scheme!r} not allowed; expected one of {allowed_schemes}")
    if not host:
        raise RepoUrlError("repo_url must include a host")
    if host not in {h.lower() for h in allowed_hosts}:
        raise RepoUrlError(f"host {host!r} is not in allowlist {allowed_hosts}")
    return url
