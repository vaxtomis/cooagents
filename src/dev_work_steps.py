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

import asyncio
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
_PLAN_CHECKBOX_RE = re.compile(
    r"^(\s*[-*]\s+\[)([ xX])(\]\s+)"
    r"([A-Za-z][A-Za-z0-9_-]*-\d+)(\s*[:：].*)$"
)


_REPAIR_STDOUT_LIMIT = 6000


def _tail_for_repair(text: str, limit: int = _REPAIR_STDOUT_LIMIT) -> str:
    """Keep repair prompts small even when the failed attempt was chatty."""
    if len(text) <= limit:
        return text
    return text[-limit:]


def _compose_step4_artifact_repair_prompt(
    *, output_path: str, parse_reason: str, stdout: str,
) -> str:
    return (
        "# DevWork STEP4 artifact repair\n\n"
        "## System retry feedback\n\n"
        "The previous STEP4 attempt finished but the required findings "
        f"artifact failed validation: {parse_reason}\n\n"
        "Do not modify source code, iteration notes, or ctx files. Only write "
        f"the missing/invalid self-review artifact to `{output_path}`.\n\n"
        f"灏嗚嚜瀹＄粨鏋滃啓鍏?`{output_path}`.\n\n"
        "Required JSON shape:\n"
        "```json\n"
        "{\"pass\": true, \"plan_execution\": [{\"id\": \"DW-01\", "
        "\"status\": \"done\", \"evidence\": [\"path/to/file.ts:10\"]}], "
        "\"findings\": []}\n"
        "```\n\n"
        "Before exiting, read the file back and confirm it is non-empty "
        "valid JSON. stdout is not accepted as the artifact.\n\n"
        "Previous stdout tail:\n"
        "```text\n"
        f"{_tail_for_repair(stdout)}\n"
        "```\n"
    )


def _compose_step5_artifact_repair_prompt(
    *, output_path: str, parse_reason: str, stdout: str,
) -> str:
    return (
        "# DevWork STEP5 review artifact repair\n\n"
        "## System retry feedback\n\n"
        "The previous STEP5 review finished but the required review artifact "
        f"failed validation: Step5 unparseable: {parse_reason}\n\n"
        "Do not re-review the code and do not modify source code, Step4 "
        "findings, iteration notes, or ctx files. Use the review conclusion "
        "already present in this session/stdout and write only the final "
        f"review JSON to `{output_path}`.\n\n"
        f"灏嗙粨鏋滃啓鍏?`{output_path}`.\n\n"
        "Required JSON shape:\n"
        "```json\n"
        "{\"score\": 90, \"issues\": [], \"plan_verification\": [], "
        "\"next_round_hints\": [], \"problem_category\": null}\n"
        "```\n\n"
        "Before exiting, read the file back and confirm it is non-empty "
        "valid JSON. stdout is not accepted as the artifact.\n\n"
        "Previous stdout tail:\n"
        "```text\n"
        f"{_tail_for_repair(stdout)}\n"
        "```\n"
    )


def _is_step4_findings_shape(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("pass"), bool)
        and isinstance(value.get("plan_execution"), list)
        and isinstance(value.get("findings"), list)
    )


def _apply_plan_verification_checkboxes(
    markdown: str, plan_verification: list[dict],
) -> str:
    """Check off only Step5-verified done items in ``## 开发计划``."""
    done_ids = {
        item.get("id")
        for item in plan_verification
        if (
            isinstance(item, dict)
            and item.get("status") == "done"
            and item.get("verified") is True
            and isinstance(item.get("id"), str)
        )
    }
    if not done_ids:
        return markdown

    lines = markdown.splitlines(keepends=True)
    in_plan = False
    changed = False
    for idx, line in enumerate(lines):
        body = line.rstrip("\r\n")
        newline = line[len(body):]
        h2 = re.match(r"^##\s+(.+?)\s*$", body)
        if h2:
            in_plan = h2.group(1) == "开发计划"
            continue
        if not in_plan:
            continue
        match = _PLAN_CHECKBOX_RE.match(body)
        if not match or match.group(4) not in done_ids:
            continue
        if match.group(2).lower() == "x":
            continue
        lines[idx] = (
            f"{match.group(1)}x{match.group(3)}{match.group(4)}"
            f"{match.group(5)}{newline}"
        )
        changed = True

    return "".join(lines) if changed else markdown


class DevWorkStepHandlersMixin:
    """Mixin providing Step2–Step5 handlers for DevWorkStateMachine."""

    async def _index_step4_findings_with_wait(
        self,
        *,
        workspace_row: dict[str, Any],
        relative_path: str,
    ) -> None:
        """Index Step4 findings, allowing a short post-process FS delay."""
        timeout_s = float(
            getattr(
                self.config.devwork,
                "step4_findings_wait_timeout_s",
                2.0,
            )
        )
        interval_s = float(
            getattr(
                self.config.devwork,
                "step4_findings_wait_interval_s",
                0.1,
            )
        )
        timeout_s = max(timeout_s, 0.0)
        interval_s = max(interval_s, 0.01)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s

        while True:
            try:
                await self.registry.index_existing(
                    workspace_row=workspace_row,
                    relative_path=relative_path,
                    kind="artifact",
                )
                return
            except NotFoundError:
                if loop.time() >= deadline:
                    raise
                await asyncio.sleep(min(interval_s, deadline - loop.time()))

    async def _repair_step4_findings_artifact(
        self,
        dw: dict[str, Any],
        *,
        workspace_row: dict[str, Any],
        round_n: int,
        findings_rel: str,
        parse_reason: str,
        stdout: str,
    ) -> dict[str, Any] | None:
        """Ask the current Step4 session to write only its missing artifact."""
        repair_prompt = _compose_step4_artifact_repair_prompt(
            output_path=self._abs_for(workspace_row, findings_rel),
            parse_reason=parse_reason,
            stdout=stdout,
        )
        prompt_rel = (
            f"devworks/{dw['id']}/prompts/"
            f"step4-round{round_n}-artifact-repair.md"
        )
        await self.registry.put_markdown(
            workspace_row=workspace_row,
            relative_path=prompt_rel,
            text=repair_prompt,
            kind="prompt",
        )
        rc, repair_stdout = await self._run_llm(
            dw,
            agent=dw["agent"],
            worktree=dw["worktree_path"],
            timeout=min(120, self.config.devwork.step4_acpx_wall_ceiling_s),
            task_file=self._abs_for(workspace_row, prompt_rel),
            step_tag="STEP4_DEVELOP",
            round_n=round_n,
            session_role="build",
        )
        if rc != 0:
            logger.warning(
                "dev_work %s Step4 artifact repair failed rc=%s stdout=%r",
                dw["id"],
                rc,
                repair_stdout[-512:],
            )
            return None
        try:
            await self._index_step4_findings_with_wait(
                workspace_row=workspace_row,
                relative_path=findings_rel,
            )
            findings_raw = await self.registry.read_text(
                workspace_slug=workspace_row["slug"],
                relative_path=findings_rel,
            )
            parsed = json.loads(findings_raw)
            if _is_step4_findings_shape(parsed):
                return parsed
            await self.registry.delete(
                workspace_row=workspace_row,
                relative_path=findings_rel,
            )
            return None
        except (NotFoundError, json.JSONDecodeError) as exc:
            logger.warning(
                "dev_work %s Step4 artifact repair output invalid: %s",
                dw["id"],
                exc,
            )
            try:
                await self.registry.delete(
                    workspace_row=workspace_row,
                    relative_path=findings_rel,
                )
            except Exception:
                logger.warning(
                    "delete invalid Step4 repair artifact failed for %s "
                    "round=%s",
                    dw["id"],
                    round_n,
                    exc_info=True,
                )
            return None

    async def _read_step5_review_outcome(
        self,
        *,
        workspace_row: dict[str, Any],
        review_rel: str,
        review_abs: str,
    ) -> tuple[ReviewOutcome | None, str | None, bool, int]:
        review_ref = await self.registry.stat(
            workspace_slug=workspace_row["slug"],
            relative_path=review_rel,
        )
        review_exists = review_ref is not None
        review_size = review_ref.size if review_ref is not None else 0
        if review_ref is None:
            return (
                None,
                f"review artifact missing: {review_rel}",
                review_exists,
                review_size,
            )
        if review_ref.size <= 0:
            return (
                None,
                f"review artifact empty: {review_rel}",
                review_exists,
                review_size,
            )
        try:
            await self.registry.index_existing(
                workspace_row=workspace_row,
                relative_path=review_rel,
                kind="artifact",
            )
        except NotFoundError:
            pass
        try:
            return (
                parse_review_output("", output_json_path=review_abs),
                None,
                review_exists,
                review_size,
            )
        except BadRequestError as exc:
            return None, str(exc), review_exists, review_size

    async def _repair_step5_review_artifact(
        self,
        dw: dict[str, Any],
        *,
        workspace_row: dict[str, Any],
        round_n: int,
        review_rel: str,
        review_abs: str,
        parse_reason: str,
        stdout: str,
    ) -> ReviewOutcome | None:
        """Ask the current Step5 session to persist only the review JSON."""
        repair_prompt = _compose_step5_artifact_repair_prompt(
            output_path=review_abs,
            parse_reason=parse_reason,
            stdout=stdout,
        )
        prompt_rel = (
            f"devworks/{dw['id']}/prompts/"
            f"step5-round{round_n}-artifact-repair.md"
        )
        await self.registry.put_markdown(
            workspace_row=workspace_row,
            relative_path=prompt_rel,
            text=repair_prompt,
            kind="prompt",
        )
        rc, repair_stdout = await self._run_llm(
            dw,
            agent=dw["agent"],
            worktree=dw["worktree_path"],
            timeout=min(120, self.config.devwork.step5_timeout),
            task_file=self._abs_for(workspace_row, prompt_rel),
            step_tag="STEP5_REVIEW",
            round_n=round_n,
            session_role="review",
        )
        if rc != 0:
            logger.warning(
                "dev_work %s Step5 artifact repair failed rc=%s stdout=%r",
                dw["id"],
                rc,
                repair_stdout[-512:],
            )
            return None
        outcome, reason, _exists, _size = await self._read_step5_review_outcome(
            workspace_row=workspace_row,
            review_rel=review_rel,
            review_abs=review_abs,
        )
        if outcome is None:
            logger.warning(
                "dev_work %s Step5 artifact repair output invalid: %s",
                dw["id"],
                reason,
            )
        return outcome

    async def _s2_iteration(self, dw: dict[str, Any]) -> None:
        """Step2 (F2=B): SM writes header -> LLM appends three H2 sections."""
        round_n = dw["iteration_rounds"] + 1
        try:
            await self._s2_iteration_body(dw, round_n)
        finally:
            # Phase 9: cold reviewer is freed after the iteration note is
            # appended; the next step opens its own (build) session.
            await self._delete_role_session(dw["id"], round_n, "plan")

    async def _s2_iteration_body(
        self, dw: dict[str, Any], round_n: int,
    ) -> None:
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

        rc, _stdout = await self._run_llm(
            dw,
            agent=dw["agent"],
            worktree=dw["worktree_path"],
            timeout=self.config.devwork.step2_timeout,
            task_file=prompt_abs,
            step_tag="STEP2_ITERATION",
            round_n=round_n,
            session_role="plan",
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
        # Phase 6: Step3 sees every mount's worktree (multi-mount tasks
        # may need to scan non-primary mounts for context).
        mount_entries = await self._load_mount_table_entries(dw)
        prompt = compose_step3(
            Step3Inputs(
                worktree_path=dw["worktree_path"],
                design_doc_path=self._abs_for(ws, dd["path"]),
                iteration_note_path=self._abs_for(ws, note["markdown_path"]),
                output_path=ctx_abs,
                mount_table_entries=mount_entries,
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
            session_role="build",
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
        # Step3 and Step4 use the same build role name for lifecycle
        # bookkeeping, but Step4 should not inherit Step3's exploratory
        # conversation. Close the Step3 build session before opening Step4's.
        await self._delete_role_session(dw["id"], round_n, "build")
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

        # Phase 6: per-mount worktrees are surfaced via mount_table_entries
        # (multi-mount tasks may write code into any mount). The scalar
        # ``worktree_path`` below remains the primary's path, used as the
        # prompt's default landing pad.
        mount_entries = await self._load_mount_table_entries(dw)
        retry_feedback = await self._loop_feedback_for_round(
            dw["id"], round_n, DevWorkStep.STEP4_DEVELOP
        )
        prompt = compose_step4(
            Step4Inputs(
                worktree_path=dw["worktree_path"],
                iteration_note_path=self._abs_for(ws, note["markdown_path"]),
                context_path=self._abs_for(ws, ctx_rel),
                findings_output_path=self._abs_for(ws, findings_rel),
                mount_table_entries=mount_entries,
                retry_feedback=retry_feedback,
            )
        )
        prompt_rel = f"devworks/{dw['id']}/prompts/step4-round{round_n}.md"
        await self.registry.put_markdown(
            workspace_row=ws, relative_path=prompt_rel,
            text=prompt, kind="prompt",
        )

        rc, stdout = await self._run_llm(
            dw,
            agent=dw["agent"],
            worktree=dw["worktree_path"],
            timeout=self.config.devwork.step4_acpx_wall_ceiling_s,
            task_file=self._abs_for(ws, prompt_rel),
            step_tag="STEP4_DEVELOP",
            round_n=round_n,
            session_role="build",
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
            await self._index_step4_findings_with_wait(
                workspace_row=ws, relative_path=findings_rel,
            )
        except NotFoundError:
            findings = await self._repair_step4_findings_artifact(
                dw,
                workspace_row=ws,
                round_n=round_n,
                findings_rel=findings_rel,
                parse_reason="Step4 findings JSON missing",
                stdout=stdout,
            )
            if findings is None:
                await self._loop_or_escalate(
                    dw,
                    back_to=DevWorkStep.STEP4_DEVELOP,
                    reason="Step4 findings JSON missing",
                    problem_category=ProblemCategory.impl_gap,
                )
                return
        else:
            try:
                findings_raw = await self.registry.read_text(
                    workspace_slug=ws["slug"], relative_path=findings_rel,
                )
                findings = json.loads(findings_raw)
            except (NotFoundError, json.JSONDecodeError) as exc:
                findings = await self._repair_step4_findings_artifact(
                    dw,
                    workspace_row=ws,
                    round_n=round_n,
                    findings_rel=findings_rel,
                    parse_reason=f"Step4 findings JSON invalid: {exc}",
                    stdout=stdout,
                )
                if findings is None:
                    await self._loop_or_escalate(
                        dw,
                        back_to=DevWorkStep.STEP4_DEVELOP,
                        reason=f"Step4 findings JSON invalid: {exc}",
                        problem_category=ProblemCategory.impl_gap,
                    )
                    return
        if not _is_step4_findings_shape(findings):
            try:
                await self.registry.delete(
                    workspace_row=ws,
                    relative_path=findings_rel,
                )
            except Exception:
                logger.warning(
                    "delete invalid Step4 findings failed for %s round=%s",
                    dw["id"],
                    round_n,
                    exc_info=True,
                )
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP4_DEVELOP,
                reason="Step4 findings JSON invalid: shape mismatch",
                problem_category=ProblemCategory.impl_gap,
            )
            return
        await self._update_gates_field(
            dw["id"], f"step4_findings_round{round_n}", findings
        )
        await self._transition(
            dw, DevWorkStep.STEP4_DEVELOP, DevWorkStep.STEP5_REVIEW
        )

    async def _apply_step5_plan_verification(
        self,
        *,
        workspace_row: dict[str, Any],
        note: dict[str, Any],
        outcome: ReviewOutcome,
    ) -> None:
        """Apply Step5-confirmed plan completion as a constrained note patch."""
        if not outcome.plan_verification:
            return
        body = await self.registry.read_text(
            workspace_slug=workspace_row["slug"],
            relative_path=note["markdown_path"],
        )
        updated = _apply_plan_verification_checkboxes(
            body, outcome.plan_verification,
        )
        if updated == body:
            return
        await self.registry.put_markdown(
            workspace_row=workspace_row,
            relative_path=note["markdown_path"],
            text=updated,
            kind="iteration_note",
        )

    async def _s5_review(self, dw: dict[str, Any]) -> None:
        """Rubric scoring; in-place retry once on parse failure.

        Phase 8: prompt is path-based — the LLM Reads the design doc /
        iteration note / Step4 findings itself, and Bashes ``git diff HEAD``
        in the primary worktree. The SM only does the rubric pre-flight
        (so an empty rubric escalates without spinning up a Step5 round).

        Phase 9: at entry, delete the build session that survived the
        Step3 → Step4 boundary (cold reviewer policy — the reviewer must
        not share state with the process that wrote the code). The review
        session is opened on the LLM call and torn down in the finally
        block; on retry, the cache's stale-name check forces a fresh
        ensure for the next attempt.
        """
        round_n = dw["iteration_rounds"] + 1
        await self._delete_role_session(dw["id"], round_n, "build")
        try:
            await self._s5_review_body(dw, round_n)
        finally:
            await self._delete_role_session(dw["id"], round_n, "review")

    async def _persist_step5_failed_attempt(
        self,
        *,
        workspace_row: dict[str, Any],
        dw: dict[str, Any],
        round_n: int,
        attempt: int,
        review_rel: str,
        review_exists: bool,
        review_size: int,
        rc: int,
        stdout: str,
        parse_reason: str,
    ) -> None:
        base_rel = (
            f"devworks/{dw['id']}/artifacts/"
            f"step5-review-round{round_n}-attempt{attempt}"
        )
        stdout_rel = f"{base_rel}-stdout.md"
        await self.registry.put_markdown(
            workspace_row=workspace_row,
            relative_path=stdout_rel,
            text=stdout or "",
            kind="artifact",
        )

        payload: dict[str, Any] = {
            "dev_work_id": dw["id"],
            "step": DevWorkStep.STEP5_REVIEW.value,
            "round": round_n,
            "attempt": attempt,
            "rc": rc,
            "parse_reason": parse_reason,
            "expected_review_path": review_rel,
            "review_artifact_exists": review_exists,
            "review_artifact_size": review_size,
            "stdout_artifact_path": stdout_rel,
        }
        if review_exists and review_size > 0:
            review_copy_rel = f"{base_rel}-review-output.md"
            try:
                review_text = await self.registry.read_text(
                    workspace_slug=workspace_row["slug"],
                    relative_path=review_rel,
                )
            except Exception:
                logger.warning(
                    "dev_work %s Step5 failed review artifact unreadable "
                    "(round=%s attempt=%s)",
                    dw["id"],
                    round_n,
                    attempt,
                    exc_info=True,
                )
            else:
                await self.registry.put_markdown(
                    workspace_row=workspace_row,
                    relative_path=review_copy_rel,
                    text=review_text,
                    kind="artifact",
                )
                payload["review_output_artifact_path"] = review_copy_rel

        await self.registry.put_json(
            workspace_row=workspace_row,
            relative_path=f"{base_rel}-failure.json",
            payload=payload,
            kind="artifact",
        )

    async def _s5_review_body(
        self, dw: dict[str, Any], round_n: int,
    ) -> None:
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
        if note.get("round") != round_n:
            await self._escalate(
                dw,
                reason=(
                    "Step5 iteration note round mismatch: "
                    f"note round {note.get('round')} != review round {round_n}"
                ),
                problem_category=None,
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
        retry_feedback = await self._loop_feedback_for_round(
            dw["id"], round_n, DevWorkStep.STEP5_REVIEW
        )

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
                retry_feedback=retry_feedback,
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
            session_role="review",
        )

        retry_key = f"step5_retry_round{round_n}"
        gates = await self._gates(dw["id"])
        attempt_raw = gates.get(retry_key, 0)
        attempt = attempt_raw if isinstance(attempt_raw, int) else 0
        attempt_n = attempt + 1

        outcome: ReviewOutcome | None = None
        parse_reason: str | None = None
        review_exists = False
        review_size = 0
        if rc == 0:
            (
                outcome,
                parse_reason,
                review_exists,
                review_size,
            ) = await self._read_step5_review_outcome(
                workspace_row=ws,
                review_rel=review_rel,
                review_abs=review_abs,
            )
        else:
            parse_reason = f"rc={rc}"

        if outcome is None:
            parse_reason = parse_reason or "unknown Step5 review failure"
            try:
                await self._persist_step5_failed_attempt(
                    workspace_row=ws,
                    dw=dw,
                    round_n=round_n,
                    attempt=attempt_n,
                    review_rel=review_rel,
                    review_exists=review_exists,
                    review_size=review_size,
                    rc=rc,
                    stdout=stdout,
                    parse_reason=parse_reason,
                )
            except Exception as exc:
                logger.exception(
                    "dev_work %s Step5 failed-attempt persistence failed "
                    "(round=%s attempt=%s)",
                    dw["id"],
                    round_n,
                    attempt_n,
                )
                await self._escalate(
                    dw,
                    reason=f"Step5 failed-attempt persistence failed: {exc}",
                    problem_category=None,
                )
                return
            if rc == 0:
                outcome = await self._repair_step5_review_artifact(
                    dw,
                    workspace_row=ws,
                    round_n=round_n,
                    review_rel=review_rel,
                    review_abs=review_abs,
                    parse_reason=parse_reason,
                    stdout=stdout,
                )
                if outcome is not None:
                    parse_reason = None
            if outcome is None:
                await self._loop_or_escalate(
                    dw,
                    back_to=DevWorkStep.STEP5_REVIEW,
                    reason=f"Step5 unparseable: {parse_reason}",
                    problem_category=None,
                )
                return

        await self._record_review(
            dw,
            note_id=note["id"],
            round_n=round_n,
            outcome=outcome,
        )

        try:
            await self._apply_step5_plan_verification(
                workspace_row=ws,
                note=note,
                outcome=outcome,
            )
        except Exception as exc:
            logger.exception(
                "dev_work %s Step5 plan checkbox update failed (round=%s)",
                dw["id"],
                round_n,
            )
            await self._escalate(
                dw,
                reason=f"Step5 plan checkbox update failed: {exc}",
                problem_category=None,
            )
            return

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
            # Phase 9: by the time we reach the COMPLETED branch, the plan
            # session was deleted in Step2's finally, the build session in
            # Step5's entry, and the review session will be deleted by
            # ``_s5_review``'s outer finally — so the cache is already
            # empty here. Terminal cleanup is therefore implicit; the
            # boot-time orphan sweep covers anything that escaped.
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
