"""Repo registry public API (Phase 1 + Phase 2 + Phase 3).

Components:
  * :class:`RepoRegistryRepo` — DB-layer CRUD for ``repos`` (Phase 1)
  * :class:`SshKeyMaterial` — frozen dataclass returned by the credential resolver
  * :func:`resolve_repo_credential` — single seam for credential resolution
  * :class:`RepoFetcher` — pure-I/O bare-clone driver (Phase 2)
  * :class:`RepoHealthLoop` — periodic fetch task; only writer of healthy/error (Phase 2)
  * :class:`RepoInspector` — pure-read branches/tree/blob/log/rev_parse (Phase 3)
"""
from src.repos.credentials import SshKeyMaterial, resolve_repo_credential
from src.repos.fetcher import RepoFetcher
from src.repos.health_loop import RepoHealthLoop
from src.repos.inspector import RepoInspector
from src.repos.registry import RepoRegistryRepo

__all__ = [
    "RepoFetcher",
    "RepoHealthLoop",
    "RepoInspector",
    "RepoRegistryRepo",
    "SshKeyMaterial",
    "resolve_repo_credential",
]
