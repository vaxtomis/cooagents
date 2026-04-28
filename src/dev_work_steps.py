"""DevWork Step2-Step5 handlers extracted as a mixin.

Phase 3 refactor: every Step2–Step5 file lives in the workspace under
``<ws>/<slug>/devworks/<dev_work_id>/`` and is written through the registry.
No ``.cooagents/`` dir is created in the git worktree; the LLM writes its
outputs to absolute paths composed via ``self._abs_for(ws, relative)`` and
the Python side re-registers them via ``registry.index_existing``.

Split out of :mod:`src.dev_work_sm` so the SM orchestrator stays under the
800-line project cap. Each handler is still a coroutine that takes a DB row
dict and drives one tick.

The mixin expects the concrete class to provide:
  * ``self.db`` / ``self.workspaces`` / ``self.iteration_notes`` / ``self.registry``
  * ``self.config.devwork`` — step timeouts + max_rounds
  * ``self._now()`` / ``self._run_llm`` / ``self._gates`` /
    ``self._update_gates_field`` / ``self._transition`` /
    ``self._record_review`` / ``self._resolve_rubric_threshold`` /
    ``self._load_mount_table_entries`` /
    ``self._escalate`` / ``self._loop_or_escalate`` / ``self._abs_for``

Do not import :mod:`src.dev_work_sm` here — avoids a circular import.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.dev_prompt_composer import (
    IterationHeaderInputs,
    Step2Inputs,
    Step3Inputs,
    Step4Inputs,
    Step5Inputs,
    compose_iteration_header,
    compose_step2,
    compose_step3,
    compose_step4,
    compose_step5,
    extract_rubric_section,
)
from src.exceptions import BadRequestError, NotFoundError
from src.models import DevWorkStep, ProblemCategory
from src.reviewer import ReviewOutcome, parse_review_output
from src.workspace_events import emit_and_deliver

logger = logging.getLogger(__name__)

_REQUIRED_H2 = ("本轮目标", "开发计划", "用例清单")


class DevWorkStepHandlersMixin:
    """Mixin providing Step2–Step5 handlers for DevWorkStateMachine."""

    async def _s2_iteration(self, dw: dict[str, Any]) -> None:
        """Step2 (F2=B): SM writes header -> LLM appends three H2 sections."""
        round_n = dw["iteration_rounds"] + 1
        ws = await self.workspaces.get(dw["workspace_id"])
        dd = await self.db.fetchone(
            "SELECT path FROM design_docs WHERE id=?", (dw["design_doc_id"],)
        )
        if dd is None:
            await self._escalate(
                dw,
                reason="design_doc row missing at Step2",
                problem_category=ProblemCategory.design_hollow,
            )
            return
        # Path-based: the LLM Reads the design doc itself; we no longer
        # pre-load the bytes here. A missing/unreadable file will surface
        # via the LLM's own Read failure instead of a Python exception.
        design_doc_abs = self._abs_for(ws, dd["path"])

        # 1) Write the SM-owned header (front-matter + H1) so the LLM can only
        #    append; this locks those lines against prompt-injection rewrites.
        note_rel = self.iteration_notes.relative_for(dw["id"], round_n)
        note_abs = self._abs_for(ws, note_rel)
        header = compose_iteration_header(
            IterationHeaderInputs(
                dev_work_id=dw["id"],
                design_doc_path=dd["path"],
                round=round_n,
                created_at=self._now(),
            )
        )
        await self.registry.put_markdown(
            workspace_row=ws, relative_path=note_rel,
            text=header, kind="iteration_note",
        )

        # 2) Materialize previous-round review markdown to a workspace file
        #    (None for round 1 / no prior review). Path-based: the LLM
        #    Reads the file rather than receiving the embedded body.
        prev_review_rel = await self._write_previous_review_for_round(
            dw, ws, round_n,
        )
        prev_review_abs = (
            self._abs_for(ws, prev_review_rel) if prev_review_rel else None
        )

        # 3) Compose Step2 prompt and run the LLM.
        prompt_text = compose_step2(
            Step2Inputs(
                dev_work_id=dw["id"],
                round=round_n,
                design_doc_path=design_doc_abs,
                user_prompt=dw["prompt"],
                previous_review_path=prev_review_abs,
                output_path=note_abs,
            )
        )
        prompt_rel = f"devworks/{dw['id']}/prompts/step2-round{round_n}.md"
        await self.registry.put_markdown(
            workspace_row=ws, relative_path=prompt_rel,
            text=prompt_text, kind="prompt",
        )
        prompt_abs = self._abs_for(ws, prompt_rel)

        worktree_cwd = dw["worktree_path"] or self._abs_for(
            ws, f"devworks/{dw['id']}"
        )
        rc, _stdout = await self._run_llm(
            dw,
            agent=dw["agent"],
            worktree=worktree_cwd,
            timeout=self.config.devwork.step2_timeout,
            task_file=prompt_abs,
            step_tag="STEP2_ITERATION",
            round_n=round_n,
        )
        if rc != 0:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP2_ITERATION,
                reason=f"Step2 LLM rc={rc}",
                problem_category=ProblemCategory.req_gap,
            )
            return

        # 3) Validate the produced markdown: three H2s required.
        try:
            body = await self.registry.read_text(
                workspace_slug=ws["slug"], relative_path=note_rel,
            )
        except NotFoundError as exc:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP2_ITERATION,
                reason=f"Step2 output unreadable: {exc}",
                problem_category=ProblemCategory.req_gap,
            )
            return
        found = set(re.findall(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE))
        missing = [h for h in _REQUIRED_H2 if h not in found]
        if missing:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP2_ITERATION,
                reason=f"Step2 missing H2: {missing}",
                problem_category=ProblemCategory.req_gap,
            )
            return

        # 4) Re-register the note so content_hash/size/mtime track the LLM's
        #    appended body, and INSERT the dev_iteration_notes row
        #    (UNIQUE(dev_work_id, round) is invariant-checked).
        try:
            await self.registry.index_existing(
                workspace_row=ws, relative_path=note_rel,
                kind="iteration_note",
            )
            await self.iteration_notes.record_round(
                workspace_row=ws,
                dev_work_id=dw["id"],
                round_n=round_n,
                markdown_path=note_rel,
            )
        except Exception as exc:
            logger.exception(
                "dev_work %s record_round failed (round=%s)",
                dw["id"],
                round_n,
            )
            await self._escalate(
                dw,
                reason=f"iteration_note INSERT failed: {exc}",
                problem_category=None,
            )
            return
        await self._transition(
            dw, DevWorkStep.STEP2_ITERATION, DevWorkStep.STEP3_CONTEXT
        )

    async def _s3_context(self, dw: dict[str, Any]) -> None:
        """Prompt-side retrieval; retries once in-place before routing back."""
        round_n = dw["iteration_rounds"] + 1
        ws = await self.workspaces.get(dw["workspace_id"])
        note = await self.iteration_notes.latest_for(dw["id"])
        if note is None:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP2_ITERATION,
                reason="Step3 found no iteration note",
                problem_category=ProblemCategory.req_gap,
            )
            return
        dd = await self.db.fetchone(
            "SELECT path FROM design_docs WHERE id=?", (dw["design_doc_id"],)
        )
        ctx_rel = f"devworks/{dw['id']}/context/ctx-round-{round_n}.md"
        ctx_abs = self._abs_for(ws, ctx_rel)
        prompt = compose_step3(
            Step3Inputs(
                worktree_path=dw["worktree_path"],
                design_doc_path=self._abs_for(ws, dd["path"]),
                iteration_note_path=self._abs_for(ws, note["markdown_path"]),
                output_path=ctx_abs,
            )
        )
        prompt_rel = f"devworks/{dw['id']}/prompts/step3-round{round_n}.md"
        await self.registry.put_markdown(
            workspace_row=ws, relative_path=prompt_rel,
            text=prompt, kind="prompt",
        )

        retry_key = f"step3_retry_round{round_n}"
        gates = await self._gates(dw["id"])
        attempt = int(gates.get(retry_key, 0))

        rc, _stdout = await self._run_llm(
            dw,
            agent=dw["agent"],
            worktree=dw["worktree_path"],
            timeout=self.config.devwork.step3_timeout,
            task_file=self._abs_for(ws, prompt_rel),
            step_tag="STEP3_CONTEXT",
            round_n=round_n,
        )

        if rc == 0:
            try:
                await self.registry.index_existing(
                    workspace_row=ws, relative_path=ctx_rel, kind="context",
                )
            except NotFoundError:
                # LLM returned 0 but never wrote the context — treat as failure.
                pass
            else:
                await self._transition(
                    dw, DevWorkStep.STEP3_CONTEXT, DevWorkStep.STEP4_DEVELOP
                )
                return

        if attempt < 1:
            await self._update_gates_field(dw["id"], retry_key, attempt + 1)
            return

        await self._loop_or_escalate(
            dw,
            back_to=DevWorkStep.STEP2_ITERATION,
            reason=f"Step3 failed twice (rc={rc})",
            problem_category=ProblemCategory.req_gap,
        )

    async def _s4_develop(self, dw: dict[str, Any]) -> None:
        """Implement + self-review once; parse findings JSON."""
        round_n = dw["iteration_rounds"] + 1
        ws = await self.workspaces.get(dw["workspace_id"])
        note = await self.iteration_notes.latest_for(dw["id"])
        if note is None:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP2_ITERATION,
                reason="Step4 found no iteration note",
                problem_category=ProblemCategory.req_gap,
            )
            return

        ctx_rel = f"devworks/{dw['id']}/context/ctx-round-{round_n}.md"
        findings_rel = (
            f"devworks/{dw['id']}/artifacts/step4-findings-round{round_n}.json"
        )

        prompt = compose_step4(
            Step4Inputs(
                worktree_path=dw["worktree_path"],
                iteration_note_path=self._abs_for(ws, note["markdown_path"]),
                context_path=self._abs_for(ws, ctx_rel),
                findings_output_path=self._abs_for(ws, findings_rel),
            )
        )
        prompt_rel = f"devworks/{dw['id']}/prompts/step4-round{round_n}.md"
        await self.registry.put_markdown(
            workspace_row=ws, relative_path=prompt_rel,
            text=prompt, kind="prompt",
        )

        rc, _stdout = await self._run_llm(
            dw,
            agent=dw["agent"],
            worktree=dw["worktree_path"],
            timeout=self.config.devwork.step4_timeout,
            task_file=self._abs_for(ws, prompt_rel),
            step_tag="STEP4_DEVELOP",
            round_n=round_n,
        )

        if rc != 0:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP4_DEVELOP,
                reason=f"Step4 failed (rc={rc})",
                problem_category=ProblemCategory.impl_gap,
            )
            return

        try:
            await self.registry.index_existing(
                workspace_row=ws, relative_path=findings_rel, kind="artifact",
            )
        except NotFoundError:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP4_DEVELOP,
                reason="Step4 findings JSON missing",
                problem_category=ProblemCategory.impl_gap,
            )
            return

        try:
            findings_raw = await self.registry.read_text(
                workspace_slug=ws["slug"], relative_path=findings_rel,
            )
            findings = json.loads(findings_raw)
        except (NotFoundError, json.JSONDecodeError) as exc:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP4_DEVELOP,
                reason=f"Step4 findings JSON invalid: {exc}",
                problem_category=ProblemCategory.impl_gap,
            )
            return
        await self._update_gates_field(
            dw["id"], f"step4_findings_round{round_n}", findings
        )
        await self._transition(
            dw, DevWorkStep.STEP4_DEVELOP, DevWorkStep.STEP5_REVIEW
        )

    async def _s5_review(self, dw: dict[str, Any]) -> None:
        """Rubric scoring; in-place retry once on parse failure.

        Phase 8: prompt is path-based — the LLM Reads the design doc /
        iteration note / Step4 findings itself, and Bashes ``git diff HEAD``
        in the primary worktree. The SM only does the rubric pre-flight
        (so an empty rubric escalates without spinning up a Step5 round).
        """
        round_n = dw["iteration_rounds"] + 1
        rubric_threshold = await self._resolve_rubric_threshold(dw)

        ws = await self.workspaces.get(dw["workspace_id"])
        dd = await self.db.fetchone(
            "SELECT * FROM design_docs WHERE id=?", (dw["design_doc_id"],)
        )
        try:
            design_text = await self.registry.read_text(
                workspace_slug=ws["slug"], relative_path=dd["path"],
            )
        except NotFoundError as exc:
            await self._escalate(
                dw,
                reason=f"design_doc unreadable at Step5: {exc}",
                problem_category=ProblemCategory.design_hollow,
            )
            return
        # Pre-flight only: confirm the rubric section exists. The composer no
        # longer embeds the body — Claude reads it itself from $design_doc_path.
        if not extract_rubric_section(design_text):
            await self._escalate(
                dw,
                reason="design_doc lacks '## 打分 rubric' section",
                problem_category=ProblemCategory.design_hollow,
            )
            return

        note = await self.iteration_notes.latest_for(dw["id"])
        if note is None:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP2_ITERATION,
                reason="Step5 found no iteration note",
                problem_category=ProblemCategory.req_gap,
            )
            return

        findings_rel = (
            f"devworks/{dw['id']}/artifacts/step4-findings-round{round_n}.json"
        )
        review_rel = (
            f"devworks/{dw['id']}/artifacts/step5-review-round{round_n}.json"
        )
        review_abs = self._abs_for(ws, review_rel)

        mount_entries = await self._load_mount_table_entries(dw)

        # Step3 ctx file path — Step5 reviewer reads it to verify Step4
        # addressed the疑点/risks Step3 raised. Phase 4 always passes a
        # concrete path; missing-file failure surfaces via the LLM's Read.
        ctx_rel = (
            f"devworks/{dw['id']}/context/ctx-round-{round_n}.md"
        )
        ctx_abs = self._abs_for(ws, ctx_rel)

        prompt = compose_step5(
            Step5Inputs(
                design_doc_path=self._abs_for(ws, dd["path"]),
                iteration_note_path=self._abs_for(
                    ws, note["markdown_path"]
                ),
                step4_findings_path=self._abs_for(ws, findings_rel),
                context_path=ctx_abs,
                mount_table_entries=mount_entries,
                primary_worktree_path=dw.get("worktree_path"),
                rubric_threshold=rubric_threshold,
                output_json_path=review_abs,
            )
        )
        prompt_rel = f"devworks/{dw['id']}/prompts/step5-round{round_n}.md"
        await self.registry.put_markdown(
            workspace_row=ws, relative_path=prompt_rel,
            text=prompt, kind="prompt",
        )

        rc, stdout = await self._run_llm(
            dw,
            agent=dw["agent"],
            worktree=dw["worktree_path"],
            timeout=self.config.devwork.step5_timeout,
            task_file=self._abs_for(ws, prompt_rel),
            step_tag="STEP5_REVIEW",
            round_n=round_n,
        )

        retry_key = f"step5_retry_round{round_n}"
        gates = await self._gates(dw["id"])
        attempt = int(gates.get(retry_key, 0))

        outcome: ReviewOutcome | None = None
        parse_reason: str | None = None
        if rc == 0:
            # Register the review artifact if the LLM produced it; missing
            # file isn't fatal — parse_review_output will fall back to stdout.
            try:
                await self.registry.index_existing(
                    workspace_row=ws, relative_path=review_rel,
                    kind="artifact",
                )
            except NotFoundError:
                pass
            try:
                outcome = parse_review_output(
                    stdout, output_json_path=review_abs
                )
            except BadRequestError as exc:
                parse_reason = str(exc)
        else:
            parse_reason = f"rc={rc}"

        if outcome is None:
            if attempt < 1:
                await self._update_gates_field(
                    dw["id"], retry_key, attempt + 1
                )
                return
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP5_REVIEW,
                reason=f"Step5 unparseable after retry: {parse_reason}",
                problem_category=None,
            )
            return

        await self._record_review(
            dw,
            note_id=note["id"],
            round_n=round_n,
            outcome=outcome,
        )

        category_value = (
            outcome.problem_category.value
            if outcome.problem_category
            else None
        )
        now = self._now()
        await self.db.execute(
            "UPDATE dev_works SET last_score=?, last_problem_category=?, "
            "updated_at=? WHERE id=?",
            (outcome.score, category_value, now, dw["id"]),
        )
        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="dev_work.round_completed",
            workspace_id=dw["workspace_id"],
            correlation_id=dw["id"],
            payload={
                "round": round_n,
                "score": outcome.score,
                "problem_category": category_value,
            },
        )

        if outcome.score >= rubric_threshold:
            fps = 1 if dw["iteration_rounds"] == 0 else 0
            await self.db.execute(
                "UPDATE dev_works SET current_step=?, first_pass_success=?, "
                "completed_at=?, updated_at=? WHERE id=?",
                (
                    DevWorkStep.COMPLETED.value,
                    fps,
                    now,
                    now,
                    dw["id"],
                ),
            )
            try:
                await self.workspaces.regenerate_workspace_md(dw["workspace_id"])
            except Exception:
                logger.exception(
                    "regenerate_workspace_md failed for %s", dw["workspace_id"]
                )
            await emit_and_deliver(
                self.db,
                self.webhooks,
                event_name="dev_work.score_passed",
                workspace_id=dw["workspace_id"],
                correlation_id=dw["id"],
                payload={"score": outcome.score, "round": round_n},
            )
            await emit_and_deliver(
                self.db,
                self.webhooks,
                event_name="dev_work.completed",
                workspace_id=dw["workspace_id"],
                correlation_id=dw["id"],
                payload={
                    "score": outcome.score,
                    "first_pass_success": bool(fps),
                },
            )
            return

        cat = outcome.problem_category
        if cat in (ProblemCategory.req_gap, ProblemCategory.impl_gap):
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP2_ITERATION,
                reason=f"{cat.value} score={outcome.score}",
                problem_category=cat,
            )
        else:
            # design_hollow OR None-category below threshold: escalate.
            await self._escalate(
                dw,
                reason=f"design_hollow/unknown score={outcome.score}",
                problem_category=ProblemCategory.design_hollow,
            )
