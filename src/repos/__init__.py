"""Repo registry public API (Phase 1).

Components:
  * :class:`RepoRegistryRepo` — DB-layer CRUD for ``repos``
  * :class:`SshKeyMaterial` — frozen dataclass returned by the credential resolver
  * :func:`resolve_repo_credential` — single seam for credential resolution

Phase 2 will add ``fetcher`` / ``health_loop`` / ``inspector`` modules behind
this same barrel without changing the v1 import surface.
"""
from src.repos.credentials import SshKeyMaterial, resolve_repo_credential
from src.repos.registry import RepoRegistryRepo

__all__ = [
    "RepoRegistryRepo",
    "SshKeyMaterial",
    "resolve_repo_credential",
]
