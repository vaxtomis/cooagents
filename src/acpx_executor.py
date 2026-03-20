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
    def __init__(self, db, job_manager, host_manager, artifact_manager, webhook_notifier, config=None, coop_dir=".coop", project_root=None):
        self.db = db
        self.jobs = job_manager
        self.hosts = host_manager
        self.artifacts = artifact_manager
        self.webhooks = webhook_notifier
        self.config = config
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
        self.coop_dir = str(self._resolve_project_path(coop_dir))
        self._state_machine = None
        self._tasks = {}  # job_id -> asyncio.Task
        self._resources = {}  # job_id -> {"stderr_fh": fh, "ssh_conn": conn}

    def set_state_machine(self, sm):
        self._state_machine = sm

    async def _notify_job_status_changed(self, run_id, job_id, status):
        if not self._state_machine:
            return
        if hasattr(self._state_machine, "on_job_status_changed"):
            await self._state_machine.on_job_status_changed(run_id, job_id, status)
            return
        if hasattr(self._state_machine, "tick"):
            await self._state_machine.tick(run_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_session_name(self, run_id, phase, revision=None):
        name = f"{run_id}-{phase}"
        if revision and revision > 1:
            name += f"-r{revision}"
        return name

    def _map_exit_code(self, rc):
        return _EXIT_CODE_MAP.get(rc, "failed")

    def _resolve_agent(self, agent_type):
        return "claude" if agent_type == "claude" else "codex"

    def _acpx_cfg(self):
        """Return the AcpxConfig or None."""
        return getattr(self.config, "acpx", None) if self.config else None

    def _dispatch_ensure_timeout(self):
        timeout_cfg = getattr(self.config, "timeouts", None) if self.config else None
        if not timeout_cfg:
            return 60
        return getattr(timeout_cfg, "dispatch_ensure", 60)

    def _get_allowed_tools(self, agent_type):
        cfg = self._acpx_cfg()
        if not cfg:
            return None
        if agent_type == "claude":
            return getattr(cfg, "allowed_tools_design", None)
        return getattr(cfg, "allowed_tools_dev", None)

    def _normalize_task_file(self, task_file):
        if not task_file:
            return None
        return os.path.abspath(task_file)

    def _resolve_project_path(self, path):
        path = Path(path)
        if not path.is_absolute():
            path = self.project_root / path
        return path

    def _json_contains_stop_reason(self, value, stop_reason):
        if isinstance(value, dict):
            if value.get("stopReason") == stop_reason:
                return True
            return any(self._json_contains_stop_reason(child, stop_reason) for child in value.values())
        if isinstance(value, list):
            return any(self._json_contains_stop_reason(child, stop_reason) for child in value)
        return False

    def _events_file_has_stop_reason(self, events_file, stop_reason):
        events_path = self._resolve_project_path(events_file)
        if not events_path.exists():
            return False

        try:
            with events_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if self._json_contains_stop_reason(payload, stop_reason):
                        return True
        except OSError:
            return False

        return False

    def _job_events_file(self, job):
        if job.get("events_file"):
            return job["events_file"]
        return str(Path(self.coop_dir) / "jobs" / job["id"] / "events.jsonl")

    def _finalize_terminal_status(self, status, events_file):
        if status != "completed" and self._events_file_has_stop_reason(events_file, "end_turn"):
            return "completed"
        return status

    # ------------------------------------------------------------------
    # Command builders
    # ------------------------------------------------------------------

    def _build_acpx_prompt_cmd(self, agent_type, session_name, worktree, timeout_sec, task_file=None):
        agent = self._resolve_agent(agent_type)
        task_file = self._normalize_task_file(task_file)
        # Global options must appear before the agent subcommand
        cmd = [
            "acpx", "--cwd", worktree,
            "--format", "json",
            "--approve-all",
            "--timeout", str(timeout_sec),
        ]
        cfg = self._acpx_cfg()
        if cfg:
            cmd += ["--ttl", str(cfg.ttl)]
            if getattr(cfg, "json_strict", False):
                cmd.append("--json-strict")
            if getattr(cfg, "model", None):
                cmd += ["--model", cfg.model]
        allowed = self._get_allowed_tools(agent_type)
        if allowed:
            cmd += ["--allowed-tools", allowed]
        # Agent subcommand and its options
        cmd.append(agent)
        cmd += ["-s", session_name]
        if task_file:
            cmd += ["--file", task_file]
        return cmd

    def _build_acpx_exec_cmd(self, agent_type, worktree, timeout_sec, task_file=None, prompt=None):
        agent = self._resolve_agent(agent_type)
        task_file = self._normalize_task_file(task_file)
        # Global options must appear before the agent subcommand
        cmd = [
            "acpx", "--cwd", worktree,
            "--format", "json",
            "--approve-all",
            "--timeout", str(timeout_sec),
        ]
        cfg = self._acpx_cfg()
        if cfg:
            if getattr(cfg, "json_strict", False):
                cmd.append("--json-strict")
            if getattr(cfg, "model", None):
                cmd += ["--model", cfg.model]
        # Agent and subcommand
        cmd += [agent, "exec"]
        if task_file:
            cmd += ["--file", task_file]
        elif prompt:
            cmd.append(prompt)
        return cmd

    def _build_acpx_ensure_cmd(self, agent_type, session_name, worktree):
        agent = self._resolve_agent(agent_type)
        return ["acpx", "--cwd", worktree, agent, "sessions", "ensure", "--name", session_name]

    def _build_acpx_cancel_cmd(self, agent_type, session_name, worktree):
        agent = self._resolve_agent(agent_type)
        return ["acpx", "--cwd", worktree, agent, "cancel", "-s", session_name]

    def _build_acpx_close_cmd(self, agent_type, session_name, worktree):
        agent = self._resolve_agent(agent_type)
        return ["acpx", "--cwd", worktree, agent, "sessions", "close", session_name]

    def _build_acpx_status_cmd(self, agent_type, session_name, worktree):
        agent = self._resolve_agent(agent_type)
        return ["acpx", "--cwd", worktree, "--format", "json", agent, "status", "-s", session_name]

    def _build_acpx_show_cmd(self, agent_type, session_name, worktree):
        """Build command for ``acpx <agent> sessions show`` (rich metadata)."""
        agent = self._resolve_agent(agent_type)
        return ["acpx", "--cwd", worktree, "--format", "json", agent, "sessions", "show", session_name]

    def _build_acpx_history_cmd(self, agent_type, session_name, worktree, limit=20):
        """Build command for ``acpx <agent> sessions history``."""
        agent = self._resolve_agent(agent_type)
        return ["acpx", "--cwd", worktree, "--format", "json",
                agent, "sessions", "history", session_name, "--limit", str(limit)]

    def _build_acpx_set_mode_cmd(self, agent_type, session_name, worktree, mode):
        agent = self._resolve_agent(agent_type)
        return ["acpx", "--cwd", worktree, agent, "set-mode", mode, "-s", session_name]

    def _build_acpx_set_cmd(self, agent_type, session_name, worktree, key, value):
        agent = self._resolve_agent(agent_type)
        return ["acpx", "--cwd", worktree, agent, "set", key, value, "-s", session_name]

    # ------------------------------------------------------------------
    # Command routing (local vs SSH)
    # ------------------------------------------------------------------

    async def _route_cmd(self, host_id, cmd, worktree="."):
        """Route command execution to local or SSH based on host."""
        if host_id:
            host = await self.db.fetchone("SELECT * FROM agent_hosts WHERE id=?", (host_id,))
            if host and host["host"] != "local":
                return await self._run_ssh_cmd(dict(host), cmd)
        return await self._run_cmd(cmd, worktree)

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

    # ------------------------------------------------------------------
    # Core session lifecycle
    # ------------------------------------------------------------------

    async def start_session(self, run_id, host, agent_type, task_file, worktree, timeout_sec, revision=None) -> str:
        """Create an acpx session and send the initial prompt. Returns job_id."""
        from src.git_utils import get_head_commit
        base_commit = await get_head_commit(worktree)

        run = await self.db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
        stage = run["current_stage"] if run else "UNKNOWN"
        stage = {
            "DESIGN_QUEUED": "DESIGN_DISPATCHED",
            "DEV_QUEUED": "DEV_DISPATCHED",
        }.get(stage, stage)
        phase = "design" if "DESIGN" in stage else "dev"
        session_name = self._make_session_name(run_id, phase, revision)

        job_id = await self.jobs.create_job(
            run_id, host["id"], agent_type, stage, task_file, worktree, base_commit, timeout_sec,
            session_name=session_name,
        )

        # Ensure session exists
        ensure_cmd = self._build_acpx_ensure_cmd(agent_type, session_name, worktree)
        try:
            ensure_timeout = self._dispatch_ensure_timeout()
            if host["host"] == "local":
                _, _, rc = await asyncio.wait_for(
                    self._run_cmd(ensure_cmd, worktree),
                    timeout=ensure_timeout,
                )
            else:
                _, _, rc = await asyncio.wait_for(
                    self._run_ssh_cmd(host, ensure_cmd),
                    timeout=ensure_timeout,
                )
        except asyncio.TimeoutError:
            now = datetime.now(timezone.utc).isoformat()
            await self.jobs.update_status(job_id, "timeout", ended_at=now)
            raise

        if rc != 0:
            now = datetime.now(timezone.utc).isoformat()
            await self.jobs.update_status(job_id, "failed", ended_at=now)
            raise RuntimeError(f"acpx ensure failed for {session_name}")

        # Send initial prompt
        prompt_cmd = self._build_acpx_prompt_cmd(agent_type, session_name, worktree, timeout_sec, task_file)

        await self.jobs.mark_running(job_id)
        await self._notify_job_status_changed(run_id, job_id, "running")
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
        """Send a followup prompt to an existing session (non-blocking)."""
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

        task = asyncio.create_task(self._watch(job["id"], process, run_id, host_id, session_name))
        self._tasks[job["id"]] = task

    async def cancel_session(self, run_id, agent_type, final_status="cancelled") -> None:
        """Cooperatively cancel the current prompt on the session."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job or not job.get("session_name"):
            return

        cancel_cmd = self._build_acpx_cancel_cmd(agent_type, job["session_name"], job["worktree"])
        try:
            await self._route_cmd(job["host_id"], cancel_cmd, job["worktree"])
        except Exception:
            pass

        task = self._tasks.get(job["id"])
        if task:
            task.cancel()

        now = datetime.now(timezone.utc).isoformat()
        await self.jobs.update_status(job["id"], final_status, ended_at=now)

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
            await self._route_cmd(job["host_id"], close_cmd, job["worktree"])
        except Exception:
            pass

        await self._emit_event(run_id, "session.closed", {"session_name": job["session_name"]})

    async def get_session_status(self, run_id, agent_type, host=None) -> dict | None:
        """Query acpx local process status (running/dead/no-session)."""
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
                stdout, _, _ = await self._route_cmd(job["host_id"], status_cmd, job["worktree"])
            return json.loads(stdout)
        except Exception:
            return None

    async def get_session_detail(self, run_id, agent_type) -> dict | None:
        """Query rich session metadata via ``sessions show`` (token usage, exit code, etc.)."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job or not job.get("session_name"):
            return None

        show_cmd = self._build_acpx_show_cmd(agent_type, job["session_name"], job["worktree"])
        try:
            stdout, _, _ = await self._route_cmd(job["host_id"], show_cmd, job["worktree"])
            return json.loads(stdout)
        except Exception:
            return None

    async def get_session_history(self, run_id, agent_type, limit=20) -> list | None:
        """Retrieve conversation turn history via ``sessions history``."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job or not job.get("session_name"):
            return None

        history_cmd = self._build_acpx_history_cmd(
            agent_type, job["session_name"], job["worktree"], limit,
        )
        try:
            stdout, _, _ = await self._route_cmd(job["host_id"], history_cmd, job["worktree"])
            return json.loads(stdout)
        except Exception:
            return None

    async def set_mode(self, run_id, agent_type, mode) -> bool:
        """Set session mode at runtime (e.g. plan/act)."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job or not job.get("session_name"):
            return False

        cmd = self._build_acpx_set_mode_cmd(
            agent_type, job["session_name"], job["worktree"], mode,
        )
        try:
            _, _, rc = await self._route_cmd(job["host_id"], cmd, job["worktree"])
            return rc == 0
        except Exception:
            return False

    async def set_config_option(self, run_id, agent_type, key, value) -> bool:
        """Set a session config option at runtime (e.g. reasoning_effort)."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run_id,),
        )
        if not job or not job.get("session_name"):
            return False

        cmd = self._build_acpx_set_cmd(
            agent_type, job["session_name"], job["worktree"], key, value,
        )
        try:
            _, _, rc = await self._route_cmd(job["host_id"], cmd, job["worktree"])
            return rc == 0
        except Exception:
            return False

    async def run_once(self, agent_type, worktree, timeout_sec, task_file=None, prompt=None) -> tuple[str, int]:
        """One-shot exec mode — no persistent session. Returns (stdout, exit_code)."""
        cmd = self._build_acpx_exec_cmd(agent_type, worktree, timeout_sec, task_file, prompt)
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=worktree,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip(), proc.returncode

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
            run = await self.db.fetchone("SELECT id FROM runs WHERE id=?", (j["run_id"],))
            if not run:
                await self.jobs.update_status(j["id"], "interrupted", ended_at=now)
                continue
            if j.get("session_name"):
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
            restored_status = self._finalize_terminal_status("interrupted", self._job_events_file(j))
            await self.jobs.update_status(j["id"], restored_status, ended_at=now)
            await self._notify_job_status_changed(j["run_id"], j["id"], restored_status)

    # ------------------------------------------------------------------
    # Process management (private)
    # ------------------------------------------------------------------

    async def _start_local(self, cmd, worktree, job_id):
        log_dir = Path(self.coop_dir) / "jobs" / job_id
        log_dir.mkdir(parents=True, exist_ok=True)

        stderr_fh = open(log_dir / "stderr.log", "w", encoding="utf-8")
        process = await asyncio.create_subprocess_exec(
            *cmd, cwd=worktree,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr_fh,
        )
        self._resources[job_id] = {"stderr_fh": stderr_fh}
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
        self._resources[job_id] = {"ssh_conn": conn}
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
            status = self._finalize_terminal_status(self._map_exit_code(rc), events_file)

            await self.jobs.update_status(job_id, status, ended_at=now)
            await self.db.execute(
                "UPDATE jobs SET events_file=? WHERE id=?",
                (str(events_file), job_id),
            )

            if status == "completed":
                await self._emit_event(run_id, "job.completed", {"job_id": job_id})
            elif status == "interrupted":
                await self._emit_event(run_id, "job.interrupted", {"job_id": job_id, "reason": "signal"})
            else:
                await self._emit_event(run_id, "job.failed", {"job_id": job_id, "exit_code": rc})

            await self._notify_job_status_changed(run_id, job_id, status)

        except asyncio.CancelledError:
            now = datetime.now(timezone.utc).isoformat()
            job = await self.db.fetchone("SELECT status, ended_at FROM jobs WHERE id=?", (job_id,))
            current_status = job["status"] if job else None
            if current_status in {"timeout", "cancelled", "failed", "completed", "interrupted"}:
                if job and not job.get("ended_at"):
                    await self.jobs.update_status(job_id, current_status, ended_at=now)
            else:
                await self.jobs.update_status(job_id, "cancelled", ended_at=now)
        except Exception as e:
            now = datetime.now(timezone.utc).isoformat()
            await self.jobs.update_status(job_id, "failed", ended_at=now)
            await self._emit_event(run_id, "job.error", {"job_id": job_id, "error": str(e)})
        finally:
            await self.hosts.decrement_load(host_id)
            self._tasks.pop(job_id, None)
            self._cleanup_resources(job_id)

    def _cleanup_resources(self, job_id):
        """Close file handles and SSH connections for a finished job."""
        res = self._resources.pop(job_id, {})
        fh = res.get("stderr_fh")
        if fh:
            try:
                fh.close()
            except Exception:
                pass
        conn = res.get("ssh_conn")
        if conn:
            try:
                conn.close()
            except Exception:
                pass

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
