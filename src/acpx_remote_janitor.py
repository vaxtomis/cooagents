"""Coordinator for cleanup passes on SSH agent hosts."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.models import LOCAL_HOST_ID

logger = logging.getLogger(__name__)


class RemoteAcpxJanitor:
    """Periodically asks each SSH host to run its local cleanup pass."""

    def __init__(
        self,
        *,
        agent_host_repo: Any,
        ssh_dispatcher: Any,
        interval_s: int = 60,
        terminate_grace_s: int = 15,
        kill_grace_s: int = 10,
        kill_enabled: bool = True,
    ) -> None:
        self.agent_host_repo = agent_host_repo
        self.ssh_dispatcher = ssh_dispatcher
        self.interval_s = interval_s
        self.terminate_grace_s = terminate_grace_s
        self.kill_grace_s = kill_grace_s
        self.kill_enabled = kill_enabled
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="acpx-remote-janitor")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await asyncio.gather(self._task, return_exceptions=True)
        finally:
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("remote acpx janitor iteration failed")
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                raise

    async def cleanup_once(self) -> None:
        hosts = await self.agent_host_repo.list_all()
        for host in hosts:
            host_id = host.get("id")
            if not host_id or host.get("host") == LOCAL_HOST_ID:
                continue
            try:
                _stdout, rc = await self.ssh_dispatcher.cleanup_remote(
                    host_id,
                    terminate_grace_s=self.terminate_grace_s,
                    kill_grace_s=self.kill_grace_s,
                    kill_enabled=self.kill_enabled,
                    timeout_s=max(
                        30, self.terminate_grace_s + self.kill_grace_s + 15
                    ),
                )
                if rc != 0:
                    logger.warning(
                        "remote acpx cleanup failed host=%s rc=%s",
                        host_id, rc,
                    )
            except Exception:
                logger.warning(
                    "remote acpx cleanup failed host=%s",
                    host_id,
                    exc_info=True,
                )
