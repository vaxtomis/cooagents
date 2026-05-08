"""Manual DevWork branch publisher.

Commits dirty per-mount worktrees and pushes their DevWork branches on
operator request. This service owns git I/O only; the route layer owns HTTP
state checks and :class:`DevWorkRepoStateRepo` owns push_state persistence.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.git_utils import COMMIT_FMT, run_git
from src.repos.credentials import SshKeyMaterial, resolve_repo_credential
from src.repos.dev_work_repo_state import DevWorkRepoStateRepo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishMountResult:
    mount_name: str
    repo_id: str
    status: str
    error: str | None = None


@dataclass(frozen=True)
class PublishReport:
    dev_work_id: str
    results: list[PublishMountResult]


class DevWorkPublisher:
    """Commit and push DevWork-owned branches for each bound mount."""

    def __init__(
        self,
        state_repo: DevWorkRepoStateRepo,
        *,
        timeout_s: float | None = None,
        strict_host_key: bool = True,
        known_hosts_path: str | None = None,
    ) -> None:
        self._state_repo = state_repo
        self.timeout_s = timeout_s
        self.strict_host_key = strict_host_key
        self._known_hosts_path = (
            str(Path(known_hosts_path).expanduser())
            if known_hosts_path else None
        )

    async def publish(self, dev_work_id: str, round_n: int) -> PublishReport:
        rows = await self._state_repo.list_for_dev_work(dev_work_id)
        results: list[PublishMountResult] = []
        for row in rows:
            if row["push_state"] == "pushed":
                results.append(
                    PublishMountResult(
                        mount_name=row["mount_name"],
                        repo_id=row["repo_id"],
                        status="skipped",
                    )
                )
                continue
            try:
                await self._publish_one(dev_work_id, round_n, row)
            except Exception as exc:
                logger.exception(
                    "dev_work publish failed dev_work_id=%s repo_id=%s mount=%s",
                    dev_work_id,
                    row.get("repo_id"),
                    row.get("mount_name"),
                )
                err = str(exc)
                try:
                    await self._state_repo.update_push_state(
                        dev_work_id,
                        row["mount_name"],
                        push_state="failed",
                        error_msg=err,
                    )
                except Exception:
                    logger.exception(
                        "could not record publish failure dev_work_id=%s "
                        "repo_id=%s mount=%s",
                        dev_work_id,
                        row.get("repo_id"),
                        row.get("mount_name"),
                    )
                results.append(
                    PublishMountResult(
                        mount_name=row["mount_name"],
                        repo_id=row["repo_id"],
                        status="failed",
                        error=err,
                    )
                )
                continue

            await self._state_repo.update_push_state(
                dev_work_id,
                row["mount_name"],
                push_state="pushed",
            )
            results.append(
                PublishMountResult(
                    mount_name=row["mount_name"],
                    repo_id=row["repo_id"],
                    status="pushed",
                )
            )
        return PublishReport(dev_work_id=dev_work_id, results=results)

    async def _publish_one(
        self,
        dev_work_id: str,
        round_n: int,
        row: dict[str, Any],
    ) -> None:
        worktree_raw = row.get("worktree_path")
        if not worktree_raw:
            raise RuntimeError(
                f"worktree_path missing for mount {row['mount_name']!r}"
            )
        worktree = Path(worktree_raw)
        if not worktree.exists():
            raise RuntimeError(
                f"worktree_path does not exist for mount "
                f"{row['mount_name']!r}: {worktree}"
            )

        env = self._build_env(row)
        status_out, _, _ = await self._git(
            "status", "--porcelain", cwd=str(worktree), env=env,
        )
        if status_out.strip():
            await self._git("add", "-A", cwd=str(worktree), env=env)
            slug, dw_short = self._commit_parts(
                dev_work_id, row["devwork_branch"],
            )
            msg = COMMIT_FMT.format(
                slug=slug,
                dw_short=dw_short,
                round=round_n,
                step="completed",
            )
            await self._git(
                "-c",
                "user.name=DevWork",
                "-c",
                "user.email=devwork@cooagents.local",
                "commit",
                "-m",
                msg,
                cwd=str(worktree),
                env=env,
            )

        await self._git(
            "push",
            "origin",
            f"HEAD:{row['devwork_branch']}",
            cwd=str(worktree),
            env=env,
        )

    async def _git(self, *args, **kwargs):
        try:
            return await run_git(*args, timeout=self.timeout_s, **kwargs)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"git operation exceeded {self.timeout_s}s timeout"
            ) from exc

    def _build_env(self, repo: dict[str, Any]) -> dict[str, str]:
        env = dict(os.environ)
        cred = resolve_repo_credential(repo)
        if cred is None:
            return env
        env["GIT_SSH_COMMAND"] = self._ssh_command(cred)
        return env

    def _ssh_command(self, cred: SshKeyMaterial) -> str:
        parts = [
            "ssh",
            "-i",
            shlex.quote(str(cred.private_key_path)),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
        ]
        if self.strict_host_key:
            parts += ["-o", "StrictHostKeyChecking=yes"]
            if self._known_hosts_path:
                parts += [
                    "-o",
                    f"UserKnownHostsFile={shlex.quote(self._known_hosts_path)}",
                ]
        else:
            parts += ["-o", "StrictHostKeyChecking=accept-new"]
        return " ".join(parts)

    @staticmethod
    def _commit_parts(dev_work_id: str, branch: str) -> tuple[str, str]:
        parts = branch.split("/")
        if len(parts) >= 3 and parts[0] == "devwork":
            return parts[1], parts[2]
        return "unknown", dev_work_id.removeprefix("dev-")
