"""Phase 4 + Phase 8: dev_prompt_composer unit tests."""
from __future__ import annotations

from src.dev_prompt_composer import (
    IterationHeaderInputs,
    MountTableEntry,
    Step2Inputs,
    Step3Inputs,
    Step4Inputs,
    Step5Inputs,
    _BTRACK_LIMITATION_NOTE,
    compose_iteration_header,
    compose_step2,
    compose_step3,
    compose_step4,
    compose_step5,
    extract_rubric_section,
)


def test_iteration_header_has_frontmatter_and_h1():
    out = compose_iteration_header(IterationHeaderInputs(
        dev_work_id="dev-abc", design_doc_path="/tmp/d.md",
        round=1, created_at="2026-04-22T00:00:00",
    ))
    assert out.startswith("---")
    assert "dev_work_id: dev-abc" in out
    assert "round: 1" in out
    assert "# 迭代设计 — Round 1" in out


def test_step2_round1_substitutes_prev_review_placeholder():
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-1", round=1,
        design_doc_path="/ws/foo/designs/d.md",
        user_prompt="PROMPT",
        previous_review_path=None,
        output_path="/tmp/x.md",
    ))
    assert "/ws/foo/designs/d.md" in out
    assert "PROMPT" in out
    assert "首轮，无上轮反馈" in out
    # Path-based: the design body is NOT inlined.
    assert "DESIGN" not in out


def test_step2_round_n_uses_previous_review_path():
    prev = "/ws/foo/devworks/dev-x/feedback/feedback-for-round2.md"
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-x", round=2,
        design_doc_path="/ws/foo/designs/d.md",
        user_prompt="P",
        previous_review_path=prev,
        output_path="/o.md",
    ))
    assert prev in out
    assert "首轮，无上轮反馈" not in out


def test_step2_prompt_does_not_embed_design_body():
    # Realistic-ish path lengths; rendered size must stay small AND the
    # design-doc body must not be inlined. Sentinel string check guards
    # against accidental future regressions to body-embedding.
    design_path = "/ws/myworkspace/designs/some-feature/login-and-oauth.md"
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-abcdef", round=3,
        design_doc_path=design_path,
        user_prompt="implement OAuth flow with PKCE",
        previous_review_path=(
            "/ws/myworkspace/devworks/dev-abcdef/feedback/"
            "feedback-for-round3.md"
        ),
        output_path=(
            "/ws/myworkspace/devworks/dev-abcdef/iterations/"
            "iteration-round-3.md"
        ),
    ))
    assert len(out.encode("utf-8")) <= 3 * 1024
    # The composer must reference the design doc by path, never by body.
    assert design_path in out
    # Markers that would only appear if the design body got inlined.
    assert "## 评审标准" not in out
    assert "---\nslug:" not in out


def test_step2_preserves_literal_dollar_signs():
    # safe_substitute must not blow up on $ in user content.
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-1", round=2,
        design_doc_path="/d.md",
        user_prompt="cost is $5",
        previous_review_path="/p.md",
        output_path="/tmp/x.md",
    ))
    assert "$5" in out


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


def test_step5_renders_paths_only():
    ctx = "/ws/foo/devworks/dev-x/context/ctx-round-1.md"
    out = compose_step5(Step5Inputs(
        design_doc_path="/ws/foo/designs/d.md",
        iteration_note_path="/ws/foo/devworks/dev-x/iteration-round-1.md",
        step4_findings_path=(
            "/ws/foo/devworks/dev-x/artifacts/step4-findings-round1.json"
        ),
        context_path=ctx,
        mount_table_entries=(
            MountTableEntry(
                mount_name="backend", repo_id="repo-bbb",
                role="backend", is_primary=True,
                base_branch="main",
                devwork_branch="devwork/ws-foo/aaa111111111",
                worktree_path="/ws/foo/.coop/worktrees/devwork-ws-foo-aaa",
            ),
            MountTableEntry(
                mount_name="frontend", repo_id="repo-aaa",
                role="frontend", is_primary=False,
                base_branch="main",
                devwork_branch="devwork/ws-foo/aaa111111111",
                worktree_path=None,
            ),
        ),
        primary_worktree_path=(
            "/ws/foo/.coop/worktrees/devwork-ws-foo-aaa"
        ),
        rubric_threshold=85,
        output_json_path=(
            "/ws/foo/devworks/dev-x/artifacts/step5-review-round1.json"
        ),
    ))
    # Paths present
    assert "/ws/foo/designs/d.md" in out
    assert "iteration-round-1.md" in out
    assert "step4-findings-round1.json" in out
    assert ctx in out
    # Mount table renders both rows
    assert "| `backend` |" in out
    assert "| `frontend` |" in out
    assert "✅" in out  # primary marked
    # frontend B-track marker (no local worktree)
    assert "_(无本地 worktree — 多仓 worker 待上线)_" in out
    # Limitation note + aggregation rule + threshold
    assert _BTRACK_LIMITATION_NOTE in out
    # Aggregation rule wording is asserted by a stable substring (the
    # constant interpolates ``$rubric_threshold`` at compose time, so
    # asserting on the raw constant would not match the rendered output).
    assert "**最严重的 category 取胜**" in out
    assert "design_hollow" in out
    assert "req_gap" in out
    assert "impl_gap" in out
    assert "85" in out
    # No embedded content from the previous embed-everything layout.
    assert "## 设计文档" not in out
    assert "## 本轮 diff" not in out


def test_step5_with_no_mounts_falls_back_to_marker():
    out = compose_step5(Step5Inputs(
        design_doc_path="/d", iteration_note_path="/n",
        step4_findings_path="/f", context_path="/c.md",
        mount_table_entries=(),
        primary_worktree_path=None, rubric_threshold=85,
        output_json_path="/s",
    ))
    assert "no repo_refs registered for this DevWork" in out
    # primary_worktree_path falls back to the human-readable placeholder.
    assert "_(no primary worktree)_" in out


def test_step5_context_path_none_uses_placeholder():
    out = compose_step5(Step5Inputs(
        design_doc_path="/d", iteration_note_path="/n",
        step4_findings_path="/f", context_path=None,
        mount_table_entries=(),
        primary_worktree_path=None, rubric_threshold=85,
        output_json_path="/s",
    ))
    assert "无 ctx 文件" in out


def test_step5_aggregation_priority_order_in_template():
    out = compose_step5(Step5Inputs(
        design_doc_path="/d", iteration_note_path="/n",
        step4_findings_path="/f", context_path="/c.md",
        mount_table_entries=(),
        primary_worktree_path=None, rubric_threshold=85,
        output_json_path="/s",
    ))
    # design_hollow MUST appear before req_gap MUST appear before impl_gap.
    assert (
        out.index("design_hollow")
        < out.index("req_gap")
        < out.index("impl_gap")
    )


def test_step5_no_longer_raises_on_empty_rubric_section():
    """Phase 8: rubric pre-flight moved out of the composer into the SM."""
    out = compose_step5(Step5Inputs(
        design_doc_path="/d", iteration_note_path="/n",
        step4_findings_path="/f", context_path="/c.md",
        mount_table_entries=(),
        primary_worktree_path=None, rubric_threshold=80,
        output_json_path="/s",
    ))
    # Composes a (degenerate) prompt without raising.
    assert "/d" in out


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
