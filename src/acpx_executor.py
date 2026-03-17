import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timezone


# acpx exit code -> JobStatus mapping
_EXIT_CODE_MAP = {
    0: "completed",
    1: "failed",
    2: "failed",
    3: "timeout",
    4: "failed",
    5: "failed",
    130: "interrupted",
}


class AcpxExecutor:
    def __init__(self, db, job_manager, host_manager, artifact_manager, webhook_notifier, config=None, coop_dir=".coop"):
        self.db = db
        self.jobs = job_manager
        self.hosts = host_manager
        self.artifacts = artifact_manager
        self.webhooks = webhook_notifier
        self.config = config
        self.coop_dir = coop_dir
        self._state_machine = None
        self._tasks = {}  # job_id -> asyncio.Task

    def set_state_machine(self, sm):
        self._state_machine = sm

    # ------------------------------------------------------------------
    # Session name helpers
    # ------------------------------------------------------------------

    def _make_session_name(self, run_id, phase, revision=None):
        name = f"{run_id}-{phase}"
        if revision and revision > 1:
            name += f"-r{revision}"
        return name

    def _map_exit_code(self, rc):
        return _EXIT_CODE_MAP.get(rc, "failed")

    # ------------------------------------------------------------------
    # Command builders
    # ------------------------------------------------------------------

    def _build_acpx_prompt_cmd(self, agent_type, session_name, worktree, timeout_sec, task_file=None):
        agent = "claude" if agent_type == "claude" else "codex"
        cmd = [
            "acpx", agent,
            "-s", session_name,
            "--cwd", worktree,
            "--format", "json",
            "--approve-all",
            "--timeout", str(timeout_sec),
        ]
        if task_file:
            cmd += ["--file", task_file]
        return cmd

    def _build_acpx_ensure_cmd(self, agent_type, session_name, worktree):
        agent = "claude" if agent_type == "claude" else "codex"
        return ["acpx", agent, "--cwd", worktree, "sessions", "ensure", "--name", session_name]

    def _build_acpx_cancel_cmd(self, agent_type, session_name, worktree):
        agent = "claude" if agent_type == "claude" else "codex"
        return ["acpx", agent, "cancel", "-s", session_name, "--cwd", worktree]

    def _build_acpx_close_cmd(self, agent_type, session_name, worktree):
        agent = "claude" if agent_type == "claude" else "codex"
        return ["acpx", agent, "--cwd", worktree, "sessions", "close", session_name]

    def _build_acpx_status_cmd(self, agent_type, session_name, worktree):
        agent = "claude" if agent_type == "claude" else "codex"
        return ["acpx", agent, "status", "-s", session_name, "--cwd", worktree, "--format", "json"]

    # ------------------------------------------------------------------
    # Core session lifecycle
    # ------------------------------------------------------------------

    async def start_session(self, run_id, host, agent_type, task_file, worktree, timeout_sec, revision=None) -> str:
        """Create an acpx session and send the initial prompt. Returns job_id."""
        from src.git_utils import get_head_commit
        base_commit = await get_head_commit(worktree)

        run = await self.db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
        stage = run["current_stage"] if run else "UNKNOWN"
        phase = "design" if "DESIGN" in stage else "dev"
        session_name = self._make_session_name(run_id, phase, revision)

        job_id = await self.jobs.create_job(
            run_id, host["id"], agent_type, stage, task_file, worktree, base_commit, timeout_sec,
            session_name=session_name,
        )

        # Ensure session exists
        ensure_cmd = self._build_acpx_ensure_cmd(agent_type, session_name, worktree)
        if host["host"] == "local":
            await self._run_cmd(ensure_cmd, worktree)
        else:
            await self._run_ssh_cmd(host, ensure_cmd)

        # Send initial prompt
        prompt_cmd = self._build_acpx_prompt_cmd(agent_type, session_name, worktree, timeout_sec, task_file)

        await self.jobs.update_status(job_id, "running")
        await self.hosts.increment_load(host["id"])
        await self._emit_event(run_id, "session.created", {"session_name": session_name, "agent_type": agent_type})

        if host["host"] == "local":
            process = await self._start_local(prompt_cmd, worktree, job_id)
        else:
            process = await self._start_ssh(host, prompt_cmd, job_id)

        task = asyncio.create_task(self._watch(job_id, process, run_id, host["id"], session_name))
        self._tasks[job_id] = task

        return job_id

    async def send_followup(self, run_id, agent_type, prompt_file, worktree, timeout_sec) -> None:
        """Send a followup prompt to an existing session.

        Launches a background watcher task (non-blocking) that triggers
        state_machine.tick() on completion, just like start_session does.
        """
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job:
            raise RuntimeError(f"No job found for run {run_id}")

        session_name = job["session_name"]
        host_id = job["host_id"]
        host = await self.db.fetchone("SELECT * FROM agent_hosts WHERE id=?", (host_id,))

        prompt_cmd = self._build_acpx_prompt_cmd(agent_type, session_name, worktree, timeout_sec, prompt_file)

        if host and host["host"] == "local":
            process = await self._start_local(prompt_cmd, worktree, job["id"])
        else:
            process = await self._start_ssh(dict(host), prompt_cmd, job["id"])

        # Background watcher -- triggers tick on completion
        task = asyncio.create_task(self._watch(job["id"], process, run_id, host_id, session_name))
        self._tasks[job["id"]] = task

    async def cancel_session(self, run_id, agent_type) -> None:
        """Cooperatively cancel the current prompt on the session."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job or not job.get("session_name"):
            return

        cancel_cmd = self._build_acpx_cancel_cmd(agent_type, job["session_name"], job["worktree"])
        try:
            await self._run_cmd(cancel_cmd, job["worktree"])
        except Exception:
            pass

        # Also cancel the asyncio task
        task = self._tasks.get(job["id"])
        if task:
            task.cancel()

        now = datetime.now(timezone.utc).isoformat()
        await self.jobs.update_status(job["id"], "cancelled", ended_at=now)

    async def close_session(self, run_id, agent_type) -> None:
        """Close the session and release resources."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job or not job.get("session_name"):
            return

        close_cmd = self._build_acpx_close_cmd(agent_type, job["session_name"], job["worktree"])
        try:
            await self._run_cmd(close_cmd, job["worktree"])
        except Exception:
            pass

        await self._emit_event(run_id, "session.closed", {"session_name": job["session_name"]})

    async def get_session_status(self, run_id, agent_type, host=None) -> dict | None:
        """Query acpx session status."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job or not job.get("session_name"):
            return None

        status_cmd = self._build_acpx_status_cmd(agent_type, job["session_name"], job["worktree"])

        try:
            if host and host["host"] != "local":
                stdout, _, _ = await self._run_ssh_cmd(host, status_cmd)
            else:
                stdout, _, _ = await self._run_cmd(status_cmd, job["worktree"])
            return json.loads(stdout)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    async def recover(self, run_id, action):
        """Recover an interrupted job."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job:
            return

        agent_type = job["agent_type"]

        if action == "resume":
            # Send RESUME.md to the same session
            resume_prompt = os.path.join(self.coop_dir, "runs", run_id, "RESUME.md")
            os.makedirs(os.path.dirname(resume_prompt), exist_ok=True)
            await self.artifacts.render_task(
                "templates/RESUME.md",
                {"run_id": run_id, "ticket": "", "resume_count": (job.get("resume_count") or 0) + 1,
                 "interrupt_reason": "process interrupted", "commits_made": "", "diff_stat": "",
                 "agent_output_tail": "", "original_task_content": ""},
                resume_prompt,
            )
            resume_count = (job.get("resume_count") or 0) + 1
            await self.db.execute("UPDATE jobs SET resume_count=? WHERE id=?", (resume_count, job["id"]))
            await self.send_followup(run_id, agent_type, resume_prompt, job["worktree"], 1800)

        elif action == "redo":
            await self.close_session(run_id, agent_type)
            from src.git_utils import run_git
            if job["worktree"] and job.get("base_commit"):
                await run_git("reset", "--hard", job["base_commit"], cwd=job["worktree"])

        elif action == "manual":
            await self.close_session(run_id, agent_type)

    async def restore_on_startup(self):
        """On startup, check acpx session status for stale jobs."""
        jobs = await self.db.fetchall(
            "SELECT * FROM jobs WHERE status IN ('starting','running')"
        )
        now = datetime.now(timezone.utc).isoformat()
        for job in jobs:
            j = dict(job)
            if j.get("session_name"):
                # Look up host for SSH routing
                host = None
                if j.get("host_id"):
                    host = await self.db.fetchone(
                        "SELECT * FROM agent_hosts WHERE id=?", (j["host_id"],)
                    )
                    if host:
                        host = dict(host)
                status = await self.get_session_status(j["run_id"], j["agent_type"], host=host)
                if status and status.get("status") == "running":
                    continue  # Still running, leave it
            await self.jobs.update_status(j["id"], "interrupted", ended_at=now)

    # ------------------------------------------------------------------
    # Process management (private)
    # ------------------------------------------------------------------

    async def _run_cmd(self, cmd, cwd):
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return stdout.decode().strip(), stderr.decode().strip(), proc.returncode

    async def _run_ssh_cmd(self, host, cmd):
        import asyncssh
        import shlex
        remote_cmd = " ".join(shlex.quote(c) for c in cmd)
        connect_args = {"host": host["host"], "known_hosts": None}
        if host.get("ssh_key"):
            connect_args["client_keys"] = [host["ssh_key"]]
        async with asyncssh.connect(**connect_args) as conn:
            result = await conn.run(remote_cmd)
            return result.stdout.strip(), result.stderr.strip(), result.returncode

    async def _start_local(self, cmd, worktree, job_id):
        log_dir = Path(self.coop_dir) / "jobs" / job_id
        log_dir.mkdir(parents=True, exist_ok=True)

        process = await asyncio.create_subprocess_exec(
            *cmd, cwd=worktree,
            stdout=asyncio.subprocess.PIPE,
            stderr=open(log_dir / "stderr.log", "w", encoding="utf-8"),
        )
        return process

    async def _start_ssh(self, host, cmd, job_id):
        import asyncssh
        import shlex
        remote_cmd = " ".join(shlex.quote(c) for c in cmd)
        connect_args = {"host": host["host"], "known_hosts": None}
        if host.get("ssh_key"):
            connect_args["client_keys"] = [host["ssh_key"]]
        conn = await asyncssh.connect(**connect_args)
        process = await conn.create_process(remote_cmd)
        return process

    async def _watch(self, job_id, process, run_id, host_id, session_name):
        """Watch a prompt process and parse NDJSON output."""
        try:
            log_dir = Path(self.coop_dir) / "jobs" / job_id
            log_dir.mkdir(parents=True, exist_ok=True)
            events_file = log_dir / "events.jsonl"

            await self._parse_ndjson_stream(process, job_id, events_file)
            await process.wait()
            rc = process.returncode
            now = datetime.now(timezone.utc).isoformat()
            status = self._map_exit_code(rc)

            await self.jobs.update_status(job_id, status, ended_at=now)
            await self.db.execute(
                "UPDATE jobs SET events_file=? WHERE id=?",
                (str(events_file), job_id),
            )

            if status == "completed":
                await self._emit_event(run_id, "job.completed", {"job_id": job_id})
                if self._state_machine:
                    await self._state_machine.tick(run_id)
            elif status == "interrupted":
                await self._emit_event(run_id, "job.interrupted", {"job_id": job_id, "reason": "signal"})
            else:
                await self._emit_event(run_id, "job.failed", {"job_id": job_id, "exit_code": rc})

        except asyncio.CancelledError:
            now = datetime.now(timezone.utc).isoformat()
            await self.jobs.update_status(job_id, "cancelled", ended_at=now)
        except Exception as e:
            now = datetime.now(timezone.utc).isoformat()
            await self.jobs.update_status(job_id, "failed", ended_at=now)
            await self._emit_event(run_id, "job.error", {"job_id": job_id, "error": str(e)})
        finally:
            await self.hosts.decrement_load(host_id)
            self._tasks.pop(job_id, None)

    async def _parse_ndjson_stream(self, process, job_id, events_file):
        """Parse NDJSON lines from process stdout, append to events file."""
        with open(events_file, "a", encoding="utf-8") as f:
            async for line in process.stdout:
                line = line.decode().strip() if isinstance(line, bytes) else line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    f.write(json.dumps(msg) + "\n")
                    f.flush()
                except json.JSONDecodeError:
                    f.write(json.dumps({"raw": line}) + "\n")
                    f.flush()

    async def _emit_event(self, run_id, event_type, payload):
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (run_id, event_type, json.dumps(payload), now),
        )
        if self.webhooks:
            await self.webhooks.notify(event_type, {"run_id": run_id, **payload})
