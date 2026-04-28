"""DevWork prompt composers — pure functions from inputs to prompt strings.

Each Step has its own frozen-dataclass input and a thin ``compose_*`` function
that renders a template under ``templates/STEP{2,3,4,5}-*.md.tpl`` via
``string.Template.safe_substitute``.  Kept pure so unit tests can assert on
exact output without spinning up the state machine.

Layout (Phase 4, PRD L184-189):
  * Step2  — LLM produces iteration-round-N.md body (F2=B)
  * Step3  — LLM-driven prompt-side context retrieval
  * Step4  — LLM implements the plan + self-reviews once
  * Step5  — LLM scores against the design rubric, emits JSON outcome
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from string import Template

_TPL_DIR = Path(__file__).resolve().parents[1] / "templates"
_STEP2_TPL = _TPL_DIR / "STEP2-iteration.md.tpl"
_STEP3_TPL = _TPL_DIR / "STEP3-context.md.tpl"
_STEP4_TPL = _TPL_DIR / "STEP4-develop.md.tpl"
_STEP5_TPL = _TPL_DIR / "STEP5-review.md.tpl"
_NOTE_HEADER_TPL = _TPL_DIR / "dev_iteration_note.header.tpl"

# Templates are read once at import and cached as compiled ``Template``
# instances. The hot path (Step2<->Step5 iteration loop) re-composes
# prompts every round, and hitting the filesystem each time is measurable
# overhead for no benefit — the template files never change at runtime.
_STEP2_TEMPLATE = Template(_STEP2_TPL.read_text(encoding="utf-8"))
_STEP3_TEMPLATE = Template(_STEP3_TPL.read_text(encoding="utf-8"))
_STEP4_TEMPLATE = Template(_STEP4_TPL.read_text(encoding="utf-8"))
_STEP5_TEMPLATE = Template(_STEP5_TPL.read_text(encoding="utf-8"))
_NOTE_HEADER_TEMPLATE = Template(_NOTE_HEADER_TPL.read_text(encoding="utf-8"))

# Human-readable placeholders the composer substitutes when an optional
# input path is missing. Grouped here so all "what-the-LLM-sees-when-empty"
# strings live in one place.
_PREV_REVIEW_PLACEHOLDER = "_(首轮，无上轮反馈 — 跳过此步)_"
_CONTEXT_PATH_PLACEHOLDER = "_(无 ctx 文件 — 本轮 Step3 未产出)_"


# ---------------------------------------------------------------------------
# Iteration note header (SM-owned, LLM-immutable prefix of round markdown)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IterationHeaderInputs:
    dev_work_id: str
    design_doc_path: str
    round: int
    created_at: str


def compose_iteration_header(inputs: IterationHeaderInputs) -> str:
    return _NOTE_HEADER_TEMPLATE.safe_substitute(
        dev_work_id=inputs.dev_work_id,
        design_doc_path=inputs.design_doc_path,
        round=str(inputs.round),
        created_at=inputs.created_at,
    )


# ---------------------------------------------------------------------------
# Step2 — Iteration design prompt (LLM appends three H2 sections)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Step2Inputs:
    dev_work_id: str
    round: int
    # Absolute POSIX path to the published design doc; the LLM Reads it itself.
    design_doc_path: str
    user_prompt: str
    # Absolute POSIX path to the previous-round review markdown; None on
    # round 1 (no prior review). The composer substitutes a placeholder
    # when None so the LLM never sees an empty path slot.
    previous_review_path: str | None
    # Absolute path the LLM should append to.
    output_path: str


def compose_step2(inputs: Step2Inputs) -> str:
    prev = inputs.previous_review_path or _PREV_REVIEW_PLACEHOLDER
    return _STEP2_TEMPLATE.safe_substitute(
        dev_work_id=inputs.dev_work_id,
        round=str(inputs.round),
        design_doc_path=inputs.design_doc_path,
        user_prompt=inputs.user_prompt,
        previous_review_path=prev,
        output_path=inputs.output_path,
    )


# ---------------------------------------------------------------------------
# Step3 — Context retrieval prompt
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Step3Inputs:
    worktree_path: str
    design_doc_path: str
    iteration_note_path: str
    output_path: str


def compose_step3(inputs: Step3Inputs) -> str:
    return _STEP3_TEMPLATE.safe_substitute(
        worktree_path=inputs.worktree_path,
        design_doc_path=inputs.design_doc_path,
        iteration_note_path=inputs.iteration_note_path,
        output_path=inputs.output_path,
    )


# ---------------------------------------------------------------------------
# Step4 — Develop + self-review prompt
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Step4Inputs:
    worktree_path: str
    iteration_note_path: str
    context_path: str
    findings_output_path: str


def compose_step4(inputs: Step4Inputs) -> str:
    return _STEP4_TEMPLATE.safe_substitute(
        worktree_path=inputs.worktree_path,
        iteration_note_path=inputs.iteration_note_path,
        context_path=inputs.context_path,
        findings_output_path=inputs.findings_output_path,
    )


# ---------------------------------------------------------------------------
# Step5 — Review / scoring prompt (Phase 8: path-based, multi-repo aware)
# ---------------------------------------------------------------------------

# Honest description of the current execution gap: only the primary mount has
# a local worktree on this server (the multi-mount worker described by the
# repo-registry PRD is not yet built). Embedded as the single source of truth
# so tests and template render the exact same wording.
_BTRACK_LIMITATION_NOTE = (
    "⚠️ 当前实现限制（B-track）：仅 primary mount 在本机有 git "
    "worktree；非 primary mount 暂未在本机产生代码改动（多仓 worker "
    "由后续 PRD 上线）。"
    "评分时 — primary mount 在其 worktree_path 下 `git diff HEAD` "
    "查看本轮改动；非 primary mount 仅基于 Step4 findings 中该 mount "
    "的条目判断；无相关 finding 视为本轮该仓无问题。"
)

# Aggregation rule order (`design_hollow > req_gap > impl_gap > null`) is
# load-bearing: it matches how the SM routes those categories
# (escalate / loop-Step2 / loop-Step4 / COMPLETED). Keep duplicated assertions
# in tests/test_dev_prompt_composer.py in sync when wording is tuned.
_AGGREGATION_RULE = """\
按以下优先级聚合，**最严重的 category 取胜**：

1. **设计文档本身缺乏可评估内容**支撑本次多仓任务 → `problem_category="design_hollow"`
2. 否则**任一仓**的迭代设计/开发计划/用例清单与设计文档或用户诉求有缺口 → `problem_category="req_gap"`
3. 否则**任一仓**存在实现/测试/代码回归（lint 失败、测试失败、代码与计划不符） → `problem_category="impl_gap"`
4. 否则全部通过阈值 → `problem_category=null` 且 `score >= $rubric_threshold`
"""


@dataclass(frozen=True)
class MountTableEntry:
    mount_name: str
    repo_id: str
    role: str            # repos.role value, e.g. "backend"; "other" for NULL
    is_primary: bool
    base_branch: str
    devwork_branch: str
    worktree_path: str | None  # set only for primary; None for others


def _render_mount_table(entries: tuple[MountTableEntry, ...]) -> str:
    if not entries:
        return "_(no repo_refs registered for this DevWork)_"
    header = (
        "| mount | repo_id | role | primary | base_branch | "
        "devwork_branch | worktree_path / 备注 |\n"
        "|---|---|---|---|---|---|---|"
    )
    rows: list[str] = [header]
    for e in entries:
        primary_cell = "✅" if e.is_primary else ""
        loc = e.worktree_path or "_(无本地 worktree — 多仓 worker 待上线)_"
        rows.append(
            f"| `{e.mount_name}` | `{e.repo_id}` | {e.role} | "
            f"{primary_cell} | `{e.base_branch}` | "
            f"`{e.devwork_branch}` | {loc} |"
        )
    return "\n".join(rows)


@dataclass(frozen=True)
class Step5Inputs:
    design_doc_path: str
    iteration_note_path: str
    step4_findings_path: str
    # Absolute POSIX path to the round's ctx-round-N.md; reviewer reads it
    # to verify Step3-raised concerns were addressed in Step4. None falls
    # back to a human-readable placeholder.
    context_path: str | None
    mount_table_entries: tuple[MountTableEntry, ...]
    primary_worktree_path: str | None
    rubric_threshold: int
    output_json_path: str


def compose_step5(inputs: Step5Inputs) -> str:
    return _STEP5_TEMPLATE.safe_substitute(
        design_doc_path=inputs.design_doc_path,
        iteration_note_path=inputs.iteration_note_path,
        step4_findings_path=inputs.step4_findings_path,
        context_path=(inputs.context_path or _CONTEXT_PATH_PLACEHOLDER),
        mount_table=_render_mount_table(inputs.mount_table_entries),
        primary_worktree_path=(
            inputs.primary_worktree_path or "_(no primary worktree)_"
        ),
        btrack_limitation=_BTRACK_LIMITATION_NOTE,
        aggregation_rule=_AGGREGATION_RULE,
        rubric_threshold=str(inputs.rubric_threshold),
        output_json_path=inputs.output_json_path,
    )


# ---------------------------------------------------------------------------
# Rubric section extractor (used by SM to build Step5Inputs)
# ---------------------------------------------------------------------------

_RUBRIC_RE = re.compile(r"##\s*打分 rubric\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL)


def extract_rubric_section(design_doc_text: str) -> str:
    """Return the body between ``## 打分 rubric`` and the next ``## `` (or EOF).

    Empty string when the section is missing — the SM interprets that as a
    structural failure (``design_hollow``) rather than a prompt composition
    bug.
    """
    m = _RUBRIC_RE.search(design_doc_text)
    return m.group(1).strip() if m else ""
