"""Periodic background fetcher for registered repos (Phase 2, repo-registry).

Mirrors :class:`src.agent_hosts.health_probe.HealthProbeLoop`: one
asyncio task, public ``probe_once`` for tests, swallow-per-iteration
errors so one bad repo does not stall the loop.

Status writes go through :class:`RepoRegistryRepo`; the loop is the
**only** writer of ``healthy``. Operators infer freshness from
``last_fetched_at`` directly — there is no separate ``stale`` state.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.repos.fetcher import RepoFetcher
    from src.repos.registry import RepoRegistryRepo

logger = logging.getLogger(__name__)


class RepoHealthLoop:
    """Drive ``RepoFetcher.fetch_or_clone`` over every registered repo.

    Single source of truth for ``fetch_status`` writes — the fetcher itself
    is pure I/O. On success the loop writes ``healthy``; on exception it
    writes ``error`` with a sanitised stderr.
    """

    def __init__(
        self,
        fetcher: "RepoFetcher",
        registry: "RepoRegistryRepo",
        *,
        interval_s: int = 300,
        parallel: int = 4,
    ) -> None:
        self.fetcher = fetcher
        self.registry = registry
        self.interval_s = interval_s
        self.parallel = parallel
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._run(), name="repo-health-loop",
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
        """Fetch every registered repo once. Public for tests to drive."""
        rows = await self.registry.list_all()
        # Semaphore created here, not in __init__: pytest-asyncio creates a
        # new event loop per test and a Semaphore bound to a closed loop
        # raises on the next acquire.
        sem = asyncio.Semaphore(self.parallel)

        async def _one(r: dict[str, Any]) -> None:
            repo_id = r["id"]
            async with sem:
                try:
                    outcome = await self.fetcher.fetch_or_clone(r)
                    logger.info("repo %s: %s", repo_id, outcome)
                except Exception as exc:
                    logger.exception("repo fetch %s failed", repo_id)
                    try:
                        await self.registry.update_fetch_status(
                            repo_id, status="error", err=str(exc),
                        )
                    except Exception:
                        logger.exception(
                            "could not record fetch error for %s", repo_id,
                        )
                    return
                # Success path: loop is the **only** writer of healthy
                # state. ``bare_clone_path`` is content-addressed by
                # ``repo_id`` so passing it on every successful tick is
                # idempotent.
                try:
                    await self.registry.update_fetch_status(
                        repo_id,
                        status="healthy",
                        err=None,
                        bare_clone_path=str(
                            self.fetcher.bare_path(repo_id)
                        ),
                    )
                except Exception:
                    logger.exception(
                        "could not record healthy state for %s", repo_id,
                    )

        await asyncio.gather(
            *[_one(r) for r in rows], return_exceptions=False,
        )

    async def _run(self) -> None:
        while True:
            try:
                await self.probe_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("repo health loop iteration failed")
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                raise
