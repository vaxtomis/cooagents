"""Phase 4: dev_prompt_composer unit tests."""
from __future__ import annotations

import pytest

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


def test_iteration_header_has_frontmatter_and_h1():
    out = compose_iteration_header(IterationHeaderInputs(
        dev_work_id="dev-abc", design_doc_path="/tmp/d.md",
        round=1, created_at="2026-04-22T00:00:00",
    ))
    assert out.startswith("---")
    assert "dev_work_id: dev-abc" in out
    assert "round: 1" in out
    assert "# 迭代设计 — Round 1" in out


def test_step2_round1_maps_empty_feedback():
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-1", round=1,
        design_doc_text="DESIGN", user_prompt="PROMPT",
        previous_feedback="", output_path="/tmp/x.md",
    ))
    assert "(首轮，无上轮反馈)" in out
    assert "DESIGN" in out
    assert "PROMPT" in out


def test_step2_preserves_literal_dollar_signs():
    # safe_substitute must not blow up on $ in user content.
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-1", round=2,
        design_doc_text="has $var literal",
        user_prompt="cost is $5",
        previous_feedback="issue: $critical",
        output_path="/tmp/x.md",
    ))
    assert "$5" in out
    assert "$var" in out


def test_step3_prompt_includes_paths():
    out = compose_step3(Step3Inputs(
        worktree_path="/wt", design_doc_path="/d.md",
        iteration_note_path="/n.md", output_path="/o.md",
    ))
    assert "/wt" in out and "/d.md" in out and "/n.md" in out and "/o.md" in out


def test_step4_prompt_includes_findings_path():
    out = compose_step4(Step4Inputs(
        worktree_path="/wt", iteration_note_path="/n.md",
        context_path="/c.md", findings_output_path="/f.json",
    ))
    assert "/f.json" in out


def test_step5_prompt_injects_rubric_and_threshold():
    out = compose_step5(Step5Inputs(
        design_doc_text="D",
        rubric_section_text="| 项 | 权重 |\n|---|---|",
        iteration_note_text="N", diff_text="diff",
        step4_findings_json="{}", rubric_threshold=85,
        output_json_path="/s.json",
    ))
    assert "| 项 | 权重 |" in out
    assert "85" in out
    assert "/s.json" in out


def test_step5_fails_fast_on_missing_rubric():
    with pytest.raises(BadRequestError):
        compose_step5(Step5Inputs(
            design_doc_text="D", rubric_section_text="",
            iteration_note_text="", diff_text="", step4_findings_json="{}",
            rubric_threshold=80, output_json_path="/s.json",
        ))


def test_extract_rubric_section_parses():
    text = (
        "## 验收标准\n\n- foo\n\n"
        "## 打分 rubric\n\n| A | B |\n|---|---|\n| x | y |\n\n"
        "## 下一章\n\nnope\n"
    )
    body = extract_rubric_section(text)
    assert "| A | B |" in body
    assert "下一章" not in body


def test_extract_rubric_section_missing_returns_empty():
    assert extract_rubric_section("no rubric at all") == ""
