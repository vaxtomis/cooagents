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
        self._dispatch_locks = {}

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
            "description,preferences_json,notify_channel,notify_to,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, ticket, repo_path, repo_url, "running", "INIT", description, prefs,
             notify_channel, notify_to, now, now),
        )
        await self._update_stage(run_id, "INIT", "REQ_COLLECTING")
        run = await self._get_run(run_id)
        if warning:
            run["warning"] = warning
        return run

    async def tick(self, run_id: str) -> dict:
        """Advance the run one step if there is an automatic transition available.

        Idempotent: review/waiting stages are no-ops.
        """
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

    async def retry(self, run_id: str, by: str, note: str | None = None) -> dict:
        """Retry a failed run, restoring it to the stage where it failed.

        Raises
        ------
        ConflictError
            If the run is not in ``failed`` status.
        """
        run = await self._get_run(run_id)
        if run["status"] != "failed":
            raise ConflictError("Can only retry failed runs", run["current_stage"])

        failed_stage = run.get("failed_at_stage") or "INIT"
        restore_stage = {
            "DESIGN_DISPATCHED": "DESIGN_QUEUED",
            "DESIGN_RUNNING": "DESIGN_QUEUED",
            "DEV_DISPATCHED": "DEV_QUEUED",
            "DEV_RUNNING": "DEV_QUEUED",
        }.get(failed_stage, failed_stage)
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE runs SET status='running', current_stage=?, updated_at=? WHERE id=?",
            (restore_stage, now, run_id),
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
        return await self._get_run(run_id)

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

    async def _tick_design_queued(self, run: dict) -> None:
        """Try to dispatch the design agent job if a host is available."""
        lock = self._dispatch_locks.setdefault(run["id"], asyncio.Lock())
        async with lock:
            current = await self._get_run(run["id"])
            if current["status"] != "running" or current["current_stage"] != "DESIGN_QUEUED":
                return
            if self.jobs and await self.jobs.get_active_job(run["id"]):
                return

            host = await self.hosts.select_host("claude")
            if not host:
                await self._emit_limited(run["id"], "host.unavailable", {
                    "stage": "DESIGN_QUEUED",
                    "agent_type": "claude",
                    "ticket": run["ticket"],
                }, limit_keys=("stage",))
                return

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
                    await self.executor.start_session(run["id"], host, "claude", task_path, wt, timeout_sec)
                else:
                    await self.executor.dispatch(run["id"], host, "claude", task_path, wt, timeout_sec)
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

        await self.artifacts.scan_and_register(run["id"], run["ticket"], "DESIGN_RUNNING", wt)
        all_artifacts = await self.artifacts.get_by_run(run["id"])
        verdict, detail = self._evaluate_design(all_artifacts, job)

        if verdict == "accept" or turn >= self._design_max_turns:
            await self.artifacts.submit_all(run["id"], "DESIGN_RUNNING")
            if hasattr(self.executor, 'close_session'):
                await self.executor.close_session(run["id"], "claude")
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
                await self._emit(run["id"], "turn.started", {"turn_num": turn + 1, "agent_type": "claude"})
                if self.jobs:
                    await self.jobs.increment_turn(job["id"])
                    await self.jobs.record_turn(job["id"], turn, revision_path, verdict, detail)
                await self.executor.send_followup(
                    run["id"], "claude", revision_path, wt, self._execution_timeout("design")
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

            host = await self.hosts.select_host("codex")
            if not host:
                await self._emit_limited(run["id"], "host.unavailable", {
                    "stage": "DEV_QUEUED",
                    "agent_type": "codex",
                    "ticket": run["ticket"],
                }, limit_keys=("stage",))
                return

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
                    await self.executor.start_session(run["id"], host, "codex", task_path, wt, timeout_sec)
                else:
                    await self.executor.dispatch(run["id"], host, "codex", task_path, wt, timeout_sec)
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

        await self.artifacts.scan_and_register(run["id"], run["ticket"], "DEV_RUNNING", wt)
        all_artifacts = await self.artifacts.get_by_run(run["id"])
        verdict, detail = self._evaluate_dev(all_artifacts, job, wt)

        if verdict == "accept" or turn >= self._dev_max_turns:
            await self.artifacts.submit_all(run["id"], "DEV_RUNNING")
            if hasattr(self.executor, 'close_session'):
                await self.executor.close_session(run["id"], "codex")
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
                await self._emit(run["id"], "turn.started", {"turn_num": turn + 1, "agent_type": "codex"})
                if self.jobs:
                    await self.jobs.increment_turn(job["id"])
                    await self.jobs.record_turn(job["id"], turn, revision_path, verdict, detail)
                await self.executor.send_followup(
                    run["id"], "codex", revision_path, wt, self._execution_timeout("dev")
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
            if status == "merged":
                await self._update_stage(run["id"], "MERGING", "MERGED")
                now = datetime.now(timezone.utc).isoformat()
                await self.db.execute(
                    "UPDATE runs SET status='completed', updated_at=? WHERE id=?",
                    (now, run["id"]),
                )
                await self._emit(run["id"], "run.completed", {})
            elif status == "conflict":
                await self._update_stage(run["id"], "MERGING", "MERGE_CONFLICT")

    async def _transition_to_failed(self, run: dict, job: dict) -> None:
        """Transition a run to FAILED status when its job has a terminal failure."""
        run_id = run["id"]
        from_stage = run["current_stage"]
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE runs SET status='failed', current_stage='FAILED', "
            "failed_at_stage=?, updated_at=? WHERE id=?",
            (from_stage, now, run_id),
        )
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
        await self._snapshot(run_id)

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

    async def _reconcile_job_session(self, run: dict, job: dict) -> dict:
        if job["status"] != "running" or not hasattr(self.executor, "get_session_status"):
            return job

        host = None
        if job.get("host_id"):
            host = await self.db.fetchone("SELECT * FROM agent_hosts WHERE id=?", (job["host_id"],))
            if host:
                host = dict(host)

        status = await self.executor.get_session_status(run["id"], job["agent_type"], host=host)
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

    async def _update_stage(
        self,
        run_id: str,
        from_stage: str,
        to_stage: str,
        **extra,
    ) -> None:
        """Persist a stage transition, record a step row, emit an event, and snapshot."""
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE runs SET current_stage=?, updated_at=? WHERE id=?",
            (to_stage, now, run_id),
        )
        await self.db.execute(
            "INSERT INTO steps(run_id,from_stage,to_stage,triggered_by,created_at) "
            "VALUES(?,?,?,?,?)",
            (run_id, from_stage, to_stage, "system", now),
        )
        await self._emit(run_id, "stage.changed", {"from": from_stage, "to": to_stage, **extra})

        # Emit gate.waiting when entering a review/conflict stage
        _GATE_FOR_STAGE = {"REQ_REVIEW": "req", "DESIGN_REVIEW": "design",
                           "DEV_REVIEW": "dev", "MERGE_CONFLICT": "merge"}
        gate = _GATE_FOR_STAGE.get(to_stage)
        if gate:
            await self._emit(run_id, "gate.waiting", {"gate": gate, "stage": to_stage})

        await self._snapshot(run_id)

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
