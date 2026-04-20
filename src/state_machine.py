"""
Core workflow state machine for cooagents.

Manages the 15-stage workflow lifecycle:
INIT → REQ_COLLECTING → REQ_REVIEW → DESIGN_QUEUED → DESIGN_DISPATCHED
→ DESIGN_RUNNING → DESIGN_REVIEW → DEV_QUEUED → DEV_DISPATCHED
→ DEV_RUNNING → DEV_REVIEW → MERGE_QUEUED → MERGING → MERGED/MERGE_CONFLICT → FAILED
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.event_limits import can_emit_event
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.trace_context import bind_run

GATE_STAGES = {"req": "REQ_REVIEW", "design": "DESIGN_REVIEW", "dev": "DEV_REVIEW"}
REJECT_TARGETS = {"req": "REQ_COLLECTING", "design": "DESIGN_QUEUED", "dev": "DEV_QUEUED"}


class StateMachine:
    """Orchestrates state transitions for a cooagents workflow run.

    Parameters
    ----------
    db:
        Async database wrapper (``src.database.Database``).
    artifact_manager:
        ``src.artifact_manager.ArtifactManager`` instance.
    host_manager:
        Object with ``select_host(agent_type, preferred_host=None)`` coroutine.
    agent_executor:
        Object with ``dispatch(run_id, host, agent_type, task_file, worktree,
        timeout_sec)`` coroutine.
    webhook_notifier:
        Object with ``notify(event_type, payload)`` coroutine.
    merge_manager:
        Optional merge manager with ``enqueue`` / ``get_status`` coroutines.
    coop_dir:
        Directory where run state snapshots and task files are stored.
    ensure_worktree_fn:
        Optional override for ``src.git_utils.ensure_worktree``. Useful for
        testing without a real git repository.  When *None* (default), the
        real implementation is imported lazily on first use.
    """

    def __init__(
        self,
        db,
        artifact_manager,
        host_manager,
        agent_executor,
        webhook_notifier,
        merge_manager=None,
        coop_dir: str = ".coop",
        ensure_worktree_fn=None,
        config=None,
        job_manager=None,
        project_root=None,
        trace_emitter=None,
    ):
        self.db = db
        self.artifacts = artifact_manager
        self.hosts = host_manager
        self.executor = agent_executor
        self.webhooks = webhook_notifier
        self.merge = merge_manager
        self.coop_dir = coop_dir
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
        if not Path(self.coop_dir).is_absolute():
            self.coop_dir = str(self.project_root / self.coop_dir)
        self._ensure_worktree = ensure_worktree_fn
        self._config = config
        self.jobs = job_manager
        self._design_max_turns = 1
        self._dev_max_turns = 1
        if config:
            self._design_max_turns = getattr(getattr(config, 'turns', None), 'design_max_turns', 1)
            self._dev_max_turns = getattr(getattr(config, 'turns', None), 'dev_max_turns', 1)
        self._trace = trace_emitter
        self._dispatch_locks = {}
        self._progression_locks = {}

    def _execution_timeout(self, phase: str) -> int:
        defaults = {"design": 1800, "dev": 3600}
        timeout_cfg = getattr(self._config, "timeouts", None) if self._config else None
        if not timeout_cfg:
            return defaults[phase]
        if phase == "design":
            return getattr(timeout_cfg, "design_execution", defaults[phase])
        return getattr(timeout_cfg, "dev_execution", defaults[phase])

    def _dispatch_reconcile_grace(self) -> int:
        timeout_cfg = getattr(self._config, "timeouts", None) if self._config else None
        if not timeout_cfg:
            return 30
        return getattr(timeout_cfg, "dispatch_reconcile_grace", 30)

    def _session_reconcile_attempts(self) -> int:
        timeout_cfg = getattr(self._config, "timeouts", None) if self._config else None
        if not timeout_cfg:
            return 3
        return max(1, getattr(timeout_cfg, "session_reconcile_attempts", 3))

    def _session_reconcile_delay(self) -> float:
        timeout_cfg = getattr(self._config, "timeouts", None) if self._config else None
        if not timeout_cfg:
            return 0.5
        return max(0.0, float(getattr(timeout_cfg, "session_reconcile_delay", 0.5)))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_run(
        self,
        ticket: str,
        repo_path: str,
        description: str | None = None,
        preferences: dict | None = None,
        notify_channel: str | None = None,
        notify_to: str | None = None,
        repo_url: str | None = None,
        design_agent: str | None = None,
        dev_agent: str | None = None,
    ) -> dict:
        """Create a new workflow run and advance it to REQ_COLLECTING.

        Returns the run dict (possibly with a ``warning`` key if a duplicate
        active run already exists for the same ticket).
        """
        # Validate repo_path is an existing git repo
        repo_p = Path(repo_path)
        if not repo_p.exists() or not (repo_p / ".git").is_dir():
            raise BadRequestError(
                f"repo_path does not exist or is not a git repository: {repo_path}. "
                "Call POST /repos/ensure first."
            )

        # Resolve agent preferences
        if design_agent is None:
            design_agent = getattr(self._config, "preferred_design_agent", "claude") if self._config else "claude"
        if dev_agent is None:
            dev_agent = getattr(self._config, "preferred_dev_agent", "claude") if self._config else "claude"

        run_id = f"run-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        prefs = json.dumps(preferences) if preferences else None

        # Warn (but don't block) on duplicate active ticket
        existing = await self.db.fetchone(
            "SELECT id FROM runs WHERE ticket=? AND status='running'", (ticket,)
        )
        warning = f"Active run already exists for ticket {ticket}" if existing else None

        await self.db.execute(
            "INSERT INTO runs(id,ticket,repo_path,repo_url,status,current_stage,"
            "description,preferences_json,notify_channel,notify_to,"
            "design_agent,dev_agent,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, ticket, repo_path, repo_url, "running", "INIT", description, prefs,
             notify_channel, notify_to, design_agent, dev_agent, now, now),
        )
        await self._update_stage(run_id, "INIT", "REQ_COLLECTING")
        run = await self._get_run(run_id)
        if warning:
            run["warning"] = warning
        return run

    async def create_run_with_requirement(
        self,
        ticket: str,
        repo_path: str,
        req_content: str,
        original_filename: str,
        description: str | None = None,
        preferences: dict | None = None,
        notify_channel: str | None = None,
        notify_to: str | None = None,
        repo_url: str | None = None,
        design_agent: str | None = None,
        dev_agent: str | None = None,
    ) -> dict:
        """Create a run with an already-written requirement, skipping REQ stages.

        Writes the requirement to disk, creates the run, registers the artifact,
        records an auto-approval for the req gate, and advances directly to
        DESIGN_QUEUED.
        """
        # Write requirement file
        req_dir = Path(repo_path) / "docs" / "req"
        req_dir.mkdir(parents=True, exist_ok=True)
        req_path = req_dir / f"REQ-{ticket}.md"
        req_path.write_text(req_content, encoding="utf-8")

        # Create the run (starts at REQ_COLLECTING)
        run = await self.create_run(
            ticket, repo_path, description, preferences,
            notify_channel=notify_channel, notify_to=notify_to,
            repo_url=repo_url, design_agent=design_agent, dev_agent=dev_agent,
        )
        run_id = run["id"]

        # Register artifact
        await self.artifacts.register(run_id, "req", str(req_path), "REQ_COLLECTING")

        # Auto-approve req gate
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT INTO approvals(run_id,gate,decision,by,comment,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (run_id, "req", "approved", "upload", f"Uploaded: {original_filename}", now),
        )
        await self._emit(run_id, "gate.approved", {"gate": "req", "by": "upload"})

        # Skip REQ_COLLECTING → REQ_REVIEW → DESIGN_QUEUED
        await self._update_stage(run_id, "REQ_COLLECTING", "DESIGN_QUEUED")
        await self._emit(run_id, "requirement.uploaded", {
            "path": str(req_path),
            "original_filename": original_filename,
        })

        return await self._get_run(run_id)

    async def tick(self, run_id: str) -> dict:
        """Advance the run one step if there is an automatic transition available.

        Idempotent: review/waiting stages are no-ops.
        """
        lock = self._progression_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            return await self._tick_unlocked(run_id)

    async def _tick_unlocked(self, run_id: str) -> dict:
        """Inner tick logic, must be called while holding ``_progression_locks[run_id]``."""
        run = await self._get_run(run_id)
        if run["status"] != "running":
            return run

        handler = {
            "REQ_COLLECTING": self._tick_req_collecting,
            "REQ_REVIEW": self._tick_review,        # no-op, waits for approve/reject
            "DESIGN_QUEUED": self._tick_design_queued,
            "DESIGN_DISPATCHED": self._tick_design_dispatched,
            "DESIGN_RUNNING": self._tick_design_running,
            "DESIGN_REVIEW": self._tick_review,
            "DEV_QUEUED": self._tick_dev_queued,
            "DEV_DISPATCHED": self._tick_dev_dispatched,
            "DEV_RUNNING": self._tick_dev_running,
            "DEV_REVIEW": self._tick_review,
            "MERGE_QUEUED": self._tick_merge_queued,
            "MERGING": self._tick_merging,
            "MERGE_CONFLICT": self._tick_review,    # waits for manual intervention
        }.get(run["current_stage"])

        if handler:
            await handler(run)
        return await self._get_run(run_id)

    async def on_job_status_changed(self, run_id: str, job_id: str, status: str) -> dict:
        """Consume a job lifecycle update and advance run stages when applicable."""
        lock = self._progression_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            run = await self._get_run(run_id)
            if run["status"] != "running":
                return run

            job = await self.db.fetchone("SELECT * FROM jobs WHERE id=? AND run_id=?", (job_id, run_id))
            if not job:
                return run
            job = dict(job)

            latest_job = await self.db.fetchone(
                "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC, id DESC LIMIT 1",
                (run_id,),
            )
            if latest_job and latest_job["id"] != job_id:
                return run

            current_stage = run["current_stage"]
            job_stage = job.get("stage", "")

            if status == "running":
                if not self._stage_matches_running_event(current_stage, job_stage):
                    return run
                await self._advance_dispatched_run_on_running_event(run, job)
            elif status == "completed":
                if not self._stage_matches_completed_event(current_stage, job_stage):
                    return run
                await self._tick_unlocked(run_id)
            elif status in {"failed", "timeout", "interrupted"}:
                run = await self._advance_queued_run_to_dispatched_job_stage(run, job_stage)
                current_stage = run["current_stage"]
                if not self._stage_matches_terminal_failure_event(current_stage, job_stage):
                    return run
                await self._tick_unlocked(run_id)
            return await self._get_run(run_id)

    async def approve(
        self,
        run_id: str,
        gate: str,
        by: str,
        comment: str | None = None,
    ) -> dict:
        """Approve a gate and advance to the next stage.

        Raises
        ------
        ConflictError
            If the run is not in the expected stage for the given gate.
        """
        run = await self._get_run(run_id)
        if run["status"] != "running":
            raise ConflictError(
                f"Cannot approve {gate}: run status is {run['status']}",
                run["current_stage"],
            )
        expected = GATE_STAGES.get(gate)
        if run["current_stage"] != expected:
            raise ConflictError(
                f"Cannot approve {gate}: run is in {run['current_stage']}, "
                f"expected {expected}",
                run["current_stage"],
            )

        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT INTO approvals(run_id,gate,decision,by,comment,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (run_id, gate, "approved", by, comment, now),
        )
        await self._emit(run_id, "gate.approved", {"gate": gate, "by": by})
        await self._trace_event("gate.approved", {"gate": gate, "by": by})

        next_stages = {"req": "DESIGN_QUEUED", "design": "DEV_QUEUED", "dev": "MERGE_QUEUED"}
        await self._update_stage(run_id, run["current_stage"], next_stages[gate])
        return await self._get_run(run_id)

    async def reject(
        self,
        run_id: str,
        gate: str,
        by: str,
        reason: str,
    ) -> dict:
        """Reject a gate and revert to the previous collection/queued stage.

        Raises
        ------
        ConflictError
            If the run is not in the expected stage for the given gate.
        """
        run = await self._get_run(run_id)
        if run["status"] != "running":
            raise ConflictError(
                f"Cannot reject {gate}: run status is {run['status']}",
                run["current_stage"],
            )
        expected = GATE_STAGES.get(gate)
        if run["current_stage"] != expected:
            raise ConflictError(
                f"Cannot reject {gate}: wrong stage",
                run["current_stage"],
            )

        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT INTO approvals(run_id,gate,decision,by,comment,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (run_id, gate, "rejected", by, reason, now),
        )
        await self._emit(run_id, "gate.rejected", {"gate": gate, "by": by, "reason": reason})
        await self._trace_event("gate.rejected", {"gate": gate, "by": by, "reason": reason})
        target = REJECT_TARGETS[gate]
        await self._update_stage(run_id, run["current_stage"], target)
        return await self._get_run(run_id)

    async def resolve_conflict(self, run_id: str, by: str) -> dict:
        """Re-queue a merge after the user has resolved conflicts externally.

        Raises
        ------
        ConflictError
            If the run is not in MERGE_CONFLICT.
        """
        run = await self._get_run(run_id)
        if run["current_stage"] != "MERGE_CONFLICT":
            raise ConflictError(
                "Can only resolve conflict in MERGE_CONFLICT stage",
                run["current_stage"],
            )

        # Remove the old merge queue entry so enqueue can create a fresh one
        if self.merge:
            await self.merge.remove(run_id)
        await self._emit(run_id, "merge.conflict_resolved", {"by": by})
        await self._update_stage(run_id, "MERGE_CONFLICT", "MERGE_QUEUED")
        return await self._get_run(run_id)

    # Stages that retry should rewind to a re-queueable predecessor. Anything
    # not listed stays at ``failed_at_stage`` so the scheduler's normal
    # handlers can decide what to do next tick.
    _RETRY_RESTORE_STAGES = {
        "INIT": "INIT",
        "REQ_COLLECTING": "REQ_COLLECTING",
        "REQ_REVIEW": "REQ_REVIEW",
        "DESIGN_QUEUED": "DESIGN_QUEUED",
        "DESIGN_DISPATCHED": "DESIGN_QUEUED",
        "DESIGN_RUNNING": "DESIGN_QUEUED",
        "DESIGN_REVIEW": "DESIGN_REVIEW",
        "DEV_QUEUED": "DEV_QUEUED",
        "DEV_DISPATCHED": "DEV_QUEUED",
        "DEV_RUNNING": "DEV_QUEUED",
        "DEV_REVIEW": "DEV_REVIEW",
        "MERGE_QUEUED": "MERGE_QUEUED",
        "MERGING": "MERGE_QUEUED",
        "MERGE_CONFLICT": "MERGE_QUEUED",
    }

    async def retry(self, run_id: str, by: str, note: str | None = None) -> dict:
        """Retry a failed run, restoring it to the stage where it failed.

        Cancels any still-active jobs (``starting`` / ``running``) for this run
        so a subsequent dispatch can claim the queued stage instead of bailing
        out on ``get_active_job``.

        Raises
        ------
        ConflictError
            If the run is not in ``failed`` status.
        """
        run = await self._get_run(run_id)
        if run["status"] != "failed":
            raise ConflictError("Can only retry failed runs", run["current_stage"])

        failed_stage = run.get("failed_at_stage") or "INIT"
        restore_stage = self._RETRY_RESTORE_STAGES.get(failed_stage, failed_stage)
        now = datetime.now(timezone.utc).isoformat()

        # CAS-guard on status='failed' so two concurrent retries cannot both
        # emit run.retried or both race through the restore transition.
        rowcount = await self.db.execute_rowcount(
            "UPDATE runs SET status='running', current_stage=?, updated_at=? "
            "WHERE id=? AND status='failed'",
            (restore_stage, now, run_id),
        )
        if rowcount != 1:
            return await self._get_run(run_id)

        # Retire any stale active jobs — otherwise the next dispatch tick sees
        # them via ``get_active_job`` and refuses to start a fresh job.
        await self.db.execute(
            "UPDATE jobs SET status='cancelled', ended_at=? "
            "WHERE run_id=? AND status IN ('starting','running')",
            (now, run_id),
        )

        await self._emit(run_id, "run.retried", {"by": by, "note": note, "restored_to": restore_stage})
        return await self._get_run(run_id)

    async def cancel(self, run_id: str, cleanup: bool = False) -> dict:
        """Cancel a run (any status → cancelled)."""
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE runs SET status='cancelled', updated_at=? WHERE id=?",
            (now, run_id),
        )
        await self._emit(run_id, "run.cancelled", {})
        self._release_run_locks(run_id)
        return await self._get_run(run_id)

    def _release_run_locks(self, run_id: str) -> None:
        """Drop per-run asyncio locks once a run has reached a terminal state.

        Why: these dicts are keyed by run_id and grow unbounded in a long-lived
        process. Dropping them on terminal transitions is safe because
        ``_tick_unlocked`` short-circuits on non-``running`` runs, so any
        future lock acquisition on the same run_id is a no-op. If ``retry``
        revives the run, ``setdefault`` will lazily recreate the locks.
        """
        self._dispatch_locks.pop(run_id, None)
        self._progression_locks.pop(run_id, None)

    async def submit_requirement(self, run_id: str, content: str) -> dict:
        """Write requirement content to disk, register as artifact, and advance stage.

        Raises
        ------
        ConflictError
            If the run is not in REQ_COLLECTING.
        """
        run = await self._get_run(run_id)
        if run["current_stage"] != "REQ_COLLECTING":
            raise ConflictError(
                "Can only submit requirement in REQ_COLLECTING",
                run["current_stage"],
            )

        # Write requirement file into the repo
        req_dir = Path(run["repo_path"]) / "docs" / "req"
        req_dir.mkdir(parents=True, exist_ok=True)
        req_path = req_dir / f"REQ-{run['ticket']}.md"
        req_path.write_text(content, encoding="utf-8")

        # Register artifact and advance stage
        await self.artifacts.register(run_id, "req", str(req_path), "REQ_COLLECTING")
        await self._update_stage(run_id, "REQ_COLLECTING", "REQ_REVIEW")
        await self._emit(run_id, "requirement.submitted", {"path": str(req_path)})
        return await self._get_run(run_id)

    # ------------------------------------------------------------------
    # Tick handlers (private)
    # ------------------------------------------------------------------

    async def _tick_req_collecting(self, run: dict) -> None:
        """No-op: waits for :meth:`submit_requirement`."""

    async def _tick_review(self, run: dict) -> None:
        """No-op: waits for :meth:`approve` or :meth:`reject`."""

    async def _advance_dispatched_run_on_running_event(self, run: dict, job: dict) -> None:
        dispatched_to_running = {
            "DESIGN_DISPATCHED": "DESIGN_RUNNING",
            "DEV_DISPATCHED": "DEV_RUNNING",
        }

        job_stage = job.get("stage")
        run = await self._advance_queued_run_to_dispatched_job_stage(run, job_stage)
        current_stage = run["current_stage"]

        target_stage = dispatched_to_running.get(current_stage)
        if target_stage and job_stage == current_stage:
            await self._update_stage(run["id"], current_stage, target_stage)

    async def _advance_queued_run_to_dispatched_job_stage(self, run: dict, job_stage: str | None) -> dict:
        queued_to_dispatched = {
            "DESIGN_QUEUED": "DESIGN_DISPATCHED",
            "DEV_QUEUED": "DEV_DISPATCHED",
        }
        current_stage = run["current_stage"]
        expected_dispatched = queued_to_dispatched.get(current_stage)
        if expected_dispatched and expected_dispatched == job_stage:
            await self._update_stage(run["id"], current_stage, expected_dispatched)
            return await self._get_run(run["id"])
        return run

    def _stage_matches_running_event(self, current_stage: str, job_stage: str) -> bool:
        allowed = {
            "DESIGN_QUEUED": "DESIGN_DISPATCHED",
            "DESIGN_DISPATCHED": "DESIGN_DISPATCHED",
            "DEV_QUEUED": "DEV_DISPATCHED",
            "DEV_DISPATCHED": "DEV_DISPATCHED",
        }
        return allowed.get(current_stage) == job_stage

    def _stage_matches_completed_event(self, current_stage: str, job_stage: str) -> bool:
        return current_stage == job_stage and current_stage in {"DESIGN_RUNNING", "DEV_RUNNING"}

    def _stage_matches_terminal_failure_event(self, current_stage: str, job_stage: str) -> bool:
        allowed = {
            "DESIGN_DISPATCHED": {"DESIGN_DISPATCHED", "DESIGN_RUNNING"},
            "DESIGN_RUNNING": {"DESIGN_DISPATCHED", "DESIGN_RUNNING"},
            "DEV_DISPATCHED": {"DEV_DISPATCHED", "DEV_RUNNING"},
            "DEV_RUNNING": {"DEV_DISPATCHED", "DEV_RUNNING"},
        }
        return job_stage in allowed.get(current_stage, set())

    async def _tick_design_queued(self, run: dict) -> None:
        """Try to dispatch the design agent job if a host is available."""
        lock = self._dispatch_locks.setdefault(run["id"], asyncio.Lock())
        async with lock:
            current = await self._get_run(run["id"])
            if current["status"] != "running" or current["current_stage"] != "DESIGN_QUEUED":
                return
            if self.jobs and await self.jobs.get_active_job(run["id"]):
                return

            preferred = run.get("design_agent") or "claude"
            fallback = "codex" if preferred == "claude" else "claude"

            host = await self.hosts.select_host(preferred)
            actual_agent = preferred
            if not host:
                host = await self.hosts.select_host(fallback)
                actual_agent = fallback
            if not host:
                await self._emit_limited(run["id"], "host.unavailable", {
                    "stage": "DESIGN_QUEUED",
                    "agent_type": preferred,
                    "ticket": run["ticket"],
                }, limit_keys=("stage",))
                return
            if actual_agent != preferred:
                await self._emit(run["id"], "agent.fallback", {
                    "stage": "DESIGN_QUEUED",
                    "preferred": preferred,
                    "actual": actual_agent,
                    "ticket": run["ticket"],
                })

            branch, wt = await self._resolve_worktree(run["repo_path"], run["ticket"], "design")

            now = datetime.now(timezone.utc).isoformat()
            await self.db.execute(
                "UPDATE runs SET design_worktree=?, design_branch=?, updated_at=? WHERE id=?",
                (wt, branch, now, run["id"]),
            )

            task_path = os.path.join(self.coop_dir, "runs", run["id"], "TASK-design.md")
            os.makedirs(os.path.dirname(task_path), exist_ok=True)
            req_source = Path(run["repo_path"]) / "docs" / "req" / f"REQ-{run['ticket']}.md"
            req_path = self._copy_file_to_worktree(
                req_source,
                wt,
                Path("docs") / "req" / req_source.name,
            )

            template = "templates/INIT-design.md"

            await self.artifacts.render_task(
                template,
                {
                    "run_id": run["id"],
                    "ticket": run["ticket"],
                    "repo_path": run["repo_path"],
                    "worktree": wt,
                    "req_path": req_path,
                },
                task_path,
            )

            timeout_sec = self._execution_timeout("design")
            try:
                if hasattr(self.executor, 'start_session'):
                    await self.executor.start_session(run["id"], host, actual_agent, task_path, wt, timeout_sec)
                else:
                    await self.executor.dispatch(run["id"], host, actual_agent, task_path, wt, timeout_sec)
            except asyncio.TimeoutError:
                job = await self.db.fetchone(
                    "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
                    (run["id"],),
                )
                if job:
                    await self._emit_limited(
                        run["id"],
                        "job.timeout",
                        {"run_id": run["id"], "job_id": job["id"], "stage": job.get("stage", "")},
                        limit_keys=("job_id",),
                    )
                return
            current = await self._get_run(run["id"])
            if current["current_stage"] == "DESIGN_QUEUED":
                await self._update_stage(run["id"], "DESIGN_QUEUED", "DESIGN_DISPATCHED")

    async def _tick_design_dispatched(self, run: dict) -> None:
        """Advance to DESIGN_RUNNING once the agent job reports as running."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run["id"],),
        )
        if not job:
            return
        job = await self._reconcile_job_session(run, dict(job))
        if job.get("_reconcile_pending"):
            return
        if job["status"] in ("failed", "timeout", "interrupted"):
            await self._transition_to_failed(run, job)
            return
        if job["status"] == "running":
            await self._update_stage(run["id"], "DESIGN_DISPATCHED", "DESIGN_RUNNING")

    async def _tick_design_running(self, run: dict) -> None:
        """Check design job result, evaluate, and either advance or request revision."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run["id"],),
        )
        if not job:
            return
        job = await self._reconcile_job_session(run, dict(job))
        if job["status"] in ("failed", "timeout", "interrupted"):
            await self._transition_to_failed(run, job)
            return
        if job["status"] != "completed":
            return

        turn = job.get("turn_count") or 1
        wt = run.get("design_worktree", "")
        job_agent = job.get("agent_type", "claude")

        await self.artifacts.scan_and_register(run["id"], run["ticket"], "DESIGN_RUNNING", wt)
        all_artifacts = await self.artifacts.get_by_run(run["id"])
        verdict, detail = self._evaluate_design(all_artifacts, job)

        if verdict == "accept" or turn >= self._design_max_turns:
            await self.artifacts.submit_all(run["id"], "DESIGN_RUNNING")
            if hasattr(self.executor, 'close_session'):
                await self.executor.close_session(run["id"], job_agent)
            await self._update_stage(run["id"], "DESIGN_RUNNING", "DESIGN_REVIEW")
        elif verdict == "revise":
            revision_path = os.path.join(self.coop_dir, "runs", run["id"], f"TURN-revision-{turn+1}.md")
            os.makedirs(os.path.dirname(revision_path), exist_ok=True)
            await self.artifacts.render_task(
                "templates/TURN-revision.md",
                {"turn": turn + 1, "feedback": detail, "ticket": run["ticket"],
                 "missing_artifacts": []},
                revision_path,
            )
            await self._emit(run["id"], "turn.completed", {"turn_num": turn, "verdict": verdict, "detail": detail})
            if hasattr(self.executor, 'send_followup'):
                await self._emit(run["id"], "turn.started", {"turn_num": turn + 1, "agent_type": job_agent})
                if self.jobs:
                    await self.jobs.increment_turn(job["id"])
                    await self.jobs.record_turn(job["id"], turn, revision_path, verdict, detail)
                await self.executor.send_followup(
                    run["id"], job_agent, revision_path, wt, self._execution_timeout("design")
                )

    async def _tick_dev_queued(self, run: dict) -> None:
        """Try to dispatch the dev agent job if a host is available."""
        lock = self._dispatch_locks.setdefault(run["id"], asyncio.Lock())
        async with lock:
            current = await self._get_run(run["id"])
            if current["status"] != "running" or current["current_stage"] != "DEV_QUEUED":
                return
            if self.jobs and await self.jobs.get_active_job(run["id"]):
                return

            preferred = run.get("dev_agent") or "claude"
            fallback = "codex" if preferred == "claude" else "claude"

            host = await self.hosts.select_host(preferred)
            actual_agent = preferred
            if not host:
                host = await self.hosts.select_host(fallback)
                actual_agent = fallback
            if not host:
                await self._emit_limited(run["id"], "host.unavailable", {
                    "stage": "DEV_QUEUED",
                    "agent_type": preferred,
                    "ticket": run["ticket"],
                }, limit_keys=("stage",))
                return
            if actual_agent != preferred:
                await self._emit(run["id"], "agent.fallback", {
                    "stage": "DEV_QUEUED",
                    "preferred": preferred,
                    "actual": actual_agent,
                    "ticket": run["ticket"],
                })

            branch, wt = await self._resolve_worktree(run["repo_path"], run["ticket"], "dev")

            now = datetime.now(timezone.utc).isoformat()
            await self.db.execute(
                "UPDATE runs SET dev_worktree=?, dev_branch=?, updated_at=? WHERE id=?",
                (wt, branch, now, run["id"]),
            )

            task_path = os.path.join(self.coop_dir, "runs", run["id"], "TASK-dev.md")
            os.makedirs(os.path.dirname(task_path), exist_ok=True)
            design_arts = await self.artifacts.get_by_run(run["id"], kind="design")
            design_path = ""
            if design_arts:
                latest_design = design_arts[-1]
                source_path = Path(latest_design["path"])
                design_path = self._copy_file_to_worktree(
                    source_path,
                    wt,
                    Path("docs") / "design" / source_path.name,
                )

            template = "templates/INIT-dev.md"

            await self.artifacts.render_task(
                template,
                {
                    "run_id": run["id"],
                    "ticket": run["ticket"],
                    "repo_path": run["repo_path"],
                    "worktree": wt,
                    "design_path": design_path,
                },
                task_path,
            )

            timeout_sec = self._execution_timeout("dev")
            try:
                if hasattr(self.executor, 'start_session'):
                    await self.executor.start_session(run["id"], host, actual_agent, task_path, wt, timeout_sec)
                else:
                    await self.executor.dispatch(run["id"], host, actual_agent, task_path, wt, timeout_sec)
            except asyncio.TimeoutError:
                job = await self.db.fetchone(
                    "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
                    (run["id"],),
                )
                if job:
                    await self._emit_limited(
                        run["id"],
                        "job.timeout",
                        {"run_id": run["id"], "job_id": job["id"], "stage": job.get("stage", "")},
                        limit_keys=("job_id",),
                    )
                return
            current = await self._get_run(run["id"])
            if current["current_stage"] == "DEV_QUEUED":
                await self._update_stage(run["id"], "DEV_QUEUED", "DEV_DISPATCHED")

    async def _tick_dev_dispatched(self, run: dict) -> None:
        """Advance to DEV_RUNNING once the dev job reports as running."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run["id"],),
        )
        if not job:
            return
        job = await self._reconcile_job_session(run, dict(job))
        if job.get("_reconcile_pending"):
            return
        if job["status"] in ("failed", "timeout", "interrupted"):
            await self._transition_to_failed(run, job)
            return
        if job["status"] == "running":
            await self._update_stage(run["id"], "DEV_DISPATCHED", "DEV_RUNNING")

    async def _tick_dev_running(self, run: dict) -> None:
        """Check dev job result, evaluate, and either advance or request revision."""
        job = await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
            (run["id"],),
        )
        if not job:
            return
        job = await self._reconcile_job_session(run, dict(job))
        if job["status"] in ("failed", "timeout", "interrupted"):
            await self._transition_to_failed(run, job)
            return
        if job["status"] != "completed":
            return

        turn = job.get("turn_count") or 1
        wt = run.get("dev_worktree", "")
        job_agent = job.get("agent_type", "codex")

        await self.artifacts.scan_and_register(run["id"], run["ticket"], "DEV_RUNNING", wt)
        all_artifacts = await self.artifacts.get_by_run(run["id"])
        verdict, detail = self._evaluate_dev(all_artifacts, job, wt)

        if verdict == "accept" or turn >= self._dev_max_turns:
            await self.artifacts.submit_all(run["id"], "DEV_RUNNING")
            if hasattr(self.executor, 'close_session'):
                await self.executor.close_session(run["id"], job_agent)
            await self._update_stage(run["id"], "DEV_RUNNING", "DEV_REVIEW")
        elif verdict == "revise":
            revision_path = os.path.join(self.coop_dir, "runs", run["id"], f"TURN-dev-fix-{turn+1}.md")
            os.makedirs(os.path.dirname(revision_path), exist_ok=True)
            await self.artifacts.render_task(
                "templates/TURN-dev-fix.md",
                {"turn": turn + 1, "feedback": detail, "ticket": run["ticket"],
                 "test_failures": []},
                revision_path,
            )
            await self._emit(run["id"], "turn.completed", {"turn_num": turn, "verdict": verdict, "detail": detail})
            if hasattr(self.executor, 'send_followup'):
                await self._emit(run["id"], "turn.started", {"turn_num": turn + 1, "agent_type": job_agent})
                if self.jobs:
                    await self.jobs.increment_turn(job["id"])
                    await self.jobs.record_turn(job["id"], turn, revision_path, verdict, detail)
                await self.executor.send_followup(
                    run["id"], job_agent, revision_path, wt, self._execution_timeout("dev")
                )

    async def _tick_merge_queued(self, run: dict) -> None:
        """Enqueue merge and advance to MERGING."""
        if self.merge:
            await self.merge.enqueue(run["id"], run.get("dev_branch", ""), priority=0)
            await self._update_stage(run["id"], "MERGE_QUEUED", "MERGING")

    async def _tick_merging(self, run: dict) -> None:
        """Check merge result and advance to MERGED or MERGE_CONFLICT."""
        if self.merge:
            status = await self.merge.get_status(run["id"])
            if status in ("waiting", "merging"):
                await self.merge.process_next()
                status = await self.merge.get_status(run["id"])
            if status == "merged":
                await self._update_stage(run["id"], "MERGING", "MERGED")
                now = datetime.now(timezone.utc).isoformat()
                await self.db.execute(
                    "UPDATE runs SET status='completed', updated_at=? WHERE id=?",
                    (now, run["id"]),
                )
                await self._emit(run["id"], "run.completed", {})
                self._release_run_locks(run["id"])
            elif status == "conflict":
                await self._update_stage(run["id"], "MERGING", "MERGE_CONFLICT")

    async def _transition_to_failed(self, run: dict, job: dict) -> None:
        """Transition a run to FAILED when its job has a terminal failure.

        CAS-guarded on ``(id, status='running', current_stage=from_stage)`` so
        that concurrent tick paths observing the same terminal job cannot emit
        duplicate ``run.failed`` events or insert duplicate steps rows.
        """
        run_id = run["id"]
        from_stage = run["current_stage"]
        now = datetime.now(timezone.utc).isoformat()
        rowcount = await self.db.execute_rowcount(
            "UPDATE runs SET status='failed', current_stage='FAILED', "
            "failed_at_stage=?, updated_at=? "
            "WHERE id=? AND status='running' AND current_stage=?",
            (from_stage, now, run_id, from_stage),
        )
        if rowcount != 1:
            return
        await self.db.execute(
            "INSERT INTO steps(run_id,from_stage,to_stage,triggered_by,created_at) "
            "VALUES(?,?,?,?,?)",
            (run_id, from_stage, "FAILED", "system", now),
        )
        await self._emit(run_id, "stage.changed", {"from": from_stage, "to": "FAILED"})
        await self._emit(run_id, "run.failed", {
            "failed_at_stage": from_stage,
            "job_id": job["id"],
            "job_status": job["status"],
        })
        await self._trace_event("run.failed", {"failed_at_stage": from_stage}, level="error")
        await self._snapshot(run_id)
        self._release_run_locks(run_id)

    # ------------------------------------------------------------------
    # Evaluators
    # ------------------------------------------------------------------

    def _evaluate_design(self, artifacts, job=None) -> tuple[str, str]:
        has_design = any(a["kind"] == "design" for a in artifacts)
        has_adr = any(a["kind"] == "adr" for a in artifacts)
        if not has_design:
            return ("revise", "未生成设计文档 DES-{ticket}.md")
        if not has_adr:
            return ("revise", "未生成架构决策记录 ADR-{ticket}.md")
        return ("accept", "")

    def _evaluate_dev(self, artifacts, job=None, worktree=None) -> tuple[str, str]:
        has_test_report = any(a["kind"] == "test-report" for a in artifacts)
        if not has_test_report:
            return ("revise", "未生成测试报告 TEST-REPORT-{ticket}.md")
        return ("accept", "")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_worktree(
        self, repo_path: str, ticket: str, phase: str
    ) -> tuple[str, str]:
        """Return ``(branch, worktree_path)`` using the injected or real implementation."""
        if self._ensure_worktree is not None:
            return await self._ensure_worktree(repo_path, ticket, phase)
        from src.git_utils import ensure_worktree
        return await ensure_worktree(repo_path, ticket, phase)

    async def _get_run(self, run_id: str) -> dict:
        """Fetch a run row or raise NotFoundError.

        The DB column is ``id`` but callers (and tests) expect ``run_id`` as
        well, so both keys are present in the returned dict.
        """
        run = await self.db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
        if not run:
            raise NotFoundError(f"Run {run_id} not found")
        row = dict(run)
        # Expose ``run_id`` as a convenience alias for the ``id`` column so
        # that API response dicts and test assertions can use the friendlier name.
        row.setdefault("run_id", row["id"])
        return row

    def _copy_file_to_worktree(self, source_path: str | Path, worktree: str, relative_target: str | Path) -> str:
        source = Path(source_path)
        target = Path(worktree) / relative_target
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target.relative_to(Path(worktree)).as_posix()

    def _resolve_job_events_path(self, job: dict) -> Path:
        events_path = Path(job["events_file"]) if job.get("events_file") else Path(self.coop_dir) / "jobs" / job["id"] / "events.jsonl"
        if not events_path.is_absolute():
            project_relative = self.project_root / events_path
            events_path = project_relative if project_relative.exists() else Path(self.coop_dir) / events_path
        return events_path

    def _json_contains_stop_reason(self, value, stop_reason: str) -> bool:
        if isinstance(value, dict):
            if value.get("stopReason") == stop_reason:
                return True
            return any(self._json_contains_stop_reason(child, stop_reason) for child in value.values())
        if isinstance(value, list):
            return any(self._json_contains_stop_reason(child, stop_reason) for child in value)
        return False

    def _job_has_stop_reason(self, job: dict, stop_reason: str) -> bool:
        events_path = self._resolve_job_events_path(job)
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

    def _job_is_within_dispatch_grace(self, run: dict, job: dict) -> bool:
        if run.get("current_stage") not in {"DESIGN_DISPATCHED", "DEV_DISPATCHED"}:
            return False
        started_at = job.get("started_at")
        if not started_at:
            return False
        try:
            started = datetime.fromisoformat(started_at)
        except ValueError:
            return False
        age = (datetime.now(timezone.utc) - started).total_seconds()
        return age < self._dispatch_reconcile_grace()

    async def _probe_session_status(self, run: dict, job: dict, host: dict | None = None):
        attempts = self._session_reconcile_attempts()
        delay = self._session_reconcile_delay()
        last_status = None
        for attempt in range(attempts):
            last_status = await self.executor.get_session_status(run["id"], job["agent_type"], host=host)
            session_state = last_status.get("status") if last_status else None
            if session_state in {"running", "alive"}:
                return last_status
            if attempt < attempts - 1 and delay > 0:
                await asyncio.sleep(delay)
        return last_status

    async def _reconcile_job_session(self, run: dict, job: dict) -> dict:
        if job["status"] != "running" or not hasattr(self.executor, "get_session_status"):
            return job

        host = None
        if job.get("host_id"):
            host = await self.db.fetchone("SELECT * FROM agent_hosts WHERE id=?", (job["host_id"],))
            if host:
                host = dict(host)

        status = await self._probe_session_status(run, job, host=host)
        session_state = status.get("status") if status else None
        if session_state in {"running", "alive"}:
            return job

        if self._job_is_within_dispatch_grace(run, job):
            return {**job, "_reconcile_pending": True}

        if self._job_has_stop_reason(job, "end_turn"):
            now = datetime.now(timezone.utc).isoformat()
            await self.jobs.update_status(job["id"], "completed", ended_at=now)
            updated = await self.db.fetchone("SELECT * FROM jobs WHERE id=?", (job["id"],))
            return dict(updated) if updated else {**job, "status": "completed", "ended_at": now}

        now = datetime.now(timezone.utc).isoformat()
        await self.jobs.update_status(job["id"], "interrupted", ended_at=now)
        reason = session_state or "missing"
        await self._emit(run["id"], "job.interrupted", {"job_id": job["id"], "reason": reason})
        updated = await self.db.fetchone("SELECT * FROM jobs WHERE id=?", (job["id"],))
        return dict(updated) if updated else {**job, "status": "interrupted", "ended_at": now}

    async def _trace_event(self, event_type, payload=None, level="info", error_detail=None, duration_ms=None):
        if self._trace:
            await self._trace.emit(event_type, payload, level=level, error_detail=error_detail,
                                   duration_ms=duration_ms, source="state_machine")

    async def _update_stage(
        self,
        run_id: str,
        from_stage: str,
        to_stage: str,
        **extra,
    ) -> bool:
        """Persist a stage transition, record a step row, emit an event, and snapshot.

        Uses compare-and-swap via ``cursor.rowcount``: the UPDATE only takes
        effect when the run is still in ``from_stage``.  Returns ``True`` if
        this call actually made the transition, ``False`` if it was pre-empted
        by a concurrent writer (including another writer targeting the same
        ``to_stage``).
        """
        now = datetime.now(timezone.utc).isoformat()
        rowcount = await self.db.execute_rowcount(
            "UPDATE runs SET current_stage=?, updated_at=? WHERE id=? AND current_stage=?",
            (to_stage, now, run_id, from_stage),
        )
        if rowcount != 1:
            return False

        await self.db.execute(
            "INSERT INTO steps(run_id,from_stage,to_stage,triggered_by,created_at) "
            "VALUES(?,?,?,?,?)",
            (run_id, from_stage, to_stage, "system", now),
        )
        await self._emit(run_id, "stage.changed", {"from": from_stage, "to": to_stage, **extra})
        bind_run(run_id)
        await self._trace_event("stage.transition", {"from": from_stage, "to": to_stage})

        # Emit gate.waiting when entering a review/conflict stage
        _GATE_FOR_STAGE = {"REQ_REVIEW": "req", "DESIGN_REVIEW": "design",
                           "DEV_REVIEW": "dev", "MERGE_CONFLICT": "merge"}
        gate = _GATE_FOR_STAGE.get(to_stage)
        if gate:
            await self._emit(run_id, "gate.waiting", {"gate": gate, "stage": to_stage})

        await self._snapshot(run_id)
        return True

    async def _emit(self, run_id: str, event_type: str, payload: dict | None = None) -> None:
        """Persist an event row and fire the webhook notifier."""
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (run_id, event_type, json.dumps(payload) if payload else None, now),
        )
        if self.webhooks:
            await self.webhooks.notify(event_type, {"run_id": run_id, **(payload or {})})

    async def _emit_limited(
        self,
        run_id: str,
        event_type: str,
        payload: dict | None = None,
        limit_keys: tuple[str, ...] = (),
        max_count: int = 3,
    ) -> None:
        payload = payload or {}
        match_fields = {key: payload.get(key) for key in limit_keys if key in payload}
        if not await can_emit_event(self.db, run_id, event_type, match_fields, max_count=max_count):
            return
        await self._emit(run_id, event_type, payload)

    async def _snapshot(self, run_id: str) -> None:
        """Write a JSON snapshot of the current run state to disk."""
        run = await self._get_run(run_id)
        snap_dir = Path(self.coop_dir) / "runs" / run_id
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "state.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
