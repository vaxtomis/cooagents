"""DesignWork state machine (Phase 3).

States (PRD L197-226):
    INIT -> MODE_BRANCH -> PRE_VALIDATE -> PROMPT_COMPOSE -> LLM_GENERATE
         -> [MOCKUP] -> POST_VALIDATE -> PERSIST -> COMPLETED
    POST_VALIDATE fail + loop < max -> back to PROMPT_COMPOSE
    POST_VALIDATE fail + loop == max -> ESCALATED
    mode=optimize -> NotImplementedError at MODE_BRANCH (v1 scope)

Drives asynchronously after ``create()``: the caller schedules
``asyncio.create_task(self.run_to_completion(id))``. Each state handler is
idempotent so an interrupted task can be resumed by ``tick(id)``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.design_prompt_composer import PromptInputs, compose_prompt
from src.design_validator import validate_design_markdown
from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.mockup_renderer import MockupSpec, PathMockupRenderer
from src.models import DesignWorkMode, DesignWorkState, RepoRef
from src.semver import next_version
from src.storage.registry import WorkspaceFileRegistry
from src.workspace_events import emit_and_deliver

logger = logging.getLogger(__name__)


class DesignWorkStateMachine:
    def __init__(
        self,
        db,
        workspaces,        # WorkspaceManager
        design_docs,       # DesignDocManager
        executor,          # object with async run_once(agent, worktree, timeout, task_file=?, prompt=?)
        config,            # Settings
        registry: WorkspaceFileRegistry,
        mockup_renderer=None,  # MockupRenderer; defaults to PathMockupRenderer (U6)
        webhooks=None,     # WebhookNotifier (optional)
        agent_host_repo=None,      # Phase 8a: AgentHostRepo (None ⇒ host_id="local")
        agent_dispatch_repo=None,  # Phase 8a: AgentDispatchRepo (None ⇒ no lifecycle row)
    ):
        self.db = db
        self.workspaces = workspaces
        self.design_docs = design_docs
        self.executor = executor
        self.config = config
        self.registry = registry
        self.mockup_renderer = mockup_renderer or PathMockupRenderer()
        self.webhooks = webhooks
        self.agent_host_repo = agent_host_repo
        self.agent_dispatch_repo = agent_dispatch_repo
        # Kept per-instance so tests starting multiple SMs don't share state.
        # Single-writer invariant: only one driver task per DesignWork id is
        # ever scheduled; the read-modify-write gates/loop updates rely on it.
        self._running: dict[str, asyncio.Task] = {}

    def schedule_driver(self, dw_id: str) -> asyncio.Task:
        """Fire a background ``run_to_completion`` task and track it.

        Logs any exception that escapes the SM (``NotImplementedError`` from
        the stubbed optimize branch is expected) and clears ``_running`` so
        the dict doesn't grow unbounded over the life of the process.
        """
        existing = self._running.get(dw_id)
        if existing is not None:
            if not existing.done():
                return existing
            self._running.pop(dw_id, None)

        task = asyncio.create_task(self.run_to_completion(dw_id))

        def _on_done(t: asyncio.Task) -> None:
            if self._running.get(dw_id) is t:
                self._running.pop(dw_id, None)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None and not isinstance(exc, NotImplementedError):
                logger.exception(
                    "design_work %s driver task failed", dw_id, exc_info=exc
                )

        task.add_done_callback(_on_done)
        self._running[dw_id] = task
        return task

    # ---- helpers ----

    def is_running(self, dw_id: str) -> bool:
        task = self._running.get(dw_id)
        if task is None:
            return False
        if task.done():
            self._running.pop(dw_id, None)
            return False
        return True

    @staticmethod
    def _new_id() -> str:
        return f"desw-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def _get(self, dw_id: str) -> dict | None:
        return await self.db.fetchone(
            "SELECT * FROM design_works WHERE id=?", (dw_id,)
        )

    async def _open_dispatch(
        self, *, host_id: str, workspace_id: str,
        correlation_id: str, correlation_kind: str,
    ) -> str | None:
        """Insert + mark_running on agent_dispatches (Phase 8a). Returns id or None.

        Failures must NOT block execution: dispatch is observability, not
        the source of truth.
        """
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

    async def _pick_host(self, agent: str) -> str:
        """Phase 8a: choose an agent host id for this DesignWork.

        Defaults to ``"local"`` when no host repo is configured (preserves
        Phase 7b call sites that don't construct one). Errors from the
        decider must not block creation — falls back to ``"local"``.
        """
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
        preferred = getattr(self.config, "preferred_design_agent", None)
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
            logger.exception("resolve DesignWork agent failed; using fallback")
            for candidate in (requested, preferred, "codex", "claude"):
                if candidate in {"codex", "claude"}:
                    return candidate
            return "codex"

    async def _transition(self, dw: dict, frm: DesignWorkState, to: DesignWorkState) -> None:
        now = self._now()
        rc = await self.db.execute_rowcount(
            "UPDATE design_works SET current_state=?, updated_at=? "
            "WHERE id=? AND current_state=?",
            (to.value, now, dw["id"], frm.value),
        )
        if rc == 0:
            logger.warning(
                "design_work %s already past %s", dw["id"], frm.value
            )
        # Keep workspace.md in sync with every state change.
        try:
            await self.workspaces.regenerate_workspace_md(dw["workspace_id"])
        except Exception:
            logger.exception(
                "regenerate_workspace_md failed for %s", dw["workspace_id"]
            )

    async def _update_gates_field(self, dw_id: str, key: str, value) -> None:
        row = await self._get(dw_id)
        if row is None:
            return
        gates = _decode_gates(row.get("gates_json"))
        gates[key] = value
        await self.db.execute(
            "UPDATE design_works SET gates_json=?, updated_at=? WHERE id=?",
            (json.dumps(gates, ensure_ascii=False), self._now(), dw_id),
        )

    # ---- public API ----

    async def create(
        self,
        *,
        workspace_id: str,
        title: str,
        sub_slug: str,
        user_input: str,
        mode: DesignWorkMode,
        parent_version: str | None,
        needs_frontend_mockup: bool,
        agent: str | None,
        rubric_threshold: int | None = None,
        repo_refs: list[tuple[RepoRef, str | None]] | None = None,
    ) -> dict:
        """Create a DesignWork plus its ``design_work_repos`` rows atomically.

        ``repo_refs`` is the validated tuple list returned by
        ``routes._repo_refs_validation.validate_design_repo_refs`` — each
        entry is ``(RepoRef, head_sha_or_none)``. Empty / None means a
        pure-doc DesignWork (no repo binding).
        """
        ws = await self.workspaces.get(workspace_id)
        if ws is None:
            raise NotFoundError(f"workspace {workspace_id!r} not found")
        if ws["status"] != "active":
            raise BadRequestError(
                f"workspace {workspace_id!r} is archived; cannot create DesignWork"
            )

        wid = self._new_id()
        now = self._now()
        input_rel = f"designs/.drafts/{wid}-input.md"
        await self.registry.put_markdown(
            workspace_row=ws,
            relative_path=input_rel,
            text=user_input,
            kind="design_input",
        )

        # U7: persist title / sub_slug / version / output_path on the row.
        version = (
            next_version(parent_version, "new") if mode == DesignWorkMode.new else None
        )
        output_rel = (
            f"designs/DES-{sub_slug}-{version}.md" if version else None
        )
        gates_payload = (
            {"rubric_threshold_override": rubric_threshold}
            if rubric_threshold is not None
            else None
        )

        resolved_agent = await self._resolve_agent(agent)
        host_id = await self._pick_host(resolved_agent)
        async with self.db.transaction():
            await self.db.execute(
                """INSERT INTO design_works
                   (id, workspace_id, mode, parent_version, needs_frontend_mockup,
                    current_state, loop, missing_sections_json, agent,
                    agent_host_id, user_input_path, title, sub_slug, version,
                    output_path, gates_json, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    wid,
                    workspace_id,
                    mode.value,
                    parent_version,
                    1 if needs_frontend_mockup else 0,
                    DesignWorkState.INIT.value,
                    0,
                    None,
                    resolved_agent,
                    host_id,
                    input_rel,
                    title,
                    sub_slug,
                    version,
                    output_rel,
                    json.dumps(gates_payload) if gates_payload else None,
                    now,
                    now,
                ),
            )
            for ref, rev in repo_refs or []:
                await self.db.execute(
                    """INSERT INTO design_work_repos(
                           design_work_id, repo_id, branch, rev, created_at)
                       VALUES(?,?,?,?,?)""",
                    (wid, ref.repo_id, ref.base_branch, rev, now),
                )
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="design_work.started",
            workspace_id=workspace_id,
            correlation_id=wid,
            payload={
                "mode": mode.value,
                "title": title,
                "sub_slug": sub_slug,
                "agent": resolved_agent,
                "agent_host_id": host_id,
            },
        )
        try:
            await self.workspaces.regenerate_workspace_md(workspace_id)
        except Exception:
            logger.exception("initial regenerate_workspace_md failed for %s", workspace_id)
        return await self._get(wid)

    async def tick(self, dw_id: str, *, from_driver: bool = False) -> dict:
        """Advance the DesignWork one step; idempotent."""
        dw = await self._get(dw_id)
        if dw is None:
            raise NotFoundError(f"design_work {dw_id!r} not found")
        if not from_driver and self.is_running(dw_id):
            raise ConflictError(
                f"design_work {dw_id!r} is already being advanced",
                current_stage=dw["current_state"],
            )

        state = DesignWorkState(dw["current_state"])
        handler = {
            DesignWorkState.INIT: self._d0_init,
            DesignWorkState.MODE_BRANCH: self._d1_mode_branch,
            DesignWorkState.PRE_VALIDATE: self._d2_pre_validate,
            DesignWorkState.PROMPT_COMPOSE: self._d3_prompt_compose,
            DesignWorkState.LLM_GENERATE: self._d4_llm_generate,
            DesignWorkState.MOCKUP: self._d4_5_mockup,
            DesignWorkState.POST_VALIDATE: self._d5_post_validate,
            DesignWorkState.PERSIST: self._d6_persist,
            DesignWorkState.COMPLETED: self._noop,
            DesignWorkState.ESCALATED: self._noop,
            DesignWorkState.CANCELLED: self._noop,
        }.get(state)
        if handler is None:
            raise BadRequestError(f"no handler for state {state!r}")
        await handler(dw)
        return await self._get(dw_id)

    async def run_to_completion(self, dw_id: str) -> dict:
        """Drive ``tick()`` until a terminal state."""
        terminal = {
            DesignWorkState.COMPLETED,
            DesignWorkState.ESCALATED,
            DesignWorkState.CANCELLED,
        }
        while True:
            dw = await self.tick(dw_id, from_driver=True)
            if DesignWorkState(dw["current_state"]) in terminal:
                return dw

    async def cancel(self, dw_id: str) -> None:
        now = self._now()
        rowcount = await self.db.execute_rowcount(
            "UPDATE design_works SET current_state=?, updated_at=? "
            "WHERE id=? AND current_state NOT IN (?, ?, ?)",
            (
                DesignWorkState.CANCELLED.value,
                now,
                dw_id,
                DesignWorkState.COMPLETED.value,
                DesignWorkState.ESCALATED.value,
                DesignWorkState.CANCELLED.value,
            ),
        )
        if rowcount == 0:
            raise NotFoundError(
                f"design_work {dw_id!r} not found or already terminal"
            )
        dw = await self._get(dw_id)
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="design_work.cancelled",
            workspace_id=dw["workspace_id"],
            correlation_id=dw_id,
        )
        task = self._running.pop(dw_id, None)
        if task is not None:
            task.cancel()

    # ---- state handlers ----
    #
    # Per U7, no in-memory _context map: everything the handlers need is
    # either on the design_works row (title / sub_slug / version /
    # output_path) or in gates_json (transient per-loop fields such as
    # last_prompt_path, rubric_threshold_override).

    async def _d0_init(self, dw: dict) -> None:
        await self._transition(
            dw, DesignWorkState.INIT, DesignWorkState.MODE_BRANCH
        )

    async def _d1_mode_branch(self, dw: dict) -> None:
        mode = DesignWorkMode(dw["mode"])
        if mode == DesignWorkMode.optimize:
            if not self.config.design.allow_optimize_mode:
                await self._escalate(dw, reason="mode=optimize not supported in v1")
                raise NotImplementedError("mode=optimize is v1 stubbed")
            # optimize fully implemented in a future phase.
        if mode == DesignWorkMode.new and dw["parent_version"] is not None:
            await self._escalate(
                dw, reason="mode=new must not supply parent_version"
            )
            raise BadRequestError("mode=new but parent_version is set")
        await self._transition(
            dw, DesignWorkState.MODE_BRANCH, DesignWorkState.PRE_VALIDATE
        )

    async def _d2_pre_validate(self, dw: dict) -> None:
        # mode=new: user_input must be substantive. Structural checks on the
        # LLM output happen at D5.
        ws = await self.workspaces.get(dw["workspace_id"])
        try:
            text = await self.registry.read_text(
                workspace_slug=ws["slug"],
                relative_path=dw["user_input_path"],
            )
        except NotFoundError:
            await self._escalate(dw, reason="user_input file missing")
            return
        if len(text.strip()) < 10:
            await self._escalate(dw, reason="user_input shorter than 10 chars")
            return
        await self._transition(
            dw, DesignWorkState.PRE_VALIDATE, DesignWorkState.PROMPT_COMPOSE
        )

    async def _d3_prompt_compose(self, dw: dict) -> None:
        ws = await self.workspaces.get(dw["workspace_id"])
        missing = _decode_missing(dw.get("missing_sections_json"))
        title = dw["title"]
        version = dw["version"]
        output_rel = dw["output_path"]

        user_input = await self.registry.read_text(
            workspace_slug=ws["slug"], relative_path=dw["user_input_path"],
        )
        # LLM receives an absolute output path so it can `Write` without
        # guessing a cwd; relative paths are workspace-internal only.
        output_abs = (
            self._abs_for(ws, output_rel) if output_rel else None
        )
        prompt = compose_prompt(
            PromptInputs(
                workspace_slug=ws["slug"],
                title=title,
                version=version,
                user_input=user_input,
                needs_frontend_mockup=bool(dw["needs_frontend_mockup"]),
                output_path=output_abs,
                parent_version=dw["parent_version"],
                missing_sections=missing,
            )
        )

        prompt_rel = f"designs/.drafts/{dw['id']}-prompt-loop{dw['loop']}.md"
        await self.registry.put_markdown(
            workspace_row=ws, relative_path=prompt_rel,
            text=prompt, kind="prompt",
        )
        await self._update_gates_field(dw["id"], "last_prompt_path", prompt_rel)
        await self._transition(
            dw, DesignWorkState.PROMPT_COMPOSE, DesignWorkState.LLM_GENERATE
        )

    def _abs_for(self, ws: dict, relative_path: str) -> str:
        """Compose an absolute POSIX-ish path for LLM-embedded prompts.

        Uses forward slashes (via ``Path.as_posix``) so the LLM sees a
        predictable separator regardless of host OS.
        """
        root = Path(self.workspaces.workspaces_root)
        return (root / ws["slug"] / relative_path).as_posix()

    async def _d4_llm_generate(self, dw: dict) -> None:
        ws = await self.workspaces.get(dw["workspace_id"])
        gates = _decode_gates(dw.get("gates_json"))
        prompt_rel = gates.get("last_prompt_path")
        ref = (
            await self.registry.stat(
                workspace_slug=ws["slug"], relative_path=prompt_rel,
            )
            if prompt_rel
            else None
        )
        if not prompt_rel or ref is None:
            # Force a D3 redo if the prompt file is gone (server restart
            # before the prompt hit disk; or manual cleanup).
            await self._transition(
                dw, DesignWorkState.LLM_GENERATE, DesignWorkState.PROMPT_COMPOSE
            )
            return
        output_rel = dw["output_path"]
        output_ref = (
            await self.registry.stat(
                workspace_slug=ws["slug"], relative_path=output_rel,
            )
            if output_rel
            else None
        )
        if output_ref is not None and output_ref.mtime_ns >= ref.mtime_ns:
            await emit_and_deliver(
                self.db,
                self.webhooks,
                event_name="design_work.llm_completed",
                workspace_id=dw["workspace_id"],
                correlation_id=dw["id"],
                payload={
                    "loop": dw["loop"],
                    "rc": 0,
                    "stdout_len": 0,
                    "recovered": True,
                },
            )
            await self._advance_after_llm_success(dw)
            return
        timeout = self.config.design.execution_timeout  # U5
        worktree = self._abs_for(ws, "designs")
        prompt_abs = self._abs_for(ws, prompt_rel)
        host_id = dw.get("agent_host_id") or "local"
        ad_id = await self._open_dispatch(
            host_id=host_id, workspace_id=dw["workspace_id"],
            correlation_id=dw["id"], correlation_kind="design_work",
        )
        try:
            stdout, rc = await self.executor.run_once(
                dw["agent"], worktree, timeout, task_file=prompt_abs,
                host_id=host_id,
                workspace_id=dw["workspace_id"],
                correlation_id=dw["id"],
            )
            dispatch_state = "succeeded" if rc == 0 else "failed"
        except asyncio.TimeoutError:
            logger.warning("design_work %s LLM call timed out", dw["id"])
            rc, stdout, dispatch_state = 124, "", "timeout"
        except Exception as exc:
            logger.exception("design_work %s LLM call failed: %s", dw["id"], exc)
            rc, stdout, dispatch_state = 1, "", "failed"
        await self._close_dispatch(ad_id, state=dispatch_state, exit_code=rc)
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="design_work.llm_completed",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={"loop": dw["loop"], "rc": rc, "stdout_len": len(stdout or "")},
        )
        if rc != 0:
            # Executor reported failure; skip MOCKUP/POST_VALIDATE since
            # there's no useful artifact to inspect. D5 would just discover
            # the missing file a moment later, wasting a round's work.
            await self._loop_or_escalate(
                dw, missing=[], reason=f"LLM call failed rc={rc}"
            )
            return
        await self._advance_after_llm_success(dw)

    async def _advance_after_llm_success(self, dw: dict) -> None:
        if bool(dw["needs_frontend_mockup"]):
            await self._transition(
                dw, DesignWorkState.LLM_GENERATE, DesignWorkState.MOCKUP
            )
        else:
            await self._transition(
                dw, DesignWorkState.LLM_GENERATE, DesignWorkState.POST_VALIDATE
            )

    async def _d4_5_mockup(self, dw: dict) -> None:
        # v1 (U6): PathMockupRenderer is a pass-through; it doesn't generate
        # images. The call exists so Phase 4+ can swap in pencil/stitch MCP
        # by replacing the renderer — no state machine change required.
        ws = await self.workspaces.get(dw["workspace_id"])
        output_rel = dw["output_path"]
        link = None
        page_structure_md = ""
        text = None
        if output_rel:
            try:
                text = await self.registry.read_text(
                    workspace_slug=ws["slug"], relative_path=output_rel,
                )
            except NotFoundError:
                text = None
        if text is not None:
            # Accept optional list markers / leading whitespace in front of
            # the marker — LLMs commonly emit `- 设计图链接或路径: …`.
            m_link = re.search(
                r"^\s*[-*]?\s*设计图链接或路径\s*[:：]\s*(.+?)\s*$",
                text,
                flags=re.MULTILINE,
            )
            if m_link:
                link = m_link.group(1).strip()
            m = re.search(
                r"## 页面结构\s*\n(.*?)(?=\n## |\Z)", text, flags=re.DOTALL
            )
            page_structure_md = m.group(1).strip() if m else ""

        result = await self.mockup_renderer.render(
            MockupSpec(
                workspace_slug=ws["slug"],
                design_sub_slug=dw["sub_slug"],
                version=dw["version"],
                page_structure_md=page_structure_md,
                user_provided_link=link,
            )
        )
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="design_work.mockup_recorded",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={"link": result.link, "note": result.note},
        )
        await self._transition(
            dw, DesignWorkState.MOCKUP, DesignWorkState.POST_VALIDATE
        )

    async def _d5_post_validate(self, dw: dict) -> None:
        ws = await self.workspaces.get(dw["workspace_id"])
        output_rel = dw["output_path"]
        ref = (
            await self.registry.stat(
                workspace_slug=ws["slug"], relative_path=output_rel,
            )
            if output_rel
            else None
        )
        if not output_rel or ref is None:
            missing_all = [
                *self.config.design.required_sections,
                *(
                    self.config.design.mockup_sections
                    if bool(dw["needs_frontend_mockup"])
                    else []
                ),
            ]
            await self._loop_or_escalate(
                dw, missing_all, reason="output file missing"
            )
            return

        text = await self.registry.read_text(
            workspace_slug=ws["slug"], relative_path=output_rel,
        )
        report = validate_design_markdown(
            text,
            required_sections=self.config.design.required_sections,
            mockup_sections=self.config.design.mockup_sections,
        )
        if report.ok:
            await self._set_missing(dw["id"], None)
            await self._transition(
                dw, DesignWorkState.POST_VALIDATE, DesignWorkState.PERSIST
            )
            return
        await self._loop_or_escalate(
            dw, list(report.all_missing()), reason="post-validate failed"
        )

    async def _d6_persist(self, dw: dict) -> None:
        ws = await self.workspaces.get(dw["workspace_id"])
        output_rel = dw["output_path"]
        slug = dw["sub_slug"]
        version = dw["version"]
        text = await self.registry.read_text(
            workspace_slug=ws["slug"], relative_path=output_rel,
        )

        # U2: rubric priority — API override > LLM front-matter > default.
        report = validate_design_markdown(
            text,
            required_sections=self.config.design.required_sections,
            mockup_sections=self.config.design.mockup_sections,
        )
        gates = _decode_gates(dw.get("gates_json"))
        override = gates.get("rubric_threshold_override")
        if isinstance(override, int) and 1 <= override <= 100:
            rubric = override
        else:
            try:
                rubric = int(report.front_matter.get("rubric_threshold", ""))
                if not 1 <= rubric <= 100:
                    raise ValueError
            except ValueError:
                rubric = self.config.scoring.default_threshold

        row = await self.design_docs.persist(
            workspace_row=ws,
            slug=slug,
            version=version,
            markdown=text,
            parent_version=dw["parent_version"],
            needs_frontend_mockup=bool(dw["needs_frontend_mockup"]),
            rubric_threshold=rubric,
        )
        await self.design_docs.publish(row["id"], dw["id"])
        now = self._now()
        await self.db.execute(
            "UPDATE design_works SET current_state=?, updated_at=? WHERE id=?",
            (DesignWorkState.COMPLETED.value, now, dw["id"]),
        )
        self._running.pop(dw["id"], None)
        try:
            await self.workspaces.regenerate_workspace_md(dw["workspace_id"])
        except Exception:
            logger.exception(
                "regenerate_workspace_md failed for %s", dw["workspace_id"]
            )
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="design_work.completed",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={
                "design_doc_id": row["id"],
                "version": version,
                "slug": slug,
            },
        )
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="design_doc.published",
            workspace_id=dw["workspace_id"],
            correlation_id=row["id"],
            payload={
                "design_work_id": dw["id"],
                "version": version,
                "slug": slug,
            },
        )

    async def _noop(self, dw: dict) -> None:
        return

    # ---- loop bookkeeping ----

    async def _set_missing(self, dw_id: str, missing: list[str] | None) -> None:
        blob = json.dumps(missing, ensure_ascii=False) if missing else None
        await self.db.execute(
            "UPDATE design_works SET missing_sections_json=?, updated_at=? WHERE id=?",
            (blob, self._now(), dw_id),
        )

    async def _loop_or_escalate(
        self, dw: dict, missing: list[str], reason: str
    ) -> None:
        next_loop = dw["loop"] + 1
        if next_loop > self.config.design.max_loops:
            await self._escalate(dw, reason=reason, missing=missing)
            return
        await self.db.execute(
            "UPDATE design_works SET loop=?, missing_sections_json=?, "
            "current_state=?, updated_at=? WHERE id=?",
            (
                next_loop,
                json.dumps(missing, ensure_ascii=False),
                DesignWorkState.PROMPT_COMPOSE.value,
                self._now(),
                dw["id"],
            ),
        )
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="design_work.round_completed",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={"loop": next_loop, "missing": missing},
        )

    async def _escalate(
        self, dw: dict, *, reason: str, missing: list[str] | None = None
    ) -> None:
        now = self._now()
        await self.db.execute(
            "UPDATE design_works SET current_state=?, escalated_at=?, "
            "escalation_reason=?, missing_sections_json=?, "
            "updated_at=? WHERE id=?",
            (
                DesignWorkState.ESCALATED.value,
                now,
                reason,
                json.dumps(missing, ensure_ascii=False) if missing else None,
                now,
                dw["id"],
            ),
        )
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="design_work.escalated",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={
                "subject": "design_work",
                "reason": reason,
                "missing_sections": missing or [],
            },
        )


def _decode_missing(blob: str | None) -> list[str]:
    if not blob:
        return []
    try:
        data = json.loads(blob)
        return list(data) if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _decode_gates(blob: str | None) -> dict:
    if not blob:
        return {}
    try:
        data = json.loads(blob)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}
