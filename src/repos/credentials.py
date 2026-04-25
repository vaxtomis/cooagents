"""Repo credential resolution (Phase 1, repo-registry).

v1 only resolves SSH keys from a filesystem path stored in
``repos.credential_ref``. The function is the single seam every caller will
go through, so a future Vault/secret-store implementation can be added as a
new branch without touching call sites.
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

    A repo row whose ``credential_ref`` is empty/None is treated as a public
    repository — callers should fall back to ambient SSH config.
    """
    ref = repo.get("credential_ref")
    if not ref:
        return None
    return _path_resolver(ref)


def _path_resolver(ref: str) -> SshKeyMaterial:
    p = Path(ref).expanduser()
    if not p.is_absolute():
        # Reject relative paths early; the caller resolves cwd at every entry
        # point, so a relative ref is almost always a config bug rather than
        # an intentional repository-relative key.
        raise BadRequestError(
            f"credential_ref must be an absolute path, got {ref!r}"
        )
    return SshKeyMaterial(private_key_path=p)
