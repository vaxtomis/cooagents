"""Periodic background fetcher for registered repos (Phase 2, repo-registry).

Mirrors :class:`src.agent_hosts.health_probe.HealthProbeLoop`: one
asyncio task, public ``probe_once`` for tests, swallow-per-iteration
errors so one bad repo does not stall the loop.

Stale detection runs at the start of each tick: any healthy row whose
``last_fetched_at`` is older than ``interval_s * _STALE_MULTIPLIER`` is
downgraded to ``stale``; the imminent fetch will then overwrite to
``healthy`` or ``error``. ``stale`` is therefore observable only when
the loop has been paused (process restart, network outage) — exactly
the PRD intent.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.repos.fetcher import RepoFetcher
    from src.repos.registry import RepoRegistryRepo

logger = logging.getLogger(__name__)

# v1: 3 missed cycles ⇒ stale (PRD L138). Codified as a module-level
# constant so the value has a name in tests instead of a magic 3.
_STALE_MULTIPLIER = 3


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
        await self._mark_stale(rows)
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

    async def _mark_stale(self, rows: list[dict[str, Any]]) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=self.interval_s * _STALE_MULTIPLIER,
        )
        for r in rows:
            if r.get("fetch_status") != "healthy":
                continue
            last = r.get("last_fetched_at")
            if not last:
                continue
            try:
                last_dt = datetime.fromisoformat(last)
            except ValueError:
                # Manual SQL edits or restored backups can corrupt this
                # column. Log so the silent skip doesn't hide the
                # corruption from ops.
                logger.warning(
                    "malformed last_fetched_at on repo %s: %r", r["id"], last,
                )
                continue
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if last_dt < cutoff:
                await self.registry.update_fetch_status(
                    r["id"], status="stale", err=None,
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
