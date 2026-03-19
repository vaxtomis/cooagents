import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

from src.event_limits import can_emit_event

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, db, host_manager, job_manager, agent_executor, webhook_notifier, config, state_machine=None):
        self.db = db
        self.host_manager = host_manager
        self.jobs = job_manager
        self.executor = agent_executor
        self.webhooks = webhook_notifier
        self.config = config
        self.sm = state_machine
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
                        await self._tick_runnable_runs()
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
                    await self._handle_starting_job_timeout(dict(job), now)

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
                        await self._handle_job_timeout(j, now)

                await self._tick_runnable_runs()

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
                    await self._notify_limited(r["id"], "review.reminder", {
                        "run_id": r["id"],
                        "ticket": r["ticket"],
                        "stage": r["current_stage"],
                    }, limit_keys=("stage",))

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Reminder loop error: {e}")

    async def _tick_runnable_runs(self):
        """Tick auto-progress stages so queued and stale in-flight runs can reconcile."""
        if not self.sm:
            return
        runnable = await self.db.fetchall(
            "SELECT id FROM runs WHERE status='running' AND current_stage IN "
            "('DESIGN_QUEUED','DESIGN_DISPATCHED','DESIGN_RUNNING',"
            "'DEV_QUEUED','DEV_DISPATCHED','DEV_RUNNING')"
        )
        for run in runnable:
            try:
                await self.sm.tick(run["id"])
            except Exception as e:
                logger.error(f"Auto-tick run {run['id']} failed: {e}")

    async def _notify_limited(
        self,
        run_id: str,
        event_type: str,
        payload: dict,
        limit_keys: tuple[str, ...] = (),
        max_count: int = 3,
    ) -> None:
        match_fields = {key: payload.get(key) for key in limit_keys if key in payload}
        if not await can_emit_event(self.db, run_id, event_type, match_fields, max_count=max_count):
            return
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (run_id, event_type, json.dumps(payload), now),
        )
        await self.webhooks.notify(event_type, payload)

    async def _handle_starting_job_timeout(self, job: dict, now: datetime) -> None:
        await self.jobs.update_status(job["id"], "timeout", ended_at=now.isoformat())
        await self._notify_limited(
            job["run_id"],
            "job.timeout",
            {"run_id": job["run_id"], "job_id": job["id"], "stage": job.get("stage", "")},
            limit_keys=("job_id",),
        )
        if self.sm:
            await self.sm.tick(job["run_id"])

    async def _handle_job_timeout(self, job: dict, now: datetime) -> None:
        await self.executor.cancel_session(job["run_id"], job["agent_type"], final_status="timeout")
        await self._notify_limited(
            job["run_id"],
            "job.timeout",
            {"run_id": job["run_id"], "job_id": job["id"], "stage": job.get("stage", "")},
            limit_keys=("job_id",),
        )
        if self.sm:
            await self.sm.tick(job["run_id"])
