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


def _render_retry_feedback(feedback: str | None) -> str:
    if not feedback:
        return ""
    return (
        "\n## System retry feedback\n\n"
        "The previous attempt for this step failed validation. Fix these "
        "items before doing any other work:\n\n"
        f"{feedback.strip()}\n"
    )


# ---------------------------------------------------------------------------
# Step responsibility walls (Phase 5)
# ---------------------------------------------------------------------------
# Each wall is a fixed H2 block prepended to the corresponding STEP{N}
# template. The wording is load-bearing: Step5's _BOUNDARY_CHECK_RUBRIC
# names each step's "禁止" list verbatim, so any change here must mirror
# there. Keep wording short — these blocks ride on every prompt artifact.

_STEP_WALL_STEP2 = (
    "## 本步职责墙\n"
    "\n"
    "**单一职责**：基于设计文档 + 上轮反馈，决定「做什么、怎么验」。\n"
    "**唯一输出**：在 iteration-round-N.md 末尾追加三段 H2"
    "（本轮目标 / 开发计划 / 用例清单）。\n"
    "**明确禁止**：不扫代码、不写代码、不决定文件级实现路径、"
    "不修改 front-matter、不写入其它文件。\n"
    "**越界即失败**：违反任何上述禁止，本轮 Step5 将以 "
    "`kind=\"boundary_violation\"` 记录并影响打分。"
)

_STEP_WALL_STEP3 = (
    "## 本步职责墙\n"
    "\n"
    "**单一职责**：基于本轮迭代规划，扫 worktree 决定「在哪里改、"
    "有什么坑」，并探查与本轮改动可能关联的代码"
    "（调用方、相似实现、依赖链、相邻测试）。\n"
    "**唯一输出**：在 ctx-round-N.md 写入两段 H2"
    "（浓缩上下文 / 疑点风险）。\n"
    "**推荐做法**：可用一两句话简述相关代码逻辑；**强烈推荐**"
    "为每个相关位置附代码引用 `path/to/file.py:123-145`，"
    "Step4 据此精确定位。\n"
    "**明确禁止**：不写代码、不改设计文档、不重写迭代规划、"
    "不拷贝整段源代码到输出、不写入其它文件。\n"
    "**越界即失败**：违反任何上述禁止，本轮 Step5 将以 "
    "`kind=\"boundary_violation\"` 记录并影响打分。"
)

_STEP_WALL_STEP4 = (
    "## 本步职责墙\n"
    "\n"
    "**单一职责**：基于规划+上下文，写代码 + 跑既有 lint/typecheck/"
    "unit + 自审。\n"
    "**唯一输出**：worktree 源码改动（保留为未提交变更）+ "
    "step4-findings-roundN.json。\n"
    "**明确禁止**：不修改 iteration_note 文件、不修改 ctx 文件、"
    "不重新规划、不 `git commit`、不写入 `.coop/` 之外的诊断文件。\n"
    "**越界即失败**：违反任何上述禁止，本轮 Step5 将以 "
    "`kind=\"boundary_violation\"` 记录并影响打分。"
)

_STEP_WALL_STEP5 = (
    "## 本步职责墙\n"
    "\n"
    "**单一职责**：对标 rubric 评分 + 分类问题；同时识别本轮交付里"
    "**缺失的功能**与**可优化的代码**，作为下一轮补齐与优化的输入。\n"
    "**唯一输出**：step5-review-roundN.json。\n"
    "**明确禁止**：不修改源代码、不修改 step4 findings、"
    "不修改 iteration_note 或 ctx 文件、不重新规划、不写入其它文件。\n"
    "**越界即失败**：违反任何上述禁止视为 reviewer 失职，"
    "下游会据此重起独立 review session。"
)

# Procedural rubric appended to STEP5 — instructs reviewer to inspect
# whether other three steps respected their walls. Distinct from the
# design-doc-extracted ``## 打分 rubric``.
_BOUNDARY_CHECK_RUBRIC = (
    "## 越界检查（procedural rubric）\n"
    "\n"
    "除了基于设计文档 rubric 的内容质量评分外，**额外**检查本轮各 step "
    "是否守住了职责墙：\n"
    "\n"
    "1. **Step2 是否越界**：iteration-round-N.md 是否只追加了三段 H2、"
    "front-matter 与 H1 未被改动？是否包含了不该有的代码块或文件级"
    "实现路径？\n"
    "2. **Step3 是否越界**：ctx-round-N.md 是否只有两段 H2"
    "（浓缩上下文 / 疑点风险）？是否拷贝了整段源代码而非摘要？"
    "是否反向修改了设计文档或 iteration-round-N.md？\n"
    "3. **Step4 是否越界**：iteration-round-N.md / ctx-round-N.md "
    "是否被 Step4 改动？是否产生了 `.coop/` 之外的诊断文件？"
    "是否擅自 `git commit`？\n"
    "\n"
    "对每一条**越界**事实，向 `issues` 数组追加一条对象，**必须**带 "
    "`kind=\"boundary_violation\"` 与 `step` 字段，例如：\n"
    "\n"
    "```json\n"
    "{\"kind\": \"boundary_violation\", \"step\": \"step4\", "
    "\"severity\": \"error\", "
    "\"message\": \"Step4 修改了 iteration-round-2.md 的 H2 \\\"开发计划\\\""
    " 段落（diff 见 …）\"}\n"
    "```\n"
    "\n"
    "未发现越界时，**不要**追加占位条目；越界检查仅在确实命中时报告。"
)

# Forward-looking hints written to next_round_hints[] for Round N+1's
# Step2 to consume. Distinct from `issues` (which is backward-looking
# problems). Keeps `kind` enum tight: only "missing_feature" or
# "optimization" — extending the enum requires touching this constant
# AND the JSON schema example block in STEP5-review.md.tpl in lockstep.
_NEXT_ROUND_HINTS_GUIDE = (
    "## 下一轮提示（next_round_hints）\n"
    "\n"
    "除了对本轮的评分（`issues`）外，**额外**输出一组面向"
    "**下一轮**的提示，写入顶层数组 `next_round_hints`。\n"
    "\n"
    "每条提示是一个 JSON 对象：\n"
    "\n"
    "- `kind`：枚举，仅可取 `\"missing_feature\"`（设计/用户诉求覆盖到"
    "但本轮未实现的功能）或 `\"optimization\"`（已实现但代码可优化"
    "的位置）。\n"
    "- `mount`：可选。问题所在 mount 名（多仓任务时建议给出）。\n"
    "- `severity`：可选。`info` / `warn` / `error`，用于排序提示。\n"
    "- `message`：必填。一句话说明缺失功能 / 可优化点；推荐附代码引用 "
    "`path/to/file.py:123-145` 让下一轮 Step3/Step4 精确定位。\n"
    "\n"
    "本轮无缺失功能且代码无明显优化项时，输出空数组 `[]`，**不要**"
    "塞入占位条目。`next_round_hints` 与 `issues` 互不替代："
    "缺失功能不是错误，不应在 `issues` 中以 error 形式出现。"
)


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
        step_wall=_STEP_WALL_STEP2,
    )


# ---------------------------------------------------------------------------
# Mount table — shared across Step3 / Step4 / Step5 prompts (Phase 6)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MountTableEntry:
    mount_name: str
    repo_id: str
    role: str            # repos.role value, e.g. "backend"; "other" for NULL
    is_primary: bool
    base_branch: str
    devwork_branch: str
    # Phase 6: every mount carries its own worktree_path. ``None`` only for
    # legacy in-flight rows created before Phase 6 (see
    # :meth:`DevWorkStateMachine._load_mount_table_entries` docstring).
    worktree_path: str | None


def _render_mount_table(entries: tuple[MountTableEntry, ...]) -> str:
    if not entries:
        return "_(no repo_refs registered for this DevWork)_"
    header = (
        "| mount | repo_id | role | primary | base_branch | "
        "devwork_branch | worktree_path |\n"
        "|---|---|---|---|---|---|---|"
    )
    rows: list[str] = [header]
    for e in entries:
        primary_cell = "✅" if e.is_primary else ""
        loc = (
            e.worktree_path
            or "_(历史 DevWork — Phase 6 之前创建，无 per-mount worktree)_"
        )
        rows.append(
            f"| `{e.mount_name}` | `{e.repo_id}` | {e.role} | "
            f"{primary_cell} | `{e.base_branch}` | "
            f"`{e.devwork_branch}` | {loc} |"
        )
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Step3 — Context retrieval prompt
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Step3Inputs:
    worktree_path: str
    design_doc_path: str
    iteration_note_path: str
    output_path: str
    # Phase 6: full mount table so Step3 LLM sees every mount's worktree
    # (it may scan non-primary mounts for context, even though it only
    # writes ctx-round-N.md against the primary worktree).
    mount_table_entries: tuple[MountTableEntry, ...]


def compose_step3(inputs: Step3Inputs) -> str:
    return _STEP3_TEMPLATE.safe_substitute(
        worktree_path=inputs.worktree_path,
        design_doc_path=inputs.design_doc_path,
        iteration_note_path=inputs.iteration_note_path,
        output_path=inputs.output_path,
        mount_table=_render_mount_table(inputs.mount_table_entries),
        step_wall=_STEP_WALL_STEP3,
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
    # Phase 6: full mount table — Step4 LLM may write code into any mount's
    # worktree (multi-mount tasks). The primary ``worktree_path`` above is
    # only the default landing pad referenced in the prompt header.
    mount_table_entries: tuple[MountTableEntry, ...]
    retry_feedback: str | None = None


def compose_step4(inputs: Step4Inputs) -> str:
    return _STEP4_TEMPLATE.safe_substitute(
        worktree_path=inputs.worktree_path,
        iteration_note_path=inputs.iteration_note_path,
        context_path=inputs.context_path,
        findings_output_path=inputs.findings_output_path,
        mount_table=_render_mount_table(inputs.mount_table_entries),
        step_wall=_STEP_WALL_STEP4,
        retry_feedback=_render_retry_feedback(inputs.retry_feedback),
    )


# ---------------------------------------------------------------------------
# Step5 — Review / scoring prompt (Phase 8: path-based, multi-repo aware)
# ---------------------------------------------------------------------------

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
    retry_feedback: str | None = None


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
        aggregation_rule=_AGGREGATION_RULE,
        rubric_threshold=str(inputs.rubric_threshold),
        output_json_path=inputs.output_json_path,
        step_wall=_STEP_WALL_STEP5,
        boundary_check=_BOUNDARY_CHECK_RUBRIC,
        next_round_hints_guide=_NEXT_ROUND_HINTS_GUIDE,
        retry_feedback=_render_retry_feedback(inputs.retry_feedback),
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
