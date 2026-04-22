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

from src.exceptions import BadRequestError

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
    design_doc_text: str
    user_prompt: str
    # Raw previous-round feedback; empty string for round 1.  The composer
    # normalises empty strings to a human-readable marker so the LLM never
    # sees a blank section body.
    previous_feedback: str
    # Absolute path the LLM should append to.
    output_path: str


def compose_step2(inputs: Step2Inputs) -> str:
    prev = inputs.previous_feedback or "(首轮，无上轮反馈)"
    return _STEP2_TEMPLATE.safe_substitute(
        dev_work_id=inputs.dev_work_id,
        round=str(inputs.round),
        design_doc_text=inputs.design_doc_text,
        user_prompt=inputs.user_prompt,
        previous_feedback=prev,
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
# Step5 — Review / scoring prompt
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Step5Inputs:
    design_doc_text: str
    # Exact body of the design doc's ``## 打分 rubric`` section (E2).
    rubric_section_text: str
    iteration_note_text: str
    diff_text: str
    step4_findings_json: str
    rubric_threshold: int
    output_json_path: str


def compose_step5(inputs: Step5Inputs) -> str:
    if not inputs.rubric_section_text.strip():
        # Structural failure: design_doc lacks the rubric section.  The caller
        # (SM) should have caught this at Step1, but fail-fast if we slip past.
        raise BadRequestError(
            "design doc missing '## 打分 rubric' section — cannot compose Step5 prompt"
        )
    return _STEP5_TEMPLATE.safe_substitute(
        design_doc_text=inputs.design_doc_text,
        rubric_section_text=inputs.rubric_section_text,
        iteration_note_text=inputs.iteration_note_text,
        diff_text=inputs.diff_text,
        step4_findings_json=inputs.step4_findings_json,
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
