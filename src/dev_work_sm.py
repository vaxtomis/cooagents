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
from src.dev_prompt_composer import MountTableEntry
from src.dev_work_steps import DevWorkStepHandlersMixin
from src.exceptions import BadRequestError, NotFoundError
from src.git_utils import DEVWORK_BRANCH_FMT, ensure_worktree
from src.llm_runner import IdleTimeoutError, ProgressTick
from src.models import (
    REPO_ROLE_PRIMARY_PRIORITY,
    AgentKind,
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

    def _abs_for(self, ws: dict[str, Any], relative_path: str) -> str:
        """Compose an absolute POSIX path under ``<root>/<slug>/`` for LLM use."""
        return (self.workspaces_root / ws["slug"] / relative_path).as_posix()

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

    # ---- Phase 8a host dispatch helpers ----

    async def _pick_host(self, agent: str) -> str:
        from src.agent_hosts.dispatch_decider import choose_host
        from src.models import LOCAL_HOST_ID

        if self.agent_host_repo is None:
            return LOCAL_HOST_ID
        try:
            return await choose_host(self.agent_host_repo, agent)
        except Exception:
            logger.exception("choose_host failed; falling back to local")
            return LOCAL_HOST_ID

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
        agent: str = AgentKind.claude.value,
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

        if not repo_refs:
            raise BadRequestError(
                "repo_refs must contain at least one entry"
            )

        dev_id = self._new_id()
        now = self._now()
        host_id = await self._pick_host(agent)
        dw_short = dev_id.removeprefix("dev-")
        devwork_branch = DEVWORK_BRANCH_FMT.format(
            slug=ws["slug"], dw_short=dw_short
        )

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
                    agent,
                    host_id,
                    None,
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
        await emit_and_deliver(
            self.db,
            self.webhooks,
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
            try:
                _, wt_path = await ensure_worktree(
                    str(bare), branch, wt_path
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

        # Mirror primary's path onto the deprecated dev_works column for
        # back-compat reads. Conditional UPDATE preserves the CAS guard so
        # a re-tick after a partial _s0_init doesn't clobber the row.
        await self.db.execute(
            "UPDATE dev_works SET worktree_path=?, worktree_branch=?, "
            "updated_at=? WHERE id=? AND worktree_path IS NULL",
            (primary_wt_path, primary_branch, self._now(), dw["id"]),
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
        """Wrapper around ``executor.run_once`` with uniform event emission.

        Phase 8a: opens an ``agent_dispatches`` row and routes to the host
        recorded on the dev_works row (defaults to ``"local"``).
        """
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

    async def _render_previous_review_markdown(self, dev_id: str) -> str:
        """Render previous-round reviewer issues + hints into a markdown blob.

        Returns an empty string when no prior review exists. Pure (DB-only).
        Phase 5: a forward-looking ``## 下一轮提示`` H2 is appended whenever
        the previous review persisted a non-empty ``next_round_hints`` array.
        Output stays byte-identical to Phase 4 when hints are empty/NULL.
        """
        row = await self.db.fetchone(
            "SELECT score, problem_category, issues_json, "
            "next_round_hints_json FROM reviews "
            "WHERE dev_work_id=? ORDER BY round DESC LIMIT 1",
            (dev_id,),
        )
        if row is None:
            return ""
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
        body = await self._render_previous_review_markdown(dw["id"])
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
                None,
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
