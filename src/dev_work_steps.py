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
from dataclasses import dataclass
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
from src.dev_plan_audit import (
    PLAN_CHECKBOX_RE as _PLAN_CHECKBOX_RE,
    extract_plan_checklist_items as _extract_plan_checklist_items,
    extract_plan_ids_from_value as _extract_plan_ids_from_value,
    format_plan_sample as _format_plan_sample,
    missing_plan_verification_ids as _missing_plan_verification_ids,
    render_step5_plan_audit_targets as _render_step5_plan_audit_targets,
)
from src.exceptions import BadRequestError, NotFoundError
from src.git_utils import run_git
from src.models import DevWorkStep, ProblemCategory
from src.reviewer import ReviewOutcome, parse_review_output
from src.workspace_events import emit_and_deliver

logger = logging.getLogger(__name__)

_REQUIRED_H2 = ("本轮目标", "开发计划", "用例清单")
_RECOMMENDED_TECH_STACK_H2 = "推荐技术栈"


_REPAIR_STDOUT_LIMIT = 6000
_STEP5_DIFF_MAX_FILES = 300
_STEP5_DIFF_MAX_CHANGED_LINES = 50_000
_STEP5_DIFF_FORBIDDEN_SEGMENTS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".turbo",
    ".vite",
    "__pycache__",
    "coverage",
    "node_modules",
}
_STEP5_DIFF_FORBIDDEN_SUFFIXES = {
    ".tsbuildinfo",
}


@dataclass(frozen=True)
class _Step5PreflightFailure:
    reason: str
    generated_paths: tuple[str, ...] = ()


def _tail_for_repair(text: str, limit: int = _REPAIR_STDOUT_LIMIT) -> str:
    """Keep repair prompts small even when the failed attempt was chatty."""
    if len(text) <= limit:
        return text
    return text[-limit:]


def _normalise_git_path(path: str) -> str:
    return path.replace("\\", "/").strip()


def _paths_from_git_status_porcelain(output: str) -> list[str]:
    paths: list[str] = []
    for line in (output or "").splitlines():
        if len(line) < 4:
            continue
        path = _normalise_git_path(line[3:])
        if " -> " in path:
            old_path, new_path = path.split(" -> ", 1)
            paths.extend([old_path, new_path])
        elif path:
            paths.append(path)
    return paths


def _is_forbidden_step5_diff_path(path: str) -> bool:
    normalised = _normalise_git_path(path)
    parts = {part for part in normalised.split("/") if part}
    if parts & _STEP5_DIFF_FORBIDDEN_SEGMENTS:
        return True
    return any(
        normalised.endswith(suffix)
        for suffix in _STEP5_DIFF_FORBIDDEN_SUFFIXES
    )


def _parse_git_numstat_changed_lines(output: str) -> int:
    changed = 0
    for line in (output or "").splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        for value in cols[:2]:
            if value == "-":
                changed += 1
                continue
            try:
                changed += int(value)
            except ValueError:
                continue
    return changed


def _review_outcome_to_payload(outcome: ReviewOutcome) -> dict[str, Any]:
    category = (
        outcome.problem_category.value
        if outcome.problem_category is not None
        else None
    )
    return {
        "score": outcome.score,
        "score_breakdown": outcome.score_breakdown,
        "issues": outcome.issues,
        "plan_verification": outcome.plan_verification,
        "next_round_hints": outcome.next_round_hints,
        "problem_category": category,
    }


def _extract_actual_score_b(score_breakdown: str | None) -> int | None:
    if not score_breakdown:
        return None
    try:
        payload = json.loads(score_breakdown)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("actual_score_b")
    if isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 100 else None


def _format_path_sample(paths: list[str], *, limit: int = 5) -> str:
    sample = ", ".join(paths[:limit])
    extra = len(paths) - limit
    if extra > 0:
        sample = f"{sample} (+{extra} more)"
    return sample


def _compose_step5_generated_diff_repair_prompt(
    *,
    mount_rows: list[dict[str, Any]],
    generated_paths: tuple[str, ...],
) -> str:
    mounts = "\n".join(
        f"- mount `{row['mount_name']}`: `{row['worktree_path']}`"
        for row in mount_rows
        if row.get("worktree_path")
    ) or "- _(no worktree paths available)_"
    paths = "\n".join(f"- `{path}`" for path in generated_paths)
    return (
        "# DevWork STEP5 generated/dependency diff repair\n\n"
        "## Goal\n\n"
        "Step5 preflight found generated, dependency, or cache paths in "
        "git status/diff. Do one narrow cleanup pass so the reviewer sees "
        "only source changes.\n\n"
        "## Worktrees\n\n"
        f"{mounts}\n\n"
        "## Affected paths\n\n"
        f"{paths}\n\n"
        "## Allowed actions\n\n"
        "- Update `.gitignore` files in the affected worktree(s).\n"
        "- Remove generated/cache/dependency artifacts from the git index "
        "and working tree when needed.\n"
        "- Run `git status --porcelain` and `git diff HEAD --name-only` "
        "to verify the affected paths no longer appear.\n\n"
        "## Forbidden actions\n\n"
        "- Do not edit source code, tests, package manifests, lockfiles, "
        "Step4 findings, iteration notes, or design/context artifacts.\n"
        "- Do not implement feature work or rerun the broader Step4 task.\n"
        "- Do not stage unrelated files.\n\n"
        "Before exiting, verify the affected generated/dependency paths are "
        "absent from `git status --porcelain` and `git diff HEAD --name-only`."
    )


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
    *,
    output_path: str,
    parse_reason: str,
    stdout: str,
    plan_audit_targets: str | None = None,
) -> str:
    audit_block = (
        f"\n{plan_audit_targets}\n\n"
        "The repaired JSON must include one `plan_verification` item for "
        "every active plan ID listed above.\n\n"
        if plan_audit_targets
        else ""
    )
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
        "{\"score\": 90, \"score_breakdown\": "
        "{\"plan_score_a\": 100, \"actual_score_b\": 90, "
        "\"final_score\": 90}, "
        "\"issues\": [], \"plan_verification\": [], "
        "\"next_round_hints\": [], \"problem_category\": null}\n"
        "```\n\n"
        f"{audit_block}"
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
    """Check off Step5-confirmed delivered items in ``## 开发计划``."""
    done_ids = {
        item.get("id")
        for item in plan_verification
        if (
            isinstance(item, dict)
            and item.get("status") == "done"
            and item.get("implemented") is not False
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
        if not match or match.group(5) not in done_ids:
            continue
        if match.group(2).lower() == "x":
            continue
        lines[idx] = (
            f"{match.group(1)}x{match.group(3)}{match.group(4)}"
            f"{match.group(5)}{match.group(6)}{newline}"
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

    async def _preflight_step5_worktrees(
        self, dw: dict[str, Any],
    ) -> _Step5PreflightFailure | None:
        rows = await self.db.fetchall(
            "SELECT mount_name, base_branch, base_rev, devwork_branch, "
            "is_primary, worktree_path FROM dev_work_repos "
            "WHERE dev_work_id=? ORDER BY is_primary DESC, mount_name",
            (dw["id"],),
        )
        if not rows:
            return None

        for row in rows:
            mount = row["mount_name"]
            worktree_path = row["worktree_path"]
            if not worktree_path and row["is_primary"]:
                worktree_path = dw.get("worktree_path")
            if not worktree_path:
                return _Step5PreflightFailure(
                    f"mount {mount!r} has no worktree_path"
                )
            if not Path(worktree_path).exists():
                return _Step5PreflightFailure(
                    f"mount {mount!r} worktree does not exist: "
                    f"{worktree_path}"
                )

            expected_branch = row["devwork_branch"]
            try:
                branch, branch_err, branch_rc = await run_git(
                    "symbolic-ref", "--quiet", "--short", "HEAD",
                    cwd=worktree_path, check=False,
                )
            except Exception as exc:
                return _Step5PreflightFailure(
                    f"mount {mount!r} cannot inspect current branch: {exc}"
                )
            if branch_rc != 0 or branch != expected_branch:
                current = branch or branch_err or "detached HEAD"
                return _Step5PreflightFailure(
                    f"mount {mount!r} worktree is on {current!r}, "
                    f"expected {expected_branch!r}"
                )

            try:
                _head, _head_err, head_rc = await run_git(
                    "rev-parse", "--verify", "HEAD^{commit}",
                    cwd=worktree_path, check=False,
                )
                if head_rc != 0:
                    start_point = row.get("base_rev") or row["base_branch"]
                    if not start_point:
                        return _Step5PreflightFailure(
                            f"mount {mount!r} has unborn HEAD and no "
                            "base_rev/base_branch repair point"
                        )
                    await run_git(
                        "reset", "--mixed", start_point, cwd=worktree_path,
                    )
            except Exception as exc:
                return _Step5PreflightFailure(
                    f"mount {mount!r} HEAD preflight/repair failed: {exc}"
                )

            try:
                diff_names, _err, _rc = await run_git(
                    "diff", "HEAD", "--name-only", cwd=worktree_path,
                )
                status_out, _err, _rc = await run_git(
                    "status", "--porcelain=v1", cwd=worktree_path,
                )
                numstat, _err, _rc = await run_git(
                    "diff", "HEAD", "--numstat", cwd=worktree_path,
                )
            except Exception as exc:
                return _Step5PreflightFailure(
                    f"mount {mount!r} diff preflight failed: {exc}"
                )

            changed_paths = sorted({
                _normalise_git_path(path)
                for path in (
                    diff_names.splitlines()
                    + _paths_from_git_status_porcelain(status_out)
                )
                if _normalise_git_path(path)
            })
            forbidden = [
                path for path in changed_paths
                if _is_forbidden_step5_diff_path(path)
            ]
            if forbidden:
                return _Step5PreflightFailure(
                    reason=(
                        f"mount {mount!r} diff contains "
                        "generated/dependency paths: "
                        f"{_format_path_sample(forbidden)}"
                    ),
                    generated_paths=tuple(forbidden),
                )
            if len(changed_paths) > _STEP5_DIFF_MAX_FILES:
                return _Step5PreflightFailure(
                    f"mount {mount!r} diff touches {len(changed_paths)} "
                    f"files, limit is {_STEP5_DIFF_MAX_FILES}"
                )

            changed_lines = _parse_git_numstat_changed_lines(numstat)
            if changed_lines > _STEP5_DIFF_MAX_CHANGED_LINES:
                return _Step5PreflightFailure(
                    f"mount {mount!r} diff changes {changed_lines} lines, "
                    f"limit is {_STEP5_DIFF_MAX_CHANGED_LINES}"
                )

        return None

    async def _collect_step5_changed_paths(
        self, dw: dict[str, Any],
    ) -> set[str]:
        rows = await self.db.fetchall(
            "SELECT mount_name, is_primary, worktree_path FROM dev_work_repos "
            "WHERE dev_work_id=? ORDER BY is_primary DESC, mount_name",
            (dw["id"],),
        )
        if not rows and dw.get("worktree_path"):
            rows = [{
                "mount_name": "primary",
                "is_primary": True,
                "worktree_path": dw.get("worktree_path"),
            }]

        changed: set[str] = set()
        for row in rows:
            worktree_path = row["worktree_path"]
            if not worktree_path and row["is_primary"]:
                worktree_path = dw.get("worktree_path")
            if not worktree_path:
                continue
            try:
                diff_names, _err, _rc = await run_git(
                    "diff", "HEAD", "--name-only",
                    cwd=worktree_path, check=False,
                )
                status_out, _err, _rc = await run_git(
                    "status", "--porcelain=v1",
                    cwd=worktree_path, check=False,
                )
            except Exception:
                logger.warning(
                    "dev_work %s Step5 changed-path collection failed "
                    "for mount=%s",
                    dw["id"],
                    row["mount_name"],
                    exc_info=True,
                )
                continue
            for path in diff_names.splitlines():
                normalised = _normalise_git_path(path)
                if normalised:
                    changed.add(normalised)
            for path in _paths_from_git_status_porcelain(status_out):
                normalised = _normalise_git_path(path)
                if normalised:
                    changed.add(normalised)
        return changed

    async def _load_previous_plan_ledger(
        self, dev_work_id: str, round_n: int,
    ) -> dict[str, dict[str, Any]]:
        row = await self.db.fetchone(
            "SELECT findings_json FROM reviews "
            "WHERE dev_work_id=? AND round < ? "
            "ORDER BY round DESC, created_at DESC LIMIT 1",
            (dev_work_id, round_n),
        )
        if row is None or not row["findings_json"]:
            return {}
        try:
            payload = json.loads(row["findings_json"])
        except (TypeError, ValueError):
            return {}
        if not isinstance(payload, list):
            return {}
        ledger: dict[str, dict[str, Any]] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            plan_id = item.get("id")
            if isinstance(plan_id, str) and plan_id.strip():
                ledger[plan_id.strip()] = item
        return ledger

    async def _load_step4_touched_plan_ids(
        self,
        *,
        workspace_row: dict[str, Any],
        findings_rel: str,
    ) -> set[str]:
        try:
            raw = await self.registry.read_text(
                workspace_slug=workspace_row["slug"],
                relative_path=findings_rel,
            )
            payload = json.loads(raw)
        except (NotFoundError, TypeError, ValueError):
            return set()
        if not isinstance(payload, dict):
            return set()
        return _extract_plan_ids_from_value(payload.get("plan_execution"))

    async def _repair_step5_generated_diff(
        self,
        dw: dict[str, Any],
        *,
        workspace_row: dict[str, Any],
        round_n: int,
        generated_paths: tuple[str, ...],
    ) -> bool:
        rows = await self.db.fetchall(
            "SELECT mount_name, is_primary, worktree_path FROM dev_work_repos "
            "WHERE dev_work_id=? ORDER BY is_primary DESC, mount_name",
            (dw["id"],),
        )
        mount_rows: list[dict[str, Any]] = []
        for row in rows:
            mount_rows.append({
                "mount_name": row["mount_name"],
                "worktree_path": (
                    row["worktree_path"]
                    or (
                        dw.get("worktree_path")
                        if row["is_primary"]
                        else None
                    )
                ),
            })
        prompt = _compose_step5_generated_diff_repair_prompt(
            mount_rows=mount_rows,
            generated_paths=generated_paths,
        )
        prompt_rel = (
            f"devworks/{dw['id']}/prompts/"
            f"step5-round{round_n}-generated-diff-repair.md"
        )
        await self.registry.put_markdown(
            workspace_row=workspace_row,
            relative_path=prompt_rel,
            text=prompt,
            kind="prompt",
        )
        try:
            rc, stdout = await self._run_llm(
                dw,
                agent=dw["agent"],
                worktree=dw["worktree_path"],
                timeout=min(
                    180,
                    self.config.devwork.step4_acpx_wall_ceiling_s,
                ),
                task_file=self._abs_for(workspace_row, prompt_rel),
                step_tag="STEP5_PREFLIGHT_REPAIR",
                round_n=round_n,
                session_role="build",
            )
        finally:
            await self._delete_role_session(dw["id"], round_n, "build")

        stdout_rel = (
            f"devworks/{dw['id']}/artifacts/"
            f"step5-round{round_n}-generated-diff-repair-stdout.md"
        )
        await self.registry.put_markdown(
            workspace_row=workspace_row,
            relative_path=stdout_rel,
            text=stdout or "",
            kind="artifact",
        )
        if rc != 0:
            logger.warning(
                "dev_work %s Step5 generated diff repair failed rc=%s "
                "stdout=%r",
                dw["id"],
                rc,
                (stdout or "")[-512:],
            )
            return False
        return True

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
        stdout: str = "",
    ) -> tuple[ReviewOutcome | None, str | None, bool, int]:
        async def persist_stdout_outcome() -> tuple[
            ReviewOutcome | None, str | None, bool, int
        ]:
            try:
                outcome = parse_review_output(stdout)
            except BadRequestError as exc:
                return None, str(exc), review_exists, review_size
            ref = await self.registry.put_json(
                workspace_row=workspace_row,
                relative_path=review_rel,
                payload=_review_outcome_to_payload(outcome),
                kind="artifact",
            )
            return outcome, None, True, ref["byte_size"]

        review_ref = await self.registry.stat(
            workspace_slug=workspace_row["slug"],
            relative_path=review_rel,
        )
        review_exists = review_ref is not None
        review_size = review_ref.size if review_ref is not None else 0
        if review_ref is None:
            if stdout:
                outcome, reason, exists, size = await persist_stdout_outcome()
                if outcome is not None:
                    return outcome, reason, exists, size
            return (
                None,
                f"review artifact missing: {review_rel}",
                review_exists,
                review_size,
            )
        if review_ref.size <= 0:
            if stdout:
                outcome, reason, exists, size = await persist_stdout_outcome()
                if outcome is not None:
                    return outcome, reason, exists, size
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
            file_reason = str(exc)
            if stdout:
                outcome, reason, exists, size = await persist_stdout_outcome()
                if outcome is not None:
                    return outcome, reason, exists, size
            return None, file_reason, review_exists, review_size

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
        plan_audit_targets: str | None = None,
    ) -> ReviewOutcome | None:
        """Ask the current Step5 session to persist only the review JSON."""
        repair_prompt = _compose_step5_artifact_repair_prompt(
            output_path=review_abs,
            parse_reason=parse_reason,
            stdout=stdout,
            plan_audit_targets=plan_audit_targets,
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
            stdout=repair_stdout,
        )
        if outcome is None:
            logger.warning(
                "dev_work %s Step5 artifact repair output invalid: %s",
                dw["id"],
                reason,
            )
        return outcome

    async def _s2_iteration(self, dw: dict[str, Any]) -> None:
        """Step2 (F2=B): SM writes header -> LLM appends required H2 sections."""
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
        prev_note_abs = (
            self._abs_for(
                ws, self.iteration_notes.relative_for(dw["id"], round_n - 1)
            )
            if round_n > 1
            else None
        )

        recommended_tech_stack = dw.get("recommended_tech_stack")
        if not isinstance(recommended_tech_stack, str):
            recommended_tech_stack = None
        elif not recommended_tech_stack.strip():
            recommended_tech_stack = None

        # 3) Compose Step2 prompt and run the LLM.
        prompt_text = compose_step2(
            Step2Inputs(
                dev_work_id=dw["id"],
                round=round_n,
                design_doc_path=design_doc_abs,
                user_prompt=dw["prompt"],
                previous_review_path=prev_review_abs,
                previous_iteration_note_path=prev_note_abs,
                recommended_tech_stack=recommended_tech_stack,
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

        # 3) Validate the produced markdown: base H2s are always required;
        #    the recommended stack section is required only when a human
        #    recommendation was provided at DevWork creation.
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
        required_h2 = (
            (*_REQUIRED_H2, _RECOMMENDED_TECH_STACK_H2)
            if recommended_tech_stack
            else _REQUIRED_H2
        )
        missing = [h for h in required_h2 if h not in found]
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
        previous_review = await self.db.fetchone(
            "SELECT score_breakdown_json FROM reviews "
            "WHERE dev_work_id=? ORDER BY round DESC, created_at DESC LIMIT 1",
            (dw["id"],),
        )
        previous_actual_score_b = (
            _extract_actual_score_b(previous_review["score_breakdown_json"])
            if previous_review is not None
            else None
        )
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
                previous_actual_score_b=previous_actual_score_b,
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
        try:
            note_body = await self.registry.read_text(
                workspace_slug=ws["slug"],
                relative_path=note["markdown_path"],
            )
        except NotFoundError as exc:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP2_ITERATION,
                reason=f"Step5 iteration note unreadable: {exc}",
                problem_category=ProblemCategory.req_gap,
            )
            return
        plan_items = _extract_plan_checklist_items(note_body)

        findings_rel = (
            f"devworks/{dw['id']}/artifacts/step4-findings-round{round_n}.json"
        )
        review_rel = (
            f"devworks/{dw['id']}/artifacts/step5-review-round{round_n}.json"
        )
        review_abs = self._abs_for(ws, review_rel)

        preflight_reason = await self._preflight_step5_worktrees(dw)
        if preflight_reason is not None and preflight_reason.generated_paths:
            repaired = await self._repair_step5_generated_diff(
                dw,
                workspace_row=ws,
                round_n=round_n,
                generated_paths=preflight_reason.generated_paths,
            )
            if repaired:
                preflight_reason = await self._preflight_step5_worktrees(dw)
            if preflight_reason is not None and preflight_reason.generated_paths:
                await self._escalate(
                    dw,
                    reason=(
                        "Step5 generated/dependency diff repair failed: "
                        f"{preflight_reason.reason}"
                    ),
                    problem_category=ProblemCategory.impl_gap,
                )
                return
        if preflight_reason is not None:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP4_DEVELOP,
                reason=f"Step5 preflight failed: {preflight_reason.reason}",
                problem_category=ProblemCategory.impl_gap,
            )
            return

        changed_paths = await self._collect_step5_changed_paths(dw)
        previous_plan_ledger = await self._load_previous_plan_ledger(
            dw["id"], round_n,
        )
        touched_plan_ids = await self._load_step4_touched_plan_ids(
            workspace_row=ws,
            findings_rel=findings_rel,
        )
        plan_audit_targets = _render_step5_plan_audit_targets(
            plan_items=plan_items,
            previous_ledger=previous_plan_ledger,
            touched_plan_ids=touched_plan_ids,
            changed_paths=changed_paths,
        )

        mount_entries = await self._load_mount_table_entries(dw)
        retry_feedback = await self._loop_feedback_for_round(
            dw["id"], round_n, DevWorkStep.STEP5_REVIEW
        )
        previous_review = await self.db.fetchone(
            "SELECT score, score_breakdown_json FROM reviews "
            "WHERE dev_work_id=? AND round < ? "
            "ORDER BY round DESC, created_at DESC LIMIT 1",
            (dw["id"], round_n),
        )
        previous_actual_score_b = None
        if previous_review is not None:
            previous_actual_score_b = _extract_actual_score_b(
                previous_review["score_breakdown_json"]
            )
        if (
            previous_actual_score_b is None
            and previous_review is not None
            and previous_review["score"] is not None
        ):
            try:
                previous_actual_score_b = int(previous_review["score"])
            except (TypeError, ValueError):
                previous_actual_score_b = None

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
                previous_actual_score_b=previous_actual_score_b,
                retry_feedback=retry_feedback,
                plan_audit_targets=plan_audit_targets,
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
                stdout=stdout,
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
                    plan_audit_targets=plan_audit_targets,
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

        missing_plan_ids = _missing_plan_verification_ids(
            plan_items=plan_items,
            plan_verification=outcome.plan_verification,
        )
        if missing_plan_ids:
            await self._loop_or_escalate(
                dw,
                back_to=DevWorkStep.STEP5_REVIEW,
                reason=(
                    "Step5 plan_verification missing active plan ids: "
                    f"{_format_plan_sample(missing_plan_ids)}"
                ),
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

        if outcome.score >= rubric_threshold and outcome.problem_category is None:
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
