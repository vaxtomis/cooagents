"""Repo registry public API (Phase 1 + Phase 2 + Phase 3 + Phase 5).

Components:
  * :class:`RepoRegistryRepo` — DB-layer CRUD for ``repos`` (Phase 1)
  * :class:`SshKeyMaterial` — frozen dataclass returned by the credential resolver
  * :func:`resolve_repo_credential` — single seam for credential resolution
  * :class:`RepoFetcher` — pure-I/O bare-clone driver (Phase 2)
  * :class:`RepoHealthLoop` — periodic fetch task; only writer of healthy/error (Phase 2)
  * :class:`RepoInspector` — pure-read branches/tree/blob/log/rev_parse (Phase 3)
  * :class:`DevWorkRepoStateRepo` — single seam for dev_work_repos.push_state (Phase 5)
"""
from src.repos.credentials import SshKeyMaterial, resolve_repo_credential
from src.repos.dev_work_repo_state import DevWorkRepoStateRepo
from src.repos.fetcher import RepoFetcher
from src.repos.health_loop import RepoHealthLoop
from src.repos.inspector import RepoInspector
from src.repos.registry import RepoRegistryRepo

__all__ = [
    "DevWorkRepoStateRepo",
    "RepoFetcher",
    "RepoHealthLoop",
    "RepoInspector",
    "RepoRegistryRepo",
    "SshKeyMaterial",
    "resolve_repo_credential",
]
