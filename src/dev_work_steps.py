"""DevWork Step2-Step5 handlers extracted as a mixin.

Split out of :mod:`src.dev_work_sm` so the SM orchestrator stays under the
800-line project cap. Each handler is still a coroutine that takes a DB
row dict and drives one tick; the driver loop in
``DevWorkStateMachine.run_to_completion`` calls them via the step→handler
dispatch in ``tick``.

The mixin expects the concrete class to provide:
  * ``self.db`` — database handle
  * ``self.workspaces`` / ``self.iteration_notes`` — managers
  * ``self.config.devwork`` — step timeouts + max_rounds
  * ``self._now()`` / ``self._run_llm`` / ``self._collect_diff`` /
    ``self._gates`` / ``self._update_gates_field`` / ``self._transition`` /
    ``self._record_review`` / ``self._resolve_rubric_threshold`` /
    ``self._escalate`` / ``self._loop_or_escalate``
  * ``self.workspace_events.emit`` equivalent via ``emit_workspace_event``

Do not import :mod:`src.dev_work_sm` here — avoids a circular import.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
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
from src.exceptions import BadRequestError
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
        try:
            design_text = Path(dd["path"]).read_text(encoding="utf-8")
        except OSError as exc:
            await self._escalate(
                dw,
                reason=f"design_doc file unreadable at Step2: {exc}",
                problem_category=ProblemCategory.design_hollow,
            )
            return

        prev_feedback = await self._last_review_text(dw["id"])

        # 1) Write the SM-owned header (front-matter + H1) so the LLM can only
        #    append; this locks those lines against prompt-injection rewrites.
        note_path = self.iteration_notes.path_for(ws, dw["id"], round_n)
        header = compose_iteration_header(
            IterationHeaderInputs(
                dev_work_id=dw["id"],
                design_doc_path=dd["path"],
                round=round_n,
                created_at=self._now(),
            )
        )
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(header, encoding="utf-8")

        # 2) Compose Step2 prompt and run the LLM.
        prompt_text = compose_step2(
            Step2Inputs(
                dev_work_id=dw["id"],
                round=round_n,
                design_doc_text=design_text,
                user_prompt=dw["prompt"],
                previous_feedback=prev_feedback,
                output_path=str(note_path),
            )
        )
        prompt_path = note_path.parent / f"step2-prompt-round{round_n}.md"
        prompt_path.write_text(prompt_text, encoding="utf-8")

        worktree_cwd = dw["worktree_path"] or str(note_path.parent)
        rc, _stdout = await self._run_llm(
            dw,
            agent=dw["agent"],
            worktree=worktree_cwd,
            timeout=self.config.devwork.step2_timeout,
            task_file=str(prompt_path),
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
            body = note_path.read_text(encoding="utf-8")
        except OSError as exc:
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

        # 4) Register the note (UNIQUE(dev_work_id, round) is invariant-checked).
        try:
            await self.iteration_notes.record_round(
                workspace_row=ws,
                dev_work_id=dw["id"],
                round_n=round_n,
                markdown_path=str(note_path),
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
        ctx_dir = Path(dw["worktree_path"]) / ".cooagents"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        output_path = ctx_dir / f"ctx-round-{round_n}.md"
        prompt = compose_step3(
            Step3Inputs(
                worktree_path=dw["worktree_path"],
                design_doc_path=dd["path"],
                iteration_note_path=note["markdown_path"],
                output_path=str(output_path),
            )
        )
        prompt_path = ctx_dir / f"step3-prompt-round{round_n}.md"
        prompt_path.write_text(prompt, encoding="utf-8")

        retry_key = f"step3_retry_round{round_n}"
        gates = await self._gates(dw["id"])
        attempt = int(gates.get(retry_key, 0))

        rc, _stdout = await self._run_llm(
            dw,
            agent=dw["agent"],
            worktree=dw["worktree_path"],
            timeout=self.config.devwork.step3_timeout,
            task_file=str(prompt_path),
            step_tag="STEP3_CONTEXT",
            round_n=round_n,
        )

        if rc == 0 and output_path.exists():
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
            reason=f"Step3 failed twice (rc={rc}, out_exists={output_path.exists()})",
            problem_category=ProblemCategory.req_gap,
        )

    async def _s4_develop(self, dw: dict[str, Any]) -> None:
        """Implement + self-review once; parse findings JSON."""
        round_n = dw["iteration_rounds"] + 1
        note = await self.iteration_notes.latest_for(dw["id"])
        if note is None:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP2_ITERATION,
                reason="Step4 found no iteration note",
                problem_category=ProblemCategory.req_gap,
            )
            return

        ctx_dir = Path(dw["worktree_path"]) / ".cooagents"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        context_path = ctx_dir / f"ctx-round-{round_n}.md"
        findings_path = ctx_dir / f"step4-round{round_n}-findings.json"

        prompt = compose_step4(
            Step4Inputs(
                worktree_path=dw["worktree_path"],
                iteration_note_path=note["markdown_path"],
                context_path=str(context_path),
                findings_output_path=str(findings_path),
            )
        )
        prompt_path = ctx_dir / f"step4-prompt-round{round_n}.md"
        prompt_path.write_text(prompt, encoding="utf-8")

        rc, _stdout = await self._run_llm(
            dw,
            agent=dw["agent"],
            worktree=dw["worktree_path"],
            timeout=self.config.devwork.step4_timeout,
            task_file=str(prompt_path),
            step_tag="STEP4_DEVELOP",
            round_n=round_n,
        )

        if rc != 0 or not findings_path.exists():
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP4_DEVELOP,
                reason=(
                    f"Step4 failed (rc={rc}, findings_exists="
                    f"{findings_path.exists()})"
                ),
                problem_category=ProblemCategory.impl_gap,
            )
            return

        try:
            findings = json.loads(findings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
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
        """Rubric scoring; in-place retry once on parse failure."""
        round_n = dw["iteration_rounds"] + 1
        rubric_threshold = await self._resolve_rubric_threshold(dw)

        dd = await self.db.fetchone(
            "SELECT * FROM design_docs WHERE id=?", (dw["design_doc_id"],)
        )
        try:
            design_text = Path(dd["path"]).read_text(encoding="utf-8")
        except OSError as exc:
            await self._escalate(
                dw,
                reason=f"design_doc unreadable at Step5: {exc}",
                problem_category=ProblemCategory.design_hollow,
            )
            return
        rubric_body = extract_rubric_section(design_text)
        if not rubric_body:
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
        try:
            iteration_note_text = Path(note["markdown_path"]).read_text(
                encoding="utf-8"
            )
        except OSError as exc:
            # Scoring against a synthetic placeholder would produce a
            # semantically meaningless result; escalate instead of silently
            # substituting as prior versions did.
            logger.warning(
                "dev_work %s iteration note unreadable at Step5: %s (%s)",
                dw["id"],
                note["markdown_path"],
                exc,
            )
            await self._escalate(
                dw,
                reason=f"iteration note unreadable at Step5: {exc}",
                problem_category=None,
            )
            return

        gates = await self._gates(dw["id"])
        step4_findings = gates.get(f"step4_findings_round{round_n}", {})
        step4_findings_json = json.dumps(
            step4_findings, ensure_ascii=False, indent=2
        )

        diff_text = await self._collect_diff(dw["worktree_path"])

        ctx_dir = Path(dw["worktree_path"]) / ".cooagents"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        output_json_path = ctx_dir / f"step5-round{round_n}.json"

        prompt = compose_step5(
            Step5Inputs(
                design_doc_text=design_text,
                rubric_section_text=rubric_body,
                iteration_note_text=iteration_note_text,
                diff_text=diff_text,
                step4_findings_json=step4_findings_json,
                rubric_threshold=rubric_threshold,
                output_json_path=str(output_json_path),
            )
        )
        prompt_path = ctx_dir / f"step5-prompt-round{round_n}.md"
        prompt_path.write_text(prompt, encoding="utf-8")

        rc, stdout = await self._run_llm(
            dw,
            agent=dw["agent"],
            worktree=dw["worktree_path"],
            timeout=self.config.devwork.step5_timeout,
            task_file=str(prompt_path),
            step_tag="STEP5_REVIEW",
            round_n=round_n,
        )

        retry_key = f"step5_retry_round{round_n}"
        attempt = int(gates.get(retry_key, 0))

        outcome: ReviewOutcome | None = None
        parse_reason: str | None = None
        if rc == 0:
            try:
                outcome = parse_review_output(
                    stdout, output_json_path=str(output_json_path)
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
                await self.workspaces.refresh_workspace_md(dw["workspace_id"])
            except Exception:
                logger.exception(
                    "refresh_workspace_md failed for %s", dw["workspace_id"]
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
        if cat == ProblemCategory.req_gap:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP2_ITERATION,
                reason=f"req_gap score={outcome.score}",
                problem_category=cat,
            )
        elif cat == ProblemCategory.impl_gap:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP4_DEVELOP,
                reason=f"impl_gap score={outcome.score}",
                problem_category=cat,
            )
        else:
            # design_hollow OR None-category below threshold: escalate.
            await self._escalate(
                dw,
                reason=f"design_hollow/unknown score={outcome.score}",
                problem_category=ProblemCategory.design_hollow,
            )
