"""DevWork state machine (Phase 4).

Steps (PRD L184-189):
    INIT -> STEP1_VALIDATE -> STEP2_ITERATION -> STEP3_CONTEXT
         -> STEP4_DEVELOP -> STEP5_REVIEW
    STEP5 score >= threshold -> COMPLETED
    STEP5 problem_category=req_gap   -> back to STEP2_ITERATION
    STEP5 problem_category=impl_gap  -> back to STEP2_ITERATION
        (impl gaps are part of the iteration: re-plan with the failure
        signal as input rather than blindly re-coding the same design)
    STEP5 problem_category=design_hollow -> ESCALATED
    iteration_rounds reaches max_rounds -> ESCALATED; explicit continue resumes

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
from src.dev_prompt_composer import MountTableEntry
from src.dev_work_steps import DevWorkStepHandlersMixin
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.git_utils import DEVWORK_BRANCH_FMT, ensure_worktree
from src.llm_runner import IdleTimeoutError, ProgressTick, dw_session_name
from src.models import (
    REPO_ROLE_PRIMARY_PRIORITY,
    DevRepoRef,
    DevWorkStep,
    ProblemCategory,
)
from src.reviewer import ReviewOutcome
from src.workspace_events import emit_and_deliver, emit_workspace_event

logger = logging.getLogger(__name__)

_TERMINAL = {
    DevWorkStep.COMPLETED,
    DevWorkStep.ESCALATED,
    DevWorkStep.CANCELLED,
}
_HARD_MAX_ROUNDS = 50

# Hard upper bound on ticks driven by ``run_to_completion``. Serves as a
# circuit-breaker: if a retry path ever fails to transition the current_step
# (intentionally in-place retries advance only via gates_json), the driver
# would otherwise spin forever. With max_rounds capped at 50 and at most a
# dozen ticks per round, 600 leaves headroom while still bounding runaway.
_MAX_TICKS = 600


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
        registry: Any,        # WorkspaceFileRegistry (Phase 3)
        webhooks: Any = None,  # WebhookNotifier (optional; None disables deliver side-channel)
        agent_host_repo: Any = None,      # Phase 8a: AgentHostRepo
        agent_dispatch_repo: Any = None,  # Phase 8a: AgentDispatchRepo
        *,
        llm_runner: Any,                  # Phase 2: LLMRunner — required.
    ) -> None:
        self.db = db
        self.workspaces = workspaces
        self.design_docs = design_docs
        self.iteration_notes = iteration_notes
        # Phase 2: executor stays on the ctor signature (DesignWork shares the
        # lifespan factory) but DevWork no longer reads it; _run_llm routes
        # through llm_runner. Slated for removal in Phase 7 cleanup.
        self.executor = executor
        self.config = config
        self.registry = registry
        self.webhooks = webhooks
        self.agent_host_repo = agent_host_repo
        self.agent_dispatch_repo = agent_dispatch_repo
        self.llm_runner = llm_runner
        # Phase 2 manager owns workspaces_root; mirror it for quick path math
        # (absolute paths embedded in LLM prompts).
        self.workspaces_root = Path(workspaces.workspaces_root).resolve()
        self._running: dict[str, asyncio.Task] = {}
        # Phase 9: in-memory cache of active acpx sessions per dev_work_id.
        # Map: dev_id -> {role: Session}. Cleared on round transition or
        # terminal state. SM crash mid-round leaves stale acpx sessions on
        # disk; the next boot's :meth:`LLMRunner.orphan_sweep_at_boot` reaps
        # them, so this cache is purely a process-local mirror.
        self._active_sessions: dict[str, dict[str, Any]] = {}

    def _abs_for(self, ws: dict[str, Any], relative_path: str) -> str:
        """Compose an absolute POSIX path under ``<root>/<slug>/`` for LLM use."""
        return (self.workspaces_root / ws["slug"] / relative_path).as_posix()

    # ---- driver ----

    def schedule_driver(self, dev_id: str) -> asyncio.Task:
        existing = self._running.get(dev_id)
        if existing is not None:
            if not existing.done():
                return existing
            self._running.pop(dev_id, None)

        task = asyncio.create_task(self.run_to_completion(dev_id))

        def _on_done(t: asyncio.Task) -> None:
            if self._running.get(dev_id) is t:
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

    def is_running(self, dev_id: str) -> bool:
        task = self._running.get(dev_id)
        if task is None:
            return False
        if task.done():
            self._running.pop(dev_id, None)
            return False
        return True

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
            await self.workspaces.regenerate_workspace_md(dw["workspace_id"])
        except Exception:
            logger.exception(
                "regenerate_workspace_md failed for %s", dw["workspace_id"]
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

    async def _append_loop_feedback(
        self,
        dev_id: str,
        *,
        for_round: int,
        back_to: DevWorkStep,
        reason: str,
        problem_category: ProblemCategory | None,
    ) -> None:
        row = await self._get(dev_id)
        if row is None:
            return
        gates = _decode_gates(row.get("gates_json"))
        entries = gates.get("loop_feedback")
        if not isinstance(entries, list):
            entries = []
        clean_entries = [item for item in entries if isinstance(item, dict)]
        clean_entries.append(
            {
                "for_round": for_round,
                "back_to": back_to.value,
                "reason": reason,
                "problem_category": (
                    problem_category.value if problem_category else None
                ),
            }
        )
        gates["loop_feedback"] = clean_entries[-50:]
        await self.db.execute(
            "UPDATE dev_works SET gates_json=?, updated_at=? WHERE id=?",
            (json.dumps(gates, ensure_ascii=False), self._now(), dev_id),
        )

    async def _loop_feedback_for_round(
        self,
        dev_id: str,
        round_n: int,
        back_to: DevWorkStep | None = None,
    ) -> str:
        gates = await self._gates(dev_id)
        entries = gates.get("loop_feedback")
        if not isinstance(entries, list):
            return ""
        lines: list[str] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            if item.get("for_round") != round_n:
                continue
            if back_to is not None and item.get("back_to") != back_to.value:
                continue
            reason = str(item.get("reason") or "").strip()
            if not reason:
                continue
            category = item.get("problem_category")
            prefix = str(item.get("back_to") or "retry")
            if category:
                prefix = f"{prefix} / {category}"
            lines.append(f"- [{prefix}] {reason}")
        return "\n".join(lines)

    def _resolve_max_rounds(self, dw: dict[str, Any]) -> int:
        gates = _decode_gates(dw.get("gates_json"))
        override = gates.get("max_rounds_override")
        if isinstance(override, int) and 0 <= override <= _HARD_MAX_ROUNDS:
            return override
        return self.config.devwork.max_rounds

    def can_continue_after_escalation(self, dw: dict[str, Any]) -> bool:
        if dw.get("current_step") != DevWorkStep.ESCALATED.value:
            return False
        gates = _decode_gates(dw.get("gates_json"))
        return isinstance(gates.get("resume_after_max_rounds"), dict)

    def _validate_max_rounds_override(self, max_rounds: int | None) -> None:
        configured_cap = self.config.devwork.max_rounds
        if max_rounds is not None and not 0 <= max_rounds <= configured_cap:
            raise BadRequestError(
                f"max_rounds override {max_rounds} exceeds configured cap "
                f"{configured_cap}"
            )

    async def _store_max_rounds_resume(
        self,
        dw: dict[str, Any],
        *,
        completed_round: int,
        max_rounds: int,
        back_to: DevWorkStep,
        reason: str,
        problem_category: ProblemCategory | None,
    ) -> None:
        row = await self._get(dw["id"])
        if row is None:
            return
        gates = _decode_gates(row.get("gates_json"))
        gates["resume_after_max_rounds"] = {
            "back_to": back_to.value,
            "reason": reason,
            "problem_category": (
                problem_category.value if problem_category else None
            ),
            "completed_round": completed_round,
            "max_rounds": max_rounds,
            "created_at": self._now(),
        }
        await self.db.execute(
            "UPDATE dev_works SET gates_json=?, updated_at=? WHERE id=?",
            (
                json.dumps(gates, ensure_ascii=False),
                self._now(),
                dw["id"],
            ),
        )

    # ---- Phase 8a host dispatch helpers ----

    async def _pick_host(self, agent: str) -> str:
        from src.agent_hosts.dispatch_decider import choose_configured_host
        from src.models import LOCAL_HOST_ID

        if self.agent_host_repo is None:
            return LOCAL_HOST_ID
        try:
            return await choose_configured_host(self.agent_host_repo, agent)
        except Exception:
            logger.exception("choose_host failed; falling back to local")
            return LOCAL_HOST_ID

    async def _resolve_agent(self, requested: str | None) -> str:
        preferred = getattr(self.config, "preferred_dev_agent", None)
        if self.agent_host_repo is None:
            for candidate in (requested, preferred, "codex", "claude"):
                if candidate in {"codex", "claude"}:
                    return candidate
            return "codex"
        try:
            from src.agent_hosts.dispatch_decider import resolve_configured_agent

            return resolve_configured_agent(
                await self.agent_host_repo.list_all(),
                requested,
                preferred=preferred,
            )
        except Exception:
            logger.exception("resolve DevWork agent failed; using fallback")
            for candidate in (requested, preferred, "codex", "claude"):
                if candidate in {"codex", "claude"}:
                    return candidate
            return "codex"

    async def _open_dispatch(
        self, *, host_id: str, workspace_id: str,
        correlation_id: str, correlation_kind: str,
    ) -> str | None:
        if self.agent_dispatch_repo is None:
            return None
        try:
            ad = await self.agent_dispatch_repo.start(
                host_id=host_id, workspace_id=workspace_id,
                correlation_id=correlation_id, correlation_kind=correlation_kind,
            )
            await self.agent_dispatch_repo.mark_running(ad["id"])
            return ad["id"]
        except Exception:
            logger.exception("agent_dispatches start failed")
            return None

    async def _close_dispatch(
        self, ad_id: str | None, *, state: str, exit_code: int,
    ) -> None:
        if ad_id is None or self.agent_dispatch_repo is None:
            return
        try:
            await self.agent_dispatch_repo.mark_finished(
                ad_id, state=state, exit_code=exit_code,
            )
        except Exception:
            logger.exception("agent_dispatches mark_finished failed")

    # ---- public API ----

    async def create(
        self,
        *,
        workspace_id: str,
        design_doc_id: str,
        repo_refs: list[tuple[DevRepoRef, str | None]],
        prompt: str,
        agent: str | None = None,
        rubric_threshold: int | None = None,
        max_rounds: int | None = None,
    ) -> dict[str, Any]:
        """Create a DevWork plus its ``dev_work_repos`` rows atomically.

        ``repo_refs`` is the validated tuple list returned by
        ``routes._repo_refs_validation.validate_dev_repo_refs`` — each
        entry is ``(DevRepoRef, base_rev_or_none)``. The dev_works INSERT
        and N dev_work_repos INSERTs run inside one transaction so a
        partial failure leaves no orphan dev_works row.
        """
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
        self._validate_max_rounds_override(max_rounds)

        if not repo_refs:
            raise BadRequestError(
                "repo_refs must contain at least one entry"
            )

        dev_id = self._new_id()
        now = self._now()
        resolved_agent = await self._resolve_agent(agent)
        host_id = await self._pick_host(resolved_agent)
        dw_short = dev_id.removeprefix("dev-")
        devwork_branch = DEVWORK_BRANCH_FMT.format(
            slug=ws["slug"], dw_short=dw_short
        )
        gates_payload = {
            key: value
            for key, value in (
                ("rubric_threshold_override", rubric_threshold),
                ("max_rounds_override", max_rounds),
            )
            if value is not None
        }

        async with self.db.transaction():
            await self.db.execute(
                """INSERT INTO dev_works
                   (id, workspace_id, design_doc_id, prompt,
                    worktree_path, worktree_branch, current_step,
                    iteration_rounds, first_pass_success, last_score,
                    last_problem_category, agent, agent_host_id, gates_json,
                    escalated_at, completed_at, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    dev_id,
                    workspace_id,
                    design_doc_id,
                    prompt,
                    None,
                    None,
                    DevWorkStep.INIT.value,
                    0,
                    None,
                    None,
                    None,
                    resolved_agent,
                    host_id,
                    json.dumps(gates_payload) if gates_payload else None,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            for ref, base_rev in repo_refs:
                await self.db.execute(
                    """INSERT INTO dev_work_repos(
                           dev_work_id, repo_id, mount_name, base_branch,
                           base_rev, devwork_branch, push_state, push_err,
                           is_primary, created_at, updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        dev_id,
                        ref.repo_id,
                        ref.mount_name,
                        ref.base_branch,
                        base_rev,
                        devwork_branch,
                        "pending",
                        None,
                        1 if ref.is_primary else 0,
                        now,
                        now,
                    ),
                )

        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="dev_work.started",
            workspace_id=workspace_id,
            correlation_id=dev_id,
            payload={
                "design_doc_id": design_doc_id,
                "agent": resolved_agent,
                "agent_host_id": host_id,
                "repo_refs": [
                    {
                        "repo_id": r.repo_id,
                        "mount_name": r.mount_name,
                        "base_branch": r.base_branch,
                    }
                    for r, _ in repo_refs
                ],
            },
        )
        try:
            await self.workspaces.regenerate_workspace_md(workspace_id)
        except Exception:
            logger.exception(
                "initial regenerate_workspace_md failed for %s", workspace_id
            )
        return await self._get(dev_id)

    async def tick(self, dev_id: str, *, from_driver: bool = False) -> dict[str, Any]:
        dw = await self._get(dev_id)
        if dw is None:
            raise NotFoundError(f"dev_work {dev_id!r} not found")
        if not from_driver and self.is_running(dev_id):
            raise ConflictError(
                f"dev_work {dev_id!r} is already being advanced",
                current_stage=dw["current_step"],
            )
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
            dw = await self.tick(dev_id, from_driver=True)
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
        # Phase 9: cancel the driver task FIRST and let it unwind so any
        # in-flight ``prompt_session_with_progress`` finishes (or errors)
        # cleanly before we tear down the session it is holding. Calling
        # ``delete_session`` while another task is still awaiting on the
        # same ``Session`` object risks a torn-down acpx process under a
        # live caller.
        task = self._running.pop(dev_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                # Driver task may surface CancelledError or any exception
                # raised while unwinding; we don't care which — the row is
                # already CANCELLED and we just need the task to have
                # released its session references.
                pass
        await self._delete_all_sessions(dev_id)
        dw = await self._get(dev_id)
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="dev_work.cancelled",
            workspace_id=dw["workspace_id"],
            correlation_id=dev_id,
        )

    async def continue_after_escalation(
        self,
        dev_id: str,
        *,
        additional_rounds: int,
        rubric_threshold: int | None = None,
    ) -> dict[str, Any]:
        if type(additional_rounds) is not int or additional_rounds < 1:
            raise BadRequestError("additional_rounds must be a positive integer")
        if rubric_threshold is not None and (
            type(rubric_threshold) is not int
            or rubric_threshold < 1
            or rubric_threshold > 100
        ):
            raise BadRequestError(
                "rubric_threshold must be an integer from 1 to 100"
            )
        dw = await self._get(dev_id)
        if dw is None:
            raise NotFoundError(f"dev_work {dev_id!r} not found")
        if self.is_running(dev_id):
            raise ConflictError(
                f"dev_work {dev_id!r} is already being advanced",
                current_stage=dw["current_step"],
            )
        if dw["current_step"] != DevWorkStep.ESCALATED.value:
            raise ConflictError(
                f"dev_work {dev_id!r} is not escalated",
                current_stage=dw["current_step"],
            )

        gates = _decode_gates(dw.get("gates_json"))
        resume = gates.get("resume_after_max_rounds")
        if not isinstance(resume, dict):
            raise ConflictError(
                f"dev_work {dev_id!r} cannot continue; escalation was not "
                "caused by max_rounds",
                current_stage=dw["current_step"],
            )
        try:
            back_to = DevWorkStep(resume["back_to"])
        except (KeyError, ValueError) as exc:
            raise BadRequestError(
                "resume_after_max_rounds has invalid back_to"
            ) from exc
        if back_to in _TERMINAL:
            raise BadRequestError("resume_after_max_rounds points to terminal step")

        try:
            completed_round = int(resume["completed_round"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BadRequestError(
                "resume_after_max_rounds has invalid completed_round"
            ) from exc
        completed_round = max(completed_round, int(dw["iteration_rounds"]))
        max_rounds = completed_round + additional_rounds
        if max_rounds > _HARD_MAX_ROUNDS:
            raise BadRequestError(
                f"continuation would set max_rounds={max_rounds}; "
                f"hard limit is {_HARD_MAX_ROUNDS}"
            )

        now = self._now()
        entries = gates.get("loop_feedback")
        if not isinstance(entries, list):
            entries = []
        clean_entries = [item for item in entries if isinstance(item, dict)]
        problem_category = resume.get("problem_category")
        reason = str(resume.get("reason") or "max_rounds continuation")
        clean_entries.append(
            {
                "for_round": completed_round + 1,
                "back_to": back_to.value,
                "reason": reason,
                "problem_category": problem_category,
            }
        )
        gates["loop_feedback"] = clean_entries[-50:]

        history = gates.get("resume_history")
        if not isinstance(history, list):
            history = []
        clean_history = [item for item in history if isinstance(item, dict)]
        history_entry = {
            "at": now,
            "completed_round": completed_round,
            "additional_rounds": additional_rounds,
            "max_rounds": max_rounds,
            "back_to": back_to.value,
        }
        if rubric_threshold is not None:
            history_entry["rubric_threshold"] = rubric_threshold
        clean_history.append(history_entry)
        gates["resume_history"] = clean_history[-20:]
        gates["max_rounds_override"] = max_rounds
        if rubric_threshold is not None:
            gates["rubric_threshold_override"] = rubric_threshold
        gates.pop("resume_after_max_rounds", None)

        category_value = str(problem_category) if problem_category else None
        rowcount = await self.db.execute_rowcount(
            "UPDATE dev_works SET iteration_rounds=?, current_step=?, "
            "escalated_at=NULL, current_progress_json=NULL, "
            "last_problem_category=?, gates_json=?, updated_at=? "
            "WHERE id=? AND current_step=?",
            (
                completed_round,
                back_to.value,
                category_value,
                json.dumps(gates, ensure_ascii=False),
                now,
                dev_id,
                DevWorkStep.ESCALATED.value,
            ),
        )
        if rowcount == 0:
            raise ConflictError(
                f"dev_work {dev_id!r} changed while continuing",
                current_stage=dw["current_step"],
            )
        event_payload = {
            "additional_rounds": additional_rounds,
            "max_rounds": max_rounds,
            "from_round": completed_round,
            "next_round": completed_round + 1,
            "back_to": back_to.value,
        }
        if rubric_threshold is not None:
            event_payload["rubric_threshold"] = rubric_threshold

        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="dev_work.continued",
            workspace_id=dw["workspace_id"],
            correlation_id=dev_id,
            payload=event_payload,
        )
        try:
            await self.workspaces.regenerate_workspace_md(dw["workspace_id"])
        except Exception:
            logger.exception(
                "regenerate_workspace_md failed for %s", dw["workspace_id"]
            )
        refreshed = await self._get(dev_id)
        assert refreshed is not None
        return refreshed

    # ---- step handlers ----

    async def _noop(self, dw: dict[str, Any]) -> None:
        return

    @staticmethod
    def _select_primary_ref(
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Two-layer primary-ref selection (no keyword matching).

        1. Explicit ``is_primary=1`` row wins (boundary + DB enforce
           "at most one"; we just pick the first such row).
        2. Else the lowest priority index of ``repos.role``; ties broken
           by lexicographic ``mount_name``.
        """
        explicit = [r for r in rows if r.get("is_primary")]
        if explicit:
            return explicit[0]
        priority = {
            role.value: i
            for i, role in enumerate(REPO_ROLE_PRIMARY_PRIORITY)
        }
        return min(
            rows,
            key=lambda r: (
                priority.get(r.get("repo_role") or "other", len(priority)),
                r["mount_name"],
            ),
        )

    async def _s0_init(self, dw: dict[str, Any]) -> None:
        """Materialize one git worktree per mount.

        Phase 6 (devwork-acpx-overhaul): every ``dev_work_repos`` row gets
        its own worktree under
        ``<workspaces_root>/.coop/worktrees/<branch_safe>/<mount_name>/``
        so multi-mount tasks can read/write each repo independently. The
        primary mount's path is mirrored onto the deprecated
        ``dev_works.worktree_path`` column for back-compat (callers that
        still read ``dw["worktree_path"]`` see the primary's path; new
        callers should read ``dev_work_repos.worktree_path`` per row).

        Phase 4 transition: ``git worktree add`` runs against the
        control-plane bare clones at
        ``<workspaces_root>/.coop/registry/repos/<repo_id>.git``. This
        intentionally pollutes the bare's reflog and ``worktrees/``
        metadata; cleanup via ``git worktree prune --expire=now`` is
        deferred to the worker handoff PRD.

        Failure mode: any per-mount ``ensure_worktree`` exception escalates
        the whole DevWork (terminal — no automatic retry). Already-created
        worktrees on healthy mounts are left on disk; ``ensure_worktree`` is
        idempotent so a manually re-created DevWork on the same branch will
        reuse them rather than fail.
        """
        refs = await self.db.fetchall(
            "SELECT dwr.*, r.role AS repo_role "
            "FROM dev_work_repos dwr "
            "JOIN repos r ON r.id = dwr.repo_id "
            "WHERE dwr.dev_work_id=? "
            "ORDER BY dwr.mount_name",
            (dw["id"],),
        )
        if not refs:
            await self._escalate(
                dw,
                reason="dev_work has no repo_refs",
                problem_category=None,
            )
            return

        primary = self._select_primary_ref(refs)
        worktrees_root = (
            self.workspaces_root / ".coop" / "worktrees"
        ).resolve()
        primary_wt_path: str | None = None
        primary_branch: str | None = None

        for ref in refs:
            bare = (
                self.workspaces_root / ".coop" / "registry" / "repos"
                / f"{ref['repo_id']}.git"
            )
            branch = ref["devwork_branch"]
            branch_safe = branch.replace("/", "-")
            mount_name = ref["mount_name"]
            wt_path_obj = (
                worktrees_root / branch_safe / mount_name
            ).resolve()
            # Defense-in-depth: slug + mount regex prevents '..' today, but
            # assert the resolved path stays under .coop/worktrees so a
            # future relaxation can't escape the sandbox.
            try:
                wt_path_obj.relative_to(worktrees_root)
            except ValueError:
                await self._escalate(
                    dw,
                    reason=(
                        f"worktree path escapes .coop/worktrees: "
                        f"{wt_path_obj}"
                    ),
                    problem_category=None,
                )
                return
            wt_path = str(wt_path_obj)
            start_point = ref.get("base_rev") or ref["base_branch"]
            try:
                _, wt_path = await ensure_worktree(
                    str(bare), branch, wt_path, start_point=start_point,
                )
            except Exception as exc:
                logger.exception(
                    "dev_work %s ensure_worktree failed (mount=%s)",
                    dw["id"], mount_name,
                )
                await self._escalate(
                    dw,
                    reason=(
                        f"ensure_worktree failed for mount "
                        f"{mount_name!r}: {exc}"
                    ),
                    problem_category=None,
                )
                return
            # No CAS guard on this per-row UPDATE: ``ensure_worktree`` is
            # idempotent and ``wt_path`` is deterministic in
            # (workspaces_root, branch_safe, mount_name), so re-entering the
            # loop after a partial _s0_init writes the same value back.
            await self.db.execute(
                "UPDATE dev_work_repos SET worktree_path=?, updated_at=? "
                "WHERE dev_work_id=? AND repo_id=?",
                (wt_path, self._now(), dw["id"], ref["repo_id"]),
            )
            if ref["repo_id"] == primary["repo_id"]:
                primary_wt_path = wt_path
                primary_branch = branch

        # Phase 9: anchor every acpx session for this DevWork at the
        # devworks dir. The LLM cd's to mount worktrees via the mount-table
        # prompt block; the anchor itself is a stable, per-DevWork dir
        # outside any worktree so session bookkeeping survives worktree
        # churn.
        ws = await self.workspaces.get(dw["workspace_id"])
        anchor_path_obj = (
            self.workspaces_root / ws["slug"] / "devworks" / dw["id"]
        ).resolve()
        try:
            anchor_path_obj.relative_to(self.workspaces_root)
        except ValueError:
            await self._escalate(
                dw,
                reason=(
                    f"session anchor path escapes workspaces_root: "
                    f"{anchor_path_obj}"
                ),
                problem_category=None,
            )
            return
        anchor_path_obj.mkdir(parents=True, exist_ok=True)
        session_anchor = str(anchor_path_obj)

        # Mirror primary's path onto the deprecated dev_works column for
        # back-compat reads. Conditional UPDATE preserves the CAS guard so
        # a re-tick after a partial _s0_init doesn't clobber the row.
        await self.db.execute(
            "UPDATE dev_works SET worktree_path=?, worktree_branch=?, "
            "session_anchor_path=?, updated_at=? "
            "WHERE id=? AND worktree_path IS NULL",
            (primary_wt_path, primary_branch, session_anchor,
             self._now(), dw["id"]),
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
        ws = await self.workspaces.get(dw["workspace_id"])
        try:
            text = await self.registry.read_text(
                workspace_slug=ws["slug"], relative_path=dd["path"],
            )
        except NotFoundError as exc:
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
                reason=f"design_doc schema invalid: {report.feedback_items()}",
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
        session_role: str | None = None,
    ) -> tuple[int, str]:
        """Wrapper around ``executor.run_once`` with uniform event emission.

        Phase 8a: opens an ``agent_dispatches`` row and routes to the host
        recorded on the dev_works row (defaults to ``"local"``).

        Phase 9: when ``session_role`` is provided (one of
        ``{"plan", "build", "review"}``), routes to
        :meth:`_run_llm_session` which uses ``prompt --session`` against a
        warm acpx session instead of one-shot ``acpx exec``. Lifecycle of
        the session (open / reuse / delete) is owned by the calling step
        handler.
        """
        if session_role is not None:
            return await self._run_llm_session(
                dw, agent=agent, role=session_role, round_n=round_n,
                step_tag=step_tag, task_file=task_file, timeout=timeout,
            )
        host_id = dw.get("agent_host_id") or "local"
        ad_id = await self._open_dispatch(
            host_id=host_id, workspace_id=dw["workspace_id"],
            correlation_id=dw["id"], correlation_kind="dev_work",
        )

        cmd = self.llm_runner._build_oneshot_cmd(
            agent, worktree, timeout,
            task_file=task_file, prompt=None,
        )

        async def heartbeat(tick: ProgressTick) -> None:
            payload = {
                "step": step_tag,
                "round": round_n,
                "elapsed_s": tick.elapsed_s,
                "last_heartbeat_at": tick.ts,
                "dispatch_id": ad_id,
            }
            # Table-only event log (no webhook fan-out per Decision recap #1).
            try:
                await emit_workspace_event(
                    self.db,
                    event_name="dev_work.progress",
                    workspace_id=dw["workspace_id"],
                    correlation_id=dw["id"],
                    payload=payload,
                )
            except Exception:
                logger.warning(
                    "emit_workspace_event(dev_work.progress) failed",
                    exc_info=True,
                )
            # Overwrite dev_works.current_progress_json so GET /dev-works/{id}
            # serves the latest snapshot without a JOIN.
            try:
                await self.db.execute(
                    "UPDATE dev_works SET current_progress_json=?, "
                    "updated_at=? WHERE id=?",
                    (
                        json.dumps(payload, ensure_ascii=False),
                        self._now(),
                        dw["id"],
                    ),
                )
            except Exception:
                logger.warning(
                    "UPDATE dev_works.current_progress_json failed",
                    exc_info=True,
                )

        try:
            stdout, rc, _progress = await self.llm_runner.run_with_progress(
                cmd=cmd,
                cwd=worktree,
                heartbeat=heartbeat,
                heartbeat_interval_s=(
                    self.config.devwork.progress_heartbeat_interval_s
                ),
                idle_timeout_s=self.config.devwork.step_idle_timeout_s,
                step_tag=step_tag,
            )
            dispatch_state = "succeeded" if rc == 0 else "failed"
        except IdleTimeoutError as exc:
            logger.warning(
                "dev_work %s idle_timeout at %s round=%s after %ss",
                dw["id"], step_tag, round_n, exc.idle_window_s,
            )
            rc, stdout, dispatch_state = 124, "", "timeout"
        except Exception:
            logger.exception(
                "dev_work %s LLM call failed at %s round=%s",
                dw["id"], step_tag, round_n,
            )
            rc = 1
            stdout = ""
            dispatch_state = "failed"

        # Clear the progress snapshot — call is over, the route should stop
        # showing a "running" badge regardless of outcome (success / fail /
        # idle_timeout).
        try:
            await self.db.execute(
                "UPDATE dev_works SET current_progress_json=NULL, "
                "updated_at=? WHERE id=?",
                (self._now(), dw["id"]),
            )
        except Exception:
            logger.warning(
                "clear current_progress_json failed", exc_info=True,
            )

        await self._close_dispatch(ad_id, state=dispatch_state, exit_code=rc)
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="dev_work.step_completed",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={"step": step_tag, "round": round_n, "rc": rc},
        )
        return rc, stdout or ""

    # ---- Phase 9 session-mode dispatch -----------------------------------

    async def _run_llm_session(
        self,
        dw: dict[str, Any],
        *,
        agent: str,
        role: str,
        round_n: int,
        step_tag: str,
        task_file: str,
        timeout: int,
    ) -> tuple[int, str]:
        """Session-mode LLM dispatch — Phase 9.

        Looks up the active session for ``(dev_id, round_n, role)``;
        creates it if absent. Runs ``prompt --session`` with the same
        heartbeat + idle_timeout machinery as oneshot. Does NOT delete the
        session — lifecycle is owned by the calling step handler:

          * Step2 deletes the plan session in its finally block.
          * Step3 / Step4 share the build session; Step5 entry deletes it.
          * Step5 deletes the review session in its finally block; round
            transition / terminal cleanup catches anything that slipped.
        """
        anchor = dw.get("session_anchor_path")
        if not anchor:
            # Defensive: _s0_init should always populate this. If a legacy
            # in-flight DevWork is missing the column, fall back to the
            # worktree_path so we never crash mid-round.
            anchor = dw["worktree_path"]
            logger.warning(
                "dev_work %s missing session_anchor_path; falling back "
                "to worktree_path=%r", dw["id"], anchor,
            )
        # Re-validate every read: even though _s0_init checks ``relative_to``
        # on write, a tampered DB row or migration bug could surface a path
        # outside the sandbox. Refusing to dispatch is safer than handing
        # an arbitrary cwd to ``acpx --cwd``.
        try:
            Path(anchor).resolve().relative_to(self.workspaces_root)
        except (ValueError, OSError):
            raise BadRequestError(
                f"dev_work {dw['id']} session anchor escapes "
                f"workspaces_root: {anchor!r}"
            )
        resolved_agent = self.llm_runner._resolve_agent(agent)
        name = dw_session_name(dw["id"], round_n, role)
        per_dw = self._active_sessions.setdefault(dw["id"], {})
        session = per_dw.get(role)
        if session is None or session.name != name:
            # Stale cache (round changed) — close any prior role binding
            # before reopening so process count never drifts upward.
            if session is not None:
                try:
                    await self.llm_runner.delete_session(session)
                except Exception:
                    logger.warning(
                        "stale session cleanup failed for %s",
                        session.name, exc_info=True,
                    )
            session = await self.llm_runner.start_session(
                name=name, anchor_cwd=anchor, agent=resolved_agent,
            )
            per_dw[role] = session

        host_id = dw.get("agent_host_id") or "local"
        ad_id = await self._open_dispatch(
            host_id=host_id, workspace_id=dw["workspace_id"],
            correlation_id=dw["id"], correlation_kind="dev_work",
        )

        async def heartbeat(tick: ProgressTick) -> None:
            payload = {
                "step": step_tag,
                "round": round_n,
                "elapsed_s": tick.elapsed_s,
                "last_heartbeat_at": tick.ts,
                "dispatch_id": ad_id,
            }
            try:
                await emit_workspace_event(
                    self.db,
                    event_name="dev_work.progress",
                    workspace_id=dw["workspace_id"],
                    correlation_id=dw["id"],
                    payload=payload,
                )
            except Exception:
                logger.warning(
                    "emit_workspace_event(dev_work.progress) failed",
                    exc_info=True,
                )
            try:
                await self.db.execute(
                    "UPDATE dev_works SET current_progress_json=?, "
                    "updated_at=? WHERE id=?",
                    (
                        json.dumps(payload, ensure_ascii=False),
                        self._now(),
                        dw["id"],
                    ),
                )
            except Exception:
                logger.warning(
                    "UPDATE dev_works.current_progress_json failed",
                    exc_info=True,
                )

        try:
            stdout, rc, _progress = (
                await self.llm_runner.prompt_session_with_progress(
                    session,
                    task_file=task_file,
                    timeout_sec=timeout,
                    heartbeat=heartbeat,
                    heartbeat_interval_s=(
                        self.config.devwork.progress_heartbeat_interval_s
                    ),
                    idle_timeout_s=self.config.devwork.step_idle_timeout_s,
                    step_tag=step_tag,
                )
            )
            dispatch_state = "succeeded" if rc == 0 else "failed"
        except IdleTimeoutError as exc:
            logger.warning(
                "dev_work %s idle_timeout at %s round=%s after %ss",
                dw["id"], step_tag, round_n, exc.idle_window_s,
            )
            rc, stdout, dispatch_state = 124, "", "timeout"
        except Exception:
            logger.exception(
                "dev_work %s session LLM call failed at %s round=%s",
                dw["id"], step_tag, round_n,
            )
            rc, stdout, dispatch_state = 1, "", "failed"

        try:
            await self.db.execute(
                "UPDATE dev_works SET current_progress_json=NULL, "
                "updated_at=? WHERE id=?",
                (self._now(), dw["id"]),
            )
        except Exception:
            logger.warning(
                "clear current_progress_json failed", exc_info=True,
            )

        await self._close_dispatch(
            ad_id, state=dispatch_state, exit_code=rc,
        )
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="dev_work.step_completed",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={"step": step_tag, "round": round_n, "rc": rc},
        )
        return rc, stdout or ""

    async def _delete_role_session(
        self, dev_id: str, round_n: int, role: str,
    ) -> None:
        """Phase 9: delete one (round, role) session from the cache.

        Best-effort — failures are logged but never raised; the next boot's
        :meth:`LLMRunner.orphan_sweep_at_boot` reaps anything that slipped.
        """
        per_dw = self._active_sessions.get(dev_id)
        if not per_dw:
            return
        target_name = dw_session_name(dev_id, round_n, role)
        session = per_dw.get(role)
        if session is None or session.name != target_name:
            return
        try:
            await self.llm_runner.delete_session(session)
        except Exception:
            logger.warning(
                "delete_role_session failed for %s", session.name,
                exc_info=True,
            )
        per_dw.pop(role, None)
        if not per_dw:
            self._active_sessions.pop(dev_id, None)

    async def _delete_round_sessions(
        self, dev_id: str, round_n: int,
    ) -> None:
        """Phase 9: delete every active session whose name encodes ``round_n``.

        Called on round transition (``_loop_or_escalate`` loop branch) so
        the previous round's sessions die before the new round's plan
        session is opened.
        """
        per_dw = self._active_sessions.get(dev_id)
        if not per_dw:
            return
        marker = f"-r{round_n}-"
        stale_roles = [
            role for role, s in per_dw.items()
            if marker in s.name
        ]
        for role in stale_roles:
            session = per_dw.get(role)
            if session is None:
                continue
            try:
                await self.llm_runner.delete_session(session)
            except Exception:
                logger.warning(
                    "delete_round_session failed for %s", session.name,
                    exc_info=True,
                )
            per_dw.pop(role, None)
        if not per_dw:
            self._active_sessions.pop(dev_id, None)

    async def _delete_all_sessions(self, dev_id: str) -> None:
        """Phase 9: delete every active session for ``dev_id``.

        Wired into terminal handlers (``_escalate``, COMPLETED branch in
        ``_s5_review``, ``cancel``) so the SM never returns control with
        live acpx sessions on disk.
        """
        per_dw = self._active_sessions.pop(dev_id, None)
        if not per_dw:
            return
        for role, session in list(per_dw.items()):
            try:
                await self.llm_runner.delete_session(session)
            except Exception:
                logger.warning(
                    "delete_all_sessions failed for %s", session.name,
                    exc_info=True,
                )

    async def _load_mount_table_entries(
        self, dw: dict[str, Any],
    ) -> tuple[MountTableEntry, ...]:
        """Build the mount-table input ordered primary-first.

        Phase 6: every mount carries its own ``worktree_path`` populated by
        :meth:`_s0_init`. Legacy in-flight rows created before Phase 6 may
        have ``worktree_path IS NULL`` on non-primary mounts — the composer
        renders a placeholder for those (callers are expected to either
        re-run the DevWork or accept the legacy view).

        Sort key intentionally bypasses :meth:`_select_primary_ref` because
        that helper would re-run the auto-selection rule, which can
        disagree with the explicit ``is_primary`` bit recorded by
        ``_s0_init`` if ``repos.role`` was edited between create and
        review.
        """
        rows = await self.db.fetchall(
            "SELECT dwr.repo_id, dwr.mount_name, dwr.base_branch, "
            "dwr.devwork_branch, dwr.is_primary, dwr.worktree_path, "
            "r.role AS repo_role "
            "FROM dev_work_repos dwr "
            "JOIN repos r ON r.id = dwr.repo_id "
            "WHERE dwr.dev_work_id=? "
            "ORDER BY dwr.mount_name",
            (dw["id"],),
        )
        if not rows:
            return ()
        ordered = sorted(
            rows, key=lambda r: (not r["is_primary"], r["mount_name"])
        )
        return tuple(
            MountTableEntry(
                mount_name=r["mount_name"],
                repo_id=r["repo_id"],
                role=r["repo_role"] or "other",
                is_primary=bool(r["is_primary"]),
                base_branch=r["base_branch"],
                devwork_branch=r["devwork_branch"],
                worktree_path=r["worktree_path"],
            )
            for r in ordered
        )

    async def _resolve_rubric_threshold(self, dw: dict[str, Any]) -> int:
        """Prefer per-DevWork override, then design_doc, then scoring default."""
        gates = _decode_gates(dw.get("gates_json"))
        override = gates.get("rubric_threshold_override")
        if isinstance(override, int) and 1 <= override <= 100:
            return override
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

    async def _render_previous_review_markdown(
        self, dev_id: str, round_n: int | None = None
    ) -> str:
        """Render previous-round reviewer issues + hints into a markdown blob.

        Returns an empty string when no prior review exists. Pure (DB-only).
        Phase 5: a forward-looking ``## 下一轮提示`` H2 is appended whenever
        the previous review persisted a non-empty ``next_round_hints`` array.
        Output stays byte-identical to Phase 4 when hints are empty/NULL.
        """
        loop_feedback = (
            await self._loop_feedback_for_round(
                dev_id, round_n, DevWorkStep.STEP2_ITERATION
            )
            if round_n is not None
            else ""
        )
        row = await self.db.fetchone(
            "SELECT score, problem_category, issues_json, "
            "next_round_hints_json FROM reviews "
            "WHERE dev_work_id=? ORDER BY round DESC LIMIT 1",
            (dev_id,),
        )
        if row is None:
            if not loop_feedback:
                return ""
            return (
                "System validation feedback from previous attempt:\n"
                f"{loop_feedback}"
            )
        try:
            issues = json.loads(row["issues_json"]) if row["issues_json"] else []
        except (ValueError, TypeError):
            issues = []
        try:
            hints = (
                json.loads(row["next_round_hints_json"])
                if row["next_round_hints_json"]
                else []
            )
        except (ValueError, TypeError):
            hints = []
        header = (
            f"上一轮评分 {row['score']}，problem_category="
            f"{row['problem_category']}"
        )
        if not issues:
            lines = [header, "(无具体 issue)"]
        else:
            lines = [header]
            for it in issues:
                if isinstance(it, dict):
                    dim = it.get("dimension") or it.get("kind") or ""
                    msg = it.get("message") or it.get("detail") or ""
                    lines.append(f"- [{dim}] {msg}" if dim else f"- {msg}")
                else:
                    lines.append(f"- {it}")
        # Forward-looking hints get their own H2 so Step2 can scan for them
        # explicitly and treat them as "next round must address X" inputs.
        if hints:
            lines.append("")
            lines.append("## 下一轮提示")
            for h in hints:
                if isinstance(h, dict):
                    kind = h.get("kind") or ""
                    msg = h.get("message") or ""
                    mount = h.get("mount") or ""
                    tokens = []
                    if kind:
                        tokens.append(f"[{kind}]")
                    if mount:
                        tokens.append(f"({mount})")
                    if msg:
                        tokens.append(msg)
                    lines.append(f"- {' '.join(tokens)}" if tokens else "-")
                else:
                    lines.append(f"- {h}")
        if loop_feedback:
            lines.append("")
            lines.append("## System validation feedback")
            lines.extend(loop_feedback.splitlines())
        return "\n".join(lines)

    async def _write_previous_review_for_round(
        self,
        dw: dict[str, Any],
        ws: dict[str, Any],
        round_n: int,
    ) -> str | None:
        """Materialize previous-round review markdown to a workspace file.

        Returns the workspace-relative path, or None when round_n == 1
        or no prior review row exists.
        """
        if round_n <= 1:
            return None
        body = await self._render_previous_review_markdown(dw["id"], round_n)
        if not body:
            return None
        rel = (
            f"devworks/{dw['id']}/feedback/feedback-for-round{round_n}.md"
        )
        await self.registry.put_markdown(
            workspace_row=ws, relative_path=rel,
            text=body, kind="feedback",
        )
        return rel

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
                round, score, issues_json, findings_json,
                next_round_hints_json, problem_category,
                reviewer, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                self._review_id(),
                dw["id"],
                None,
                note_id,
                round_n,
                outcome.score,
                json.dumps(outcome.issues, ensure_ascii=False),
                (
                    json.dumps(
                        outcome.plan_verification,
                        ensure_ascii=False,
                    )
                    if outcome.plan_verification
                    else None
                ),
                (
                    json.dumps(outcome.next_round_hints, ensure_ascii=False)
                    if outcome.next_round_hints
                    else None
                ),
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
        # Step4 output validation failures are implementation retries inside
        # the current iteration design, not a new Step2 planning round.
        if back_to == DevWorkStep.STEP4_DEVELOP:
            await self._retry_step4_same_round_or_escalate(
                dw, reason=reason, problem_category=problem_category
            )
            return
        # Step5 parser/artifact failures are review retries for the current
        # iteration note, not new planning rounds.
        if back_to == DevWorkStep.STEP5_REVIEW:
            await self._retry_step5_same_round_or_escalate(
                dw, reason=reason, problem_category=problem_category
            )
            return

        next_round = dw["iteration_rounds"] + 1
        max_rounds = self._resolve_max_rounds(dw)
        if next_round >= max_rounds:
            await self._store_max_rounds_resume(
                dw,
                completed_round=next_round,
                max_rounds=max_rounds,
                back_to=back_to,
                reason=reason,
                problem_category=problem_category,
            )
            category_value = (
                problem_category.value if problem_category else None
            )
            await self.db.execute(
                "UPDATE dev_works SET iteration_rounds=?, "
                "last_problem_category=?, updated_at=? WHERE id=?",
                (next_round, category_value, self._now(), dw["id"]),
            )
            refreshed = await self._get(dw["id"])
            if refreshed is not None:
                dw = refreshed
            await self._escalate(
                dw,
                reason=f"max_rounds reached ({max_rounds}); {reason}",
                problem_category=problem_category,
            )
            return
        await self._append_loop_feedback(
            dw["id"],
            for_round=next_round + 1,
            back_to=back_to,
            reason=reason,
            problem_category=problem_category,
        )
        # Phase 9: drop the just-finished round's sessions before the DB
        # row's ``iteration_rounds`` is incremented to ``next_round``.
        # Step handlers compute ``round_n = dw["iteration_rounds"] + 1``
        # when they open sessions, which equals ``next_round`` here —
        # i.e. ``next_round`` is the *just-completed* round number, not a
        # forward-looking one. Names mirror that intent.
        completed_round = next_round
        await self._delete_round_sessions(dw["id"], completed_round)
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
        await emit_and_deliver(
            self.db,
            self.webhooks,
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
            await self.workspaces.regenerate_workspace_md(dw["workspace_id"])
        except Exception:
            logger.exception(
                "regenerate_workspace_md failed for %s", dw["workspace_id"]
            )

    async def _retry_step4_same_round_or_escalate(
        self,
        dw: dict[str, Any],
        *,
        reason: str,
        problem_category: ProblemCategory | None,
    ) -> None:
        round_n = dw["iteration_rounds"] + 1
        retry_key = f"step4_retry_round{round_n}"
        gates = await self._gates(dw["id"])
        attempt_raw = gates.get(retry_key, 0)
        attempt = attempt_raw if isinstance(attempt_raw, int) else 0
        if attempt >= 1:
            await self._escalate(
                dw,
                reason=f"{reason} after Step4 retry",
                problem_category=problem_category,
            )
            return

        await self._append_loop_feedback(
            dw["id"],
            for_round=round_n,
            back_to=DevWorkStep.STEP4_DEVELOP,
            reason=reason,
            problem_category=problem_category,
        )
        ws = await self.workspaces.get(dw["workspace_id"])
        if ws is None:
            await self._escalate(
                dw,
                reason="Step4 retry cleanup failed: workspace missing",
                problem_category=problem_category,
            )
            return
        findings_rel = (
            f"devworks/{dw['id']}/artifacts/"
            f"step4-findings-round{round_n}.json"
        )
        try:
            await self.registry.delete(
                workspace_row=ws,
                relative_path=findings_rel,
            )
        except Exception as exc:
            logger.exception(
                "delete stale Step4 findings failed for %s round=%s",
                dw["id"],
                round_n,
            )
            await self._escalate(
                dw,
                reason=f"Step4 retry cleanup failed: {exc}",
                problem_category=problem_category,
            )
            return
        await self._update_gates_field(dw["id"], retry_key, attempt + 1)
        await self._delete_round_sessions(dw["id"], round_n)
        now = self._now()
        category_value = (
            problem_category.value if problem_category else None
        )
        await self.db.execute(
            "UPDATE dev_works SET current_step=?, "
            "last_problem_category=?, updated_at=? WHERE id=?",
            (
                DevWorkStep.STEP4_DEVELOP.value,
                category_value,
                now,
                dw["id"],
            ),
        )
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="dev_work.round_completed",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={
                "round": round_n,
                "back_to": DevWorkStep.STEP4_DEVELOP.value,
                "problem_category": category_value,
                "reason": reason,
                "retry": True,
            },
        )
        try:
            await self.workspaces.regenerate_workspace_md(dw["workspace_id"])
        except Exception:
            logger.exception(
                "regenerate_workspace_md failed for %s", dw["workspace_id"]
            )

    async def _retry_step5_same_round_or_escalate(
        self,
        dw: dict[str, Any],
        *,
        reason: str,
        problem_category: ProblemCategory | None,
    ) -> None:
        round_n = dw["iteration_rounds"] + 1
        retry_key = f"step5_retry_round{round_n}"
        gates = await self._gates(dw["id"])
        attempt_raw = gates.get(retry_key, 0)
        attempt = attempt_raw if isinstance(attempt_raw, int) else 0
        should_retry = attempt < 1
        if should_retry:
            await self._append_loop_feedback(
                dw["id"],
                for_round=round_n,
                back_to=DevWorkStep.STEP5_REVIEW,
                reason=reason,
                problem_category=problem_category,
            )

        ws = await self.workspaces.get(dw["workspace_id"])
        if ws is None:
            await self._escalate(
                dw,
                reason="Step5 retry cleanup failed: workspace missing",
                problem_category=problem_category,
            )
            return
        review_rel = (
            f"devworks/{dw['id']}/artifacts/"
            f"step5-review-round{round_n}.json"
        )
        try:
            await self.registry.delete(
                workspace_row=ws,
                relative_path=review_rel,
            )
        except Exception as exc:
            logger.exception(
                "delete stale Step5 review failed for %s round=%s",
                dw["id"],
                round_n,
            )
            await self._escalate(
                dw,
                reason=f"Step5 retry cleanup failed: {exc}",
                problem_category=problem_category,
            )
            return

        if not should_retry:
            await self._escalate(
                dw,
                reason=f"{reason} after Step5 retry",
                problem_category=problem_category,
            )
            return

        await self._update_gates_field(dw["id"], retry_key, attempt + 1)
        await self._delete_role_session(dw["id"], round_n, "review")
        now = self._now()
        category_value = (
            problem_category.value if problem_category else None
        )
        await self.db.execute(
            "UPDATE dev_works SET current_step=?, "
            "last_problem_category=?, updated_at=? WHERE id=?",
            (
                DevWorkStep.STEP5_REVIEW.value,
                category_value,
                now,
                dw["id"],
            ),
        )
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="dev_work.round_completed",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={
                "round": round_n,
                "back_to": DevWorkStep.STEP5_REVIEW.value,
                "problem_category": category_value,
                "reason": reason,
                "retry": True,
            },
        )
        try:
            await self.workspaces.regenerate_workspace_md(dw["workspace_id"])
        except Exception:
            logger.exception(
                "regenerate_workspace_md failed for %s", dw["workspace_id"]
            )

    async def _escalate(
        self,
        dw: dict[str, Any],
        *,
        reason: str,
        problem_category: ProblemCategory | None,
    ) -> None:
        # Phase 9: terminal cleanup before flipping current_step. Best-effort;
        # the boot-time sweep covers anything that fails here.
        await self._delete_all_sessions(dw["id"])
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
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="dev_work.escalated",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={
                "reason": reason,
                "problem_category": category_value,
                "rounds": dw["iteration_rounds"],
            },
        )
        await emit_and_deliver(
            self.db,
            self.webhooks,
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
            await self.workspaces.regenerate_workspace_md(dw["workspace_id"])
        except Exception:
            logger.exception(
                "regenerate_workspace_md failed for %s", dw["workspace_id"]
            )
