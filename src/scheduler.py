import asyncio
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, db, host_manager, job_manager, agent_executor, webhook_notifier, config):
        self.db = db
        self.host_manager = host_manager
        self.jobs = job_manager
        self.executor = agent_executor
        self.webhooks = webhook_notifier
        self.config = config
        self._tasks = []

    async def start(self):
        self._tasks.append(asyncio.create_task(self._health_check_loop()))
        self._tasks.append(asyncio.create_task(self._timeout_enforcement_loop()))
        self._tasks.append(asyncio.create_task(self._reminder_loop()))

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _health_check_loop(self):
        while True:
            try:
                await asyncio.sleep(self.config.health_check.interval)
                hosts = await self.host_manager.list_all()
                for host in hosts:
                    old_status = host["status"]
                    is_online = await self.host_manager.health_check(host["id"])
                    if old_status == "active" and not is_online:
                        await self.webhooks.notify("host.offline", {"host_id": host["id"]})
                    elif old_status == "offline" and is_online:
                        await self.webhooks.notify("host.online", {"host_id": host["id"]})
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Health check error: {e}")

    async def _timeout_enforcement_loop(self):
        while True:
            try:
                await asyncio.sleep(30)
                now = datetime.now(timezone.utc)

                # Check dispatch startup timeout
                dispatch_timeout = self.config.timeouts.dispatch_startup
                cutoff = (now - timedelta(seconds=dispatch_timeout)).isoformat()
                stale_starting = await self.db.fetchall(
                    "SELECT * FROM jobs WHERE status='starting' AND started_at < ?", (cutoff,)
                )
                for job in stale_starting:
                    j = dict(job)
                    await self.jobs.update_status(j["id"], "failed", ended_at=now.isoformat())
                    await self.webhooks.notify("job.timeout", {"job_id": j["id"], "run_id": j["run_id"]})

                # Check running job timeouts
                running_jobs = await self.db.fetchall(
                    "SELECT * FROM jobs WHERE status='running'"
                )
                for job in running_jobs:
                    j = dict(job)
                    started = datetime.fromisoformat(j["started_at"])
                    stage = j.get("stage", "")
                    if "DESIGN" in stage:
                        timeout = self.config.timeouts.design_execution
                    else:
                        timeout = self.config.timeouts.dev_execution
                    if (now - started).total_seconds() > timeout:
                        await self.executor.cancel(j["id"])
                        await self.webhooks.notify("job.timeout", {"job_id": j["id"], "run_id": j["run_id"]})

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Timeout enforcement error: {e}")

    async def _reminder_loop(self):
        while True:
            try:
                await asyncio.sleep(3600)
                now = datetime.now(timezone.utc)

                # Review reminders: runs in REVIEW stages > 24h
                review_cutoff = (now - timedelta(seconds=self.config.timeouts.review_reminder)).isoformat()
                review_runs = await self.db.fetchall(
                    "SELECT * FROM runs WHERE status='running' AND current_stage IN ('REQ_REVIEW','DESIGN_REVIEW','DEV_REVIEW') AND updated_at < ?",
                    (review_cutoff,)
                )
                for run in review_runs:
                    r = dict(run)
                    await self.webhooks.notify("review.reminder", {
                        "run_id": r["id"],
                        "ticket": r["ticket"],
                        "stage": r["current_stage"],
                    })

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Reminder loop error: {e}")
