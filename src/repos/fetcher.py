"""Bare-clone fetcher for the repo registry (Phase 2, repo-registry).

Owns the on-disk layout for control-plane bare clones:
    <workspaces_root>/.coop/registry/repos/<repo_id>.git

Reads ``repos`` rows, runs ``git clone --bare`` on first contact, then
``git fetch --prune`` on subsequent invocations. SSH key material is
injected via ``GIT_SSH_COMMAND``; the resolver is the single seam (Phase 1
:func:`src.repos.credentials.resolve_repo_credential`).

This module is **pure I/O**: it never reads or writes the ``repos`` table.
Callers (``RepoHealthLoop`` and the ``POST /repos/{id}/fetch`` route)
own status writes. Failures bubble out as exceptions so callers can
decide whether to record ``error`` or surface 502 to the user.

This module never commits or pushes — by PRD design the cooagents host
bare clone is read-only. Worktrees / writes happen on the agent host.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
from pathlib import Path
from typing import Any

from src.git_utils import run_git
from src.repos.credentials import SshKeyMaterial, resolve_repo_credential

logger = logging.getLogger(__name__)


class RepoFetcher:
    """Clone / fetch driver for registered repos. Pure I/O — no DB writes."""

    def __init__(
        self,
        *,
        workspaces_root: Path,
        strict_host_key: bool = True,
        known_hosts_path: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self._registry_root = (
            Path(workspaces_root) / ".coop" / "registry" / "repos"
        )
        self.strict_host_key = strict_host_key
        # Wall-clock cap per ``git`` invocation. ``BatchMode=yes`` only
        # prevents auth prompts; a stalled TCP connect can still hang the
        # event loop. None disables the timeout (used by tests).
        self.timeout_s = timeout_s
        # ``~`` already expanded by AgentsConfig field validator (when reused);
        # belt-and-braces here too in case a test injects a literal ``~/...``.
        self._known_hosts_path = (
            str(Path(known_hosts_path).expanduser())
            if known_hosts_path else None
        )

    def bare_path(self, repo_id: str) -> Path:
        """Return the canonical bare-clone path for ``repo_id``."""
        return self._registry_root / f"{repo_id}.git"

    async def fetch_or_clone(self, repo: dict[str, Any]) -> str:
        """Clone if the bare directory is absent, otherwise fetch.

        Returns ``"cloned"`` or ``"fetched"`` for caller logging /
        observability. Raises on failure — the caller decides whether to
        write ``status='error'`` (loop) or surface a 502 (HTTP route).

        On timeout we re-raise as ``RuntimeError`` so the loop's generic
        ``except Exception`` writes a human-readable ``last_fetch_err``
        instead of an empty ``TimeoutError`` repr.
        """
        bare = self.bare_path(repo["id"])
        env = self._build_env(repo)
        try:
            if bare.exists():
                await self._fetch(bare, env)
                return "fetched"
            await self._clone(repo, bare, env)
            return "cloned"
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"git operation exceeded {self.timeout_s}s timeout"
            ) from exc

    async def _clone(
        self, repo: dict[str, Any], bare: Path, env: dict[str, str],
    ) -> None:
        bare.parent.mkdir(parents=True, exist_ok=True)
        # ``--bare`` (not ``--mirror``): only refs/heads/*; no PR refs / notes.
        await run_git(
            "clone", "--bare", repo["url"], str(bare),
            check=True, env=env, timeout=self.timeout_s,
        )

    async def _fetch(self, bare: Path, env: dict[str, str]) -> None:
        # ``--prune`` deletes server-side gone refs locally; without it
        # deleted branches accumulate forever in the bare clone.
        await run_git(
            "--git-dir", str(bare),
            "fetch", "--prune", "origin",
            check=True, env=env, timeout=self.timeout_s,
        )

    def _build_env(self, repo: dict[str, Any]) -> dict[str, str]:
        # Inherit parent env so PATH / HOME / SSH_AUTH_SOCK survive; only
        # override GIT_SSH_COMMAND when the repo has an explicit key.
        env = dict(os.environ)
        cred = resolve_repo_credential(repo)
        if cred is None:
            return env
        env["GIT_SSH_COMMAND"] = self._ssh_command(cred)
        return env

    def _ssh_command(self, cred: SshKeyMaterial) -> str:
        # ``IdentitiesOnly=yes`` forces git to use this key; without it ssh
        # falls back to the agent and we silently authenticate as the wrong
        # identity. ``BatchMode=yes`` makes ssh fail fast on prompt instead
        # of hanging the asyncio loop.
        parts = [
            "ssh",
            "-i", shlex.quote(str(cred.private_key_path)),
            "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes",
        ]
        if self.strict_host_key:
            parts += ["-o", "StrictHostKeyChecking=yes"]
            if self._known_hosts_path:
                parts += [
                    "-o",
                    f"UserKnownHostsFile={shlex.quote(self._known_hosts_path)}",
                ]
        else:
            # ``accept-new`` mirrors PRD intent: trust on first use, refuse
            # silently changed keys. Never use ``no``.
            parts += ["-o", "StrictHostKeyChecking=accept-new"]
        return " ".join(parts)
