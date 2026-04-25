"""Background asyncio task that polls every agent host (Phase 8a)."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent_hosts.repo import AgentHostRepo
    from src.agent_hosts.ssh_dispatcher import SshDispatcher

logger = logging.getLogger(__name__)


class HealthProbeLoop:
    """Periodically run :meth:`SshDispatcher.healthcheck` for every host.

    One bad host must not stall the loop — failures from probe call to
    update are swallowed per host with a logged exception, and the next
    host in the list is still probed in the same iteration.
    """

    def __init__(
        self,
        dispatcher: "SshDispatcher",
        repo: "AgentHostRepo",
        *,
        interval_s: int = 60,
    ) -> None:
        self.dispatcher = dispatcher
        self.repo = repo
        self.interval_s = interval_s
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._run(), name="agent-health-probe"
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await asyncio.gather(self._task, return_exceptions=True)
        finally:
            self._task = None

    async def probe_once(self) -> None:
        """Probe every host exactly once. Public so tests can drive a tick."""
        for h in await self.repo.list_all():
            host_id = h["id"]
            try:
                result = await self.dispatcher.healthcheck(host_id)
                await self.repo.update_health(
                    host_id,
                    status=result["health_status"],
                    err=result.get("last_health_err"),
                )
            except Exception as exc:
                logger.exception("health probe %s failed", host_id)
                try:
                    await self.repo.update_health(
                        host_id, status="unhealthy", err=str(exc)
                    )
                except Exception:  # final guard — never let one host kill the loop
                    logger.exception(
                        "could not record unhealthy state for %s", host_id
                    )

    async def _run(self) -> None:
        while True:
            try:
                await self.probe_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("health probe loop iteration failed")
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                raise
