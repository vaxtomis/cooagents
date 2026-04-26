"""Repo credential resolution (Phase 1, repo-registry).

v1 only resolves SSH keys from a filesystem path stored in
``repos.ssh_key_path``. Single function so tests / fetcher have one
seam to mock.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.exceptions import BadRequestError


@dataclass(frozen=True)
class SshKeyMaterial:
    """Resolved SSH credential material.

    Phase 2's fetcher is responsible for verifying the file actually exists
    and is readable on the host that will use it; this dataclass intentionally
    does no I/O.
    """

    private_key_path: Path


def resolve_repo_credential(repo: dict[str, Any]) -> SshKeyMaterial | None:
    """Return credential material for *repo* or ``None`` if unauthenticated.

    A repo row whose ``ssh_key_path`` is empty/None is treated as a public
    repository — callers should fall back to ambient SSH config.
    """
    ref = repo.get("ssh_key_path")
    if not ref:
        return None
    p = Path(ref).expanduser()
    if not p.is_absolute():
        # Reject relative paths early; the caller resolves cwd at every entry
        # point, so a relative ref is almost always a config bug rather than
        # an intentional repository-relative key.
        raise BadRequestError(
            f"ssh_key_path must be an absolute path, got {ref!r}"
        )
    return SshKeyMaterial(private_key_path=p)
