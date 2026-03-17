import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timezone


class AgentExecutor:
    def __init__(self, db, job_manager, host_manager, artifact_manager, webhook_notifier, config=None, coop_dir=".coop"):
        self.db = db
        self.jobs = job_manager
        self.hosts = host_manager
        self.artifacts = artifact_manager
        self.webhooks = webhook_notifier
        self.config = config
        self.coop_dir = coop_dir
        self._state_machine = None  # Set later to avoid circular dep
        self._tasks = {}  # job_id → asyncio.Task

    def set_state_machine(self, sm):
        self._state_machine = sm

    async def dispatch(self, run_id, host, agent_type, task_file, worktree, timeout_sec) -> str:
        from src.git_utils import get_head_commit
        base_commit = await get_head_commit(worktree)

        run = await self.db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
        stage = run["current_stage"] if run else "UNKNOWN"

        job_id = await self.jobs.create_job(
            run_id, host["id"], agent_type, stage, task_file, worktree, base_commit, timeout_sec
        )

        # Build command
        cmd_parts = self._build_command(agent_type, task_file)

        # Start process
        if host["host"] == "local":
            process = await self._run_local(cmd_parts, worktree, job_id)
        else:
            process = await self._run_ssh(host, cmd_parts, worktree, job_id)

        # Update job status to running
        await self.jobs.update_status(job_id, "running")
        await self.hosts.increment_load(host["id"])

        # Launch background watcher
        task = asyncio.create_task(self._watch(job_id, process, run_id, host["id"], timeout_sec))
        self._tasks[job_id] = task

        return job_id

    def _build_command(self, agent_type, task_file):
        task_content = Path(task_file).read_text(encoding="utf-8")
        if agent_type == "claude":
            return ["claude", "-p", task_content, "--output-format", "json", "--max-turns", "50"]
        else:
            return ["codex", "-q", "--prompt", task_content]

    async def _run_local(self, cmd_parts, worktree, job_id):
        log_dir = Path(self.coop_dir) / "jobs" / job_id
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_file = open(log_dir / "stdout.log", "w", encoding="utf-8")
        stderr_file = open(log_dir / "stderr.log", "w", encoding="utf-8")

        process = await asyncio.create_subprocess_exec(
            *cmd_parts,
            cwd=worktree,
            stdout=stdout_file,
            stderr=stderr_file,
        )
        return process

    async def _run_ssh(self, host, cmd_parts, worktree, job_id):
        import asyncssh
        import shlex

        remote_cmd = f"cd {shlex.quote(worktree)} && {' '.join(shlex.quote(c) for c in cmd_parts)}"

        connect_args = {"host": host["host"], "known_hosts": None}
        if host.get("ssh_key"):
            connect_args["client_keys"] = [host["ssh_key"]]

        conn = await asyncssh.connect(**connect_args)
        process = await conn.create_process(remote_cmd)
        return process

    async def _watch(self, job_id, process, run_id, host_id, timeout_sec):
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_sec)
            rc = process.returncode
            now = datetime.now(timezone.utc).isoformat()

            if rc == 0:
                await self.jobs.update_status(job_id, "completed", ended_at=now)
                await self._on_complete(job_id, run_id)
            else:
                await self.jobs.update_status(job_id, "failed", ended_at=now)
                await self._emit_event(run_id, "job.failed", {"job_id": job_id, "exit_code": rc})
        except asyncio.TimeoutError:
            process.kill()
            now = datetime.now(timezone.utc).isoformat()
            await self._on_interrupted(job_id, run_id, "timeout")
            await self.jobs.update_status(job_id, "timeout", ended_at=now)
        except Exception as e:
            now = datetime.now(timezone.utc).isoformat()
            await self.jobs.update_status(job_id, "failed", ended_at=now)
            await self._emit_event(run_id, "job.error", {"job_id": job_id, "error": str(e)})
        finally:
            await self.hosts.decrement_load(host_id)
            self._tasks.pop(job_id, None)

    async def _on_complete(self, job_id, run_id):
        await self._emit_event(run_id, "job.completed", {"job_id": job_id})
        if self._state_machine:
            await self._state_machine.tick(run_id)

    async def _on_interrupted(self, job_id, run_id, reason):
        from src.git_utils import stash_save
        job = await self.db.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
        if job and job["worktree"]:
            stashed = await stash_save(job["worktree"], f"interrupted-{job_id}")
            snapshot = {
                "reason": reason,
                "stashed": stashed,
                "base_commit": job.get("base_commit"),
            }
            await self.jobs.update_status(job_id, "interrupted", snapshot_json=json.dumps(snapshot))
        await self._emit_event(run_id, "job.interrupted", {"job_id": job_id, "reason": reason})

    async def cancel(self, job_id):
        job = await self.db.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            return
        task = self._tasks.get(job_id)
        if task:
            task.cancel()
        now = datetime.now(timezone.utc).isoformat()
        await self.jobs.update_status(job_id, "cancelled", ended_at=now)

    async def recover(self, run_id, action):
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,)
        )
        if not job:
            return

        if action == "resume":
            from src.git_utils import stash_pop
            if job["worktree"]:
                await stash_pop(job["worktree"])
            # Re-dispatch with resume task
            resume_count = (job.get("resume_count") or 0) + 1
            await self.db.execute(
                "UPDATE jobs SET resume_count=? WHERE id=?", (resume_count, job["id"])
            )
        elif action == "redo":
            from src.git_utils import run_git
            if job["worktree"] and job.get("base_commit"):
                await run_git("reset", "--hard", job["base_commit"], cwd=job["worktree"])
        # manual: no action needed

    async def restore_on_startup(self):
        jobs = await self.db.fetchall(
            "SELECT * FROM jobs WHERE status IN ('starting','running')"
        )
        now = datetime.now(timezone.utc).isoformat()
        for job in jobs:
            await self.jobs.update_status(dict(job)["id"], "interrupted", ended_at=now)

    async def _emit_event(self, run_id, event_type, payload):
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (run_id, event_type, json.dumps(payload), now)
        )
        if self.webhooks:
            await self.webhooks.notify(event_type, {"run_id": run_id, **payload})
