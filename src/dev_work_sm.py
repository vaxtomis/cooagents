"""DevWork state machine (Phase 4).

Steps (PRD L184-189):
    INIT -> STEP1_VALIDATE -> STEP2_ITERATION -> STEP3_CONTEXT
         -> STEP4_DEVELOP -> STEP5_REVIEW
    STEP5 score >= threshold -> COMPLETED
    STEP5 problem_category=req_gap   -> back to STEP2_ITERATION
    STEP5 problem_category=impl_gap  -> back to STEP4_DEVELOP
    STEP5 problem_category=design_hollow -> ESCALATED
    iteration_rounds > max_rounds -> ESCALATED

Drives asynchronously after ``create()``: the caller schedules
``asyncio.create_task(self.run_to_completion(id))``. Each step handler is
idempotent so an interrupted task can be resumed by ``tick(id)``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.design_validator import validate_design_markdown
from src.dev_work_steps import DevWorkStepHandlersMixin
from src.exceptions import BadRequestError, NotFoundError
from src.git_utils import ensure_worktree, run_git
from src.models import AgentKind, DevWorkStep, ProblemCategory
from src.reviewer import ReviewOutcome
from src.workspace_events import emit_workspace_event

logger = logging.getLogger(__name__)

_TERMINAL = {
    DevWorkStep.COMPLETED,
    DevWorkStep.ESCALATED,
    DevWorkStep.CANCELLED,
}

# Hard upper bound on ticks driven by ``run_to_completion``. Serves as a
# circuit-breaker: if a retry path ever fails to transition the current_step
# (intentionally in-place retries advance only via gates_json), the driver
# would otherwise spin forever. With max_rounds=5 and at most a dozen ticks
# per round, 100 leaves a comfortable headroom while still bounding runaway.
_MAX_TICKS = 100


def _decode_gates(blob: str | None) -> dict:
    if not blob:
        return {}
    try:
        data = json.loads(blob)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


class DevWorkStateMachine(DevWorkStepHandlersMixin):
    # Step2–Step5 handlers live in :class:`DevWorkStepHandlersMixin` to keep
    # this file under the 800-line project cap. The mixin depends on
    # ``self.db``, ``self.workspaces``, ``self.iteration_notes``,
    # ``self.config.devwork`` and the shared helpers below.
    #
    # Dependencies are typed ``Any`` to avoid hard import cycles (the
    # WorkspaceManager, DesignDocManager, and Executor live alongside this
    # module and import each other). Contracts are enforced at the call
    # sites. Return types on public methods use ``dict[str, Any]`` — the
    # DB row shape is stable (schema.sql) but not expressed as a TypedDict.
    def __init__(
        self,
        db: Any,
        workspaces: Any,      # WorkspaceManager
        design_docs: Any,     # DesignDocManager (Phase 3)
        iteration_notes: Any, # DevIterationNoteManager
        executor: Any,        # async run_once(agent, worktree, timeout, task_file=?, prompt=?)
        config: Any,          # Settings
    ) -> None:
        self.db = db
        self.workspaces = workspaces
        self.design_docs = design_docs
        self.iteration_notes = iteration_notes
        self.executor = executor
        self.config = config
        # Phase 2 manager owns workspaces_root; mirror it for quick path math.
        self.workspaces_root = Path(workspaces.workspaces_root).resolve()
        self._running: dict[str, asyncio.Task] = {}

    # ---- driver ----

    def schedule_driver(self, dev_id: str) -> asyncio.Task:
        task = asyncio.create_task(self.run_to_completion(dev_id))

        def _on_done(t: asyncio.Task) -> None:
            self._running.pop(dev_id, None)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.exception(
                    "dev_work %s driver task failed", dev_id, exc_info=exc
                )

        task.add_done_callback(_on_done)
        self._running[dev_id] = task
        return task

    # ---- helpers ----

    @staticmethod
    def _new_id() -> str:
        return f"dev-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _review_id() -> str:
        return f"rev-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def _get(self, dev_id: str) -> dict[str, Any] | None:
        return await self.db.fetchone(
            "SELECT * FROM dev_works WHERE id=?", (dev_id,)
        )

    async def _transition(
        self, dw: dict[str, Any], frm: DevWorkStep, to: DevWorkStep
    ) -> None:
        now = self._now()
        rc = await self.db.execute_rowcount(
            "UPDATE dev_works SET current_step=?, updated_at=? "
            "WHERE id=? AND current_step=?",
            (to.value, now, dw["id"], frm.value),
        )
        if rc == 0:
            logger.warning(
                "dev_work %s already past %s", dw["id"], frm.value
            )
        try:
            await self.workspaces.refresh_workspace_md(dw["workspace_id"])
        except Exception:
            logger.exception(
                "refresh_workspace_md failed for %s", dw["workspace_id"]
            )

    async def _update_gates_field(
        self, dev_id: str, key: str, value: Any
    ) -> None:
        row = await self._get(dev_id)
        if row is None:
            return
        gates = _decode_gates(row.get("gates_json"))
        gates[key] = value
        await self.db.execute(
            "UPDATE dev_works SET gates_json=?, updated_at=? WHERE id=?",
            (json.dumps(gates, ensure_ascii=False), self._now(), dev_id),
        )

    async def _gates(self, dev_id: str) -> dict[str, Any]:
        row = await self._get(dev_id)
        return _decode_gates(row.get("gates_json") if row else None)

    # ---- public API ----

    async def create(
        self,
        *,
        workspace_id: str,
        design_doc_id: str,
        repo_path: str,
        prompt: str,
        agent: str = AgentKind.claude.value,
    ) -> dict[str, Any]:
        ws = await self.workspaces.get(workspace_id)
        if ws is None:
            raise NotFoundError(f"workspace {workspace_id!r} not found")
        if ws["status"] != "active":
            raise BadRequestError(
                f"workspace {workspace_id!r} is archived; cannot create DevWork"
            )

        dd = await self.db.fetchone(
            "SELECT * FROM design_docs WHERE id=?", (design_doc_id,)
        )
        if dd is None:
            raise NotFoundError(f"design_doc {design_doc_id!r} not found")
        if dd["status"] != "published":
            raise BadRequestError(
                f"design_doc {design_doc_id!r} status={dd['status']!r}; "
                "DevWork requires a published design_doc"
            )
        if dd["workspace_id"] != workspace_id:
            raise BadRequestError(
                f"design_doc {design_doc_id!r} belongs to workspace "
                f"{dd['workspace_id']!r}, not {workspace_id!r}"
            )

        dev_id = self._new_id()
        now = self._now()
        await self.db.execute(
            """INSERT INTO dev_works
               (id, workspace_id, design_doc_id, repo_path, prompt,
                worktree_path, worktree_branch, current_step,
                iteration_rounds, first_pass_success, last_score,
                last_problem_category, agent, gates_json,
                escalated_at, completed_at, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                dev_id,
                workspace_id,
                design_doc_id,
                repo_path,
                prompt,
                None,
                None,
                DevWorkStep.INIT.value,
                0,
                None,
                None,
                None,
                agent,
                None,
                None,
                None,
                now,
                now,
            ),
        )
        await emit_workspace_event(
            self.db,
            event_name="dev_work.started",
            workspace_id=workspace_id,
            correlation_id=dev_id,
            payload={"design_doc_id": design_doc_id, "repo_path": repo_path},
        )
        try:
            await self.workspaces.refresh_workspace_md(workspace_id)
        except Exception:
            logger.exception(
                "initial refresh_workspace_md failed for %s", workspace_id
            )
        return await self._get(dev_id)

    async def tick(self, dev_id: str) -> dict[str, Any]:
        dw = await self._get(dev_id)
        if dw is None:
            raise NotFoundError(f"dev_work {dev_id!r} not found")
        step = DevWorkStep(dw["current_step"])
        handler = {
            DevWorkStep.INIT: self._s0_init,
            DevWorkStep.STEP1_VALIDATE: self._s1_validate,
            DevWorkStep.STEP2_ITERATION: self._s2_iteration,
            DevWorkStep.STEP3_CONTEXT: self._s3_context,
            DevWorkStep.STEP4_DEVELOP: self._s4_develop,
            DevWorkStep.STEP5_REVIEW: self._s5_review,
            DevWorkStep.COMPLETED: self._noop,
            DevWorkStep.ESCALATED: self._noop,
            DevWorkStep.CANCELLED: self._noop,
        }.get(step)
        if handler is None:
            raise BadRequestError(f"no handler for step {step!r}")
        await handler(dw)
        return await self._get(dev_id)

    async def run_to_completion(self, dev_id: str) -> dict[str, Any]:
        """Tick until a terminal step is reached.

        Hard-capped at ``_MAX_TICKS`` so an inadvertent non-advancing retry
        path cannot spin this coroutine forever. Exceeding the ceiling is
        treated as a bug: escalate and surface a RuntimeError so operators
        see it.
        """
        for _ in range(_MAX_TICKS):
            dw = await self.tick(dev_id)
            if DevWorkStep(dw["current_step"]) in _TERMINAL:
                return dw
        logger.error(
            "dev_work %s exceeded %s ticks without reaching terminal",
            dev_id,
            _MAX_TICKS,
        )
        dw = await self._get(dev_id)
        if dw is not None and DevWorkStep(dw["current_step"]) not in _TERMINAL:
            await self._escalate(
                dw,
                reason=f"run_to_completion exceeded {_MAX_TICKS} ticks",
                problem_category=None,
            )
        raise RuntimeError(
            f"dev_work {dev_id!r} exceeded {_MAX_TICKS} ticks without terminal"
        )

    async def cancel(self, dev_id: str) -> None:
        now = self._now()
        rowcount = await self.db.execute_rowcount(
            "UPDATE dev_works SET current_step=?, updated_at=? "
            "WHERE id=? AND current_step NOT IN (?, ?, ?)",
            (
                DevWorkStep.CANCELLED.value,
                now,
                dev_id,
                DevWorkStep.COMPLETED.value,
                DevWorkStep.ESCALATED.value,
                DevWorkStep.CANCELLED.value,
            ),
        )
        if rowcount == 0:
            raise NotFoundError(
                f"dev_work {dev_id!r} not found or already terminal"
            )
        dw = await self._get(dev_id)
        await emit_workspace_event(
            self.db,
            event_name="dev_work.cancelled",
            workspace_id=dw["workspace_id"],
            correlation_id=dev_id,
        )
        task = self._running.pop(dev_id, None)
        if task is not None:
            task.cancel()

    # ---- step handlers ----

    async def _noop(self, dw: dict[str, Any]) -> None:
        return

    async def _s0_init(self, dw: dict[str, Any]) -> None:
        """Lazily create the git worktree under workspaces_root/.coop/worktrees/."""
        ws = await self.workspaces.get(dw["workspace_id"])
        short_id = dw["id"].removeprefix("dev-")  # hex12
        branch = f"devwork/{ws['slug']}-{short_id}"          # C2
        branch_safe = branch.replace("/", "-")
        wt_path = str(
            self.workspaces_root / ".coop" / "worktrees" / branch_safe
        )                                                     # C3
        try:
            _, wt_path = await ensure_worktree(
                dw["repo_path"], branch, wt_path
            )
        except Exception as exc:
            logger.exception("dev_work %s ensure_worktree failed", dw["id"])
            await self._escalate(
                dw,
                reason=f"ensure_worktree failed: {exc}",
                problem_category=None,
            )
            return
        await self.db.execute(
            "UPDATE dev_works SET worktree_path=?, worktree_branch=?, "
            "updated_at=? WHERE id=? AND worktree_path IS NULL",
            (wt_path, branch, self._now(), dw["id"]),
        )
        # Re-fetch since we UPDATEd out-of-band, so _transition's CAS sees
        # the right pre-image.
        refreshed = await self._get(dw["id"])
        await self._transition(
            refreshed, DevWorkStep.INIT, DevWorkStep.STEP1_VALIDATE
        )

    async def _s1_validate(self, dw: dict[str, Any]) -> None:
        """Revalidate design_doc on every entry (B4)."""
        dd = await self.db.fetchone(
            "SELECT * FROM design_docs WHERE id=?", (dw["design_doc_id"],)
        )
        if dd is None or dd["status"] != "published":
            await self._escalate(
                dw,
                reason=f"design_doc missing or not published (status="
                f"{dd['status'] if dd else 'missing'})",
                problem_category=ProblemCategory.design_hollow,
            )
            return
        try:
            text = Path(dd["path"]).read_text(encoding="utf-8")
        except OSError as exc:
            await self._escalate(
                dw,
                reason=f"design_doc file unreadable: {exc}",
                problem_category=ProblemCategory.design_hollow,
            )
            return
        report = validate_design_markdown(
            text,
            required_sections=self.config.design.required_sections,
            mockup_sections=self.config.design.mockup_sections,
        )
        if not report.ok:
            await self._escalate(
                dw,
                reason=f"design_doc schema invalid: {report.all_missing()}",
                problem_category=ProblemCategory.design_hollow,
            )
            return
        await self._transition(
            dw, DevWorkStep.STEP1_VALIDATE, DevWorkStep.STEP2_ITERATION
        )


    # ---- shared helpers ----

    async def _run_llm(
        self,
        dw: dict[str, Any],
        *,
        agent: str,
        worktree: str,
        timeout: int,
        task_file: str,
        step_tag: str,
        round_n: int,
    ) -> tuple[int, str]:
        """Wrapper around ``executor.run_once`` with uniform event emission."""
        try:
            stdout, rc = await self.executor.run_once(
                agent, worktree, timeout, task_file=task_file,
            )
        except Exception:
            logger.exception(
                "dev_work %s LLM call failed at %s round=%s",
                dw["id"],
                step_tag,
                round_n,
            )
            rc = 1
            stdout = ""
        await emit_workspace_event(
            self.db,
            event_name="dev_work.step_completed",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={"step": step_tag, "round": round_n, "rc": rc},
        )
        return rc, stdout or ""

    async def _collect_diff(self, worktree_path: str) -> str:
        """Best-effort `git diff` collection; returns empty string on failure."""
        if not worktree_path:
            return ""
        try:
            out, _err, rc = await run_git(
                "diff", "HEAD", cwd=worktree_path, check=False
            )
        except Exception:
            logger.exception("collect_diff failed at %s", worktree_path)
            return ""
        return out if rc == 0 else ""

    async def _resolve_rubric_threshold(self, dw: dict[str, Any]) -> int:
        """Prefer design_doc.rubric_threshold; fall back to scoring default."""
        row = await self.db.fetchone(
            "SELECT rubric_threshold FROM design_docs WHERE id=?",
            (dw["design_doc_id"],),
        )
        if row is not None:
            try:
                rt = int(row["rubric_threshold"])
                if 1 <= rt <= 100:
                    return rt
            except (TypeError, ValueError):
                pass
        return self.config.scoring.default_threshold

    async def _last_review_text(self, dev_id: str) -> str:
        """Render previous-round reviewer issues into a markdown blob.

        Returns an empty string for round 1 (no prior review).  The composer
        substitutes the explicit 「首轮」 placeholder when it sees an empty
        string, so we do not need to emit one here.
        """
        row = await self.db.fetchone(
            "SELECT score, problem_category, issues_json FROM reviews "
            "WHERE dev_work_id=? ORDER BY round DESC LIMIT 1",
            (dev_id,),
        )
        if row is None:
            return ""
        try:
            issues = json.loads(row["issues_json"]) if row["issues_json"] else []
        except (ValueError, TypeError):
            issues = []
        header = (
            f"上一轮评分 {row['score']}，problem_category="
            f"{row['problem_category']}"
        )
        if not issues:
            return f"{header}\n(无具体 issue)"
        lines = [header]
        for it in issues:
            if isinstance(it, dict):
                dim = it.get("dimension") or it.get("kind") or ""
                msg = it.get("message") or it.get("detail") or ""
                lines.append(f"- [{dim}] {msg}" if dim else f"- {msg}")
            else:
                lines.append(f"- {it}")
        return "\n".join(lines)

    async def _record_review(
        self,
        dw: dict[str, Any],
        *,
        note_id: str,
        round_n: int,
        outcome: ReviewOutcome,
    ) -> None:
        now = self._now()
        await self.db.execute(
            """INSERT INTO reviews
               (id, dev_work_id, design_work_id, dev_iteration_note_id,
                round, score, issues_json, findings_json, problem_category,
                reviewer, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                self._review_id(),
                dw["id"],
                None,
                note_id,
                round_n,
                outcome.score,
                json.dumps(outcome.issues, ensure_ascii=False),
                None,
                (
                    outcome.problem_category.value
                    if outcome.problem_category
                    else None
                ),
                dw["agent"],
                now,
            ),
        )
        # Append score to note history for Phase 8 metrics.
        try:
            await self.iteration_notes.append_score(note_id, outcome.score)
        except Exception:
            logger.exception(
                "append_score failed for note %s (dev_work=%s)",
                note_id,
                dw["id"],
            )

    # ---- loop / escalate ----

    async def _loop_or_escalate(
        self,
        dw: dict[str, Any],
        *,
        back_to: DevWorkStep,
        reason: str,
        problem_category: ProblemCategory | None,
    ) -> None:
        next_round = dw["iteration_rounds"] + 1
        if next_round > self.config.devwork.max_rounds:
            await self._escalate(
                dw, reason=reason, problem_category=problem_category
            )
            return
        now = self._now()
        category_value = (
            problem_category.value if problem_category else None
        )
        await self.db.execute(
            "UPDATE dev_works SET iteration_rounds=?, current_step=?, "
            "last_problem_category=?, updated_at=? WHERE id=?",
            (
                next_round,
                back_to.value,
                category_value,
                now,
                dw["id"],
            ),
        )
        await emit_workspace_event(
            self.db,
            event_name="dev_work.round_completed",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={
                "round": next_round,
                "back_to": back_to.value,
                "problem_category": category_value,
                "reason": reason,
            },
        )
        try:
            await self.workspaces.refresh_workspace_md(dw["workspace_id"])
        except Exception:
            logger.exception(
                "refresh_workspace_md failed for %s", dw["workspace_id"]
            )

    async def _escalate(
        self,
        dw: dict[str, Any],
        *,
        reason: str,
        problem_category: ProblemCategory | None,
    ) -> None:
        now = self._now()
        category_value = (
            problem_category.value if problem_category else None
        )
        await self.db.execute(
            "UPDATE dev_works SET current_step=?, escalated_at=?, "
            "last_problem_category=?, updated_at=? WHERE id=?",
            (
                DevWorkStep.ESCALATED.value,
                now,
                category_value,
                now,
                dw["id"],
            ),
        )
        await emit_workspace_event(
            self.db,
            event_name="dev_work.escalated",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={
                "reason": reason,
                "problem_category": category_value,
                "rounds": dw["iteration_rounds"],
            },
        )
        await emit_workspace_event(
            self.db,
            event_name="workspace.human_intervention",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={
                "subject": "dev_work",
                "reason": reason,
                "problem_category": category_value,
            },
        )
        try:
            await self.workspaces.refresh_workspace_md(dw["workspace_id"])
        except Exception:
            logger.exception(
                "refresh_workspace_md failed for %s", dw["workspace_id"]
            )
