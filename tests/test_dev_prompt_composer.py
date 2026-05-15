"""Phase 4 + Phase 8: dev_prompt_composer unit tests."""
from __future__ import annotations

from src.dev_prompt_composer import (
    IterationHeaderInputs,
    MountTableEntry,
    Step2Inputs,
    Step3Inputs,
    Step4Inputs,
    Step5Inputs,
    _BOUNDARY_CHECK_RUBRIC,
    _CONTEXT_COMPLETENESS_GUIDE,
    _NEXT_ROUND_HINTS_GUIDE,
    _PLAN_VERIFICATION_GUIDE,
    _STEP_WALL_STEP2,
    _STEP_WALL_STEP3,
    _STEP_WALL_STEP4,
    _STEP_WALL_STEP5,
    compose_iteration_header,
    compose_step2,
    compose_step3,
    compose_step4,
    compose_step5,
    extract_rubric_section,
)


def _step5_minimal() -> Step5Inputs:
    return Step5Inputs(
        design_doc_path="/d", iteration_note_path="/n",
        step4_findings_path="/f", context_path="/c.md",
        mount_table_entries=(),
        primary_worktree_path=None, rubric_threshold=85,
        output_json_path="/s",
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
    assert "首轮，无上一轮迭代设计" in out
    # Path-based: the design body is NOT inlined.
    assert "DESIGN" not in out


def test_step2_round_n_uses_previous_review_path():
    prev = "/ws/foo/devworks/dev-x/feedback/feedback-for-round2.md"
    prev_note = "/ws/foo/devworks/dev-x/iteration-round-1.md"
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-x", round=2,
        design_doc_path="/ws/foo/designs/d.md",
        user_prompt="P",
        previous_review_path=prev,
        previous_iteration_note_path=prev_note,
        output_path="/o.md",
    ))
    assert prev in out
    assert prev_note in out
    assert "首轮，无上轮反馈" not in out
    assert "首轮，无上一轮迭代设计" not in out


def test_step2_round1_prefers_coarse_main_plan_framework():
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-x", round=1,
        design_doc_path="/ws/foo/designs/d.md",
        user_prompt="P",
        previous_review_path=None,
        output_path="/ws/foo/devworks/dev-x/iteration-round-1.md",
    ))
    assert "Round 1 优先按设计文档与上下文发现拆粗粒度主 PLAN" in out
    assert "只写顶层 DW-xx" in out
    assert "覆盖需求/流程/验收面" in out
    assert "默认不展开大量子 PLAN" in out
    assert "[P0|P1|P2]" in out
    assert "P0=准出必需" in out
    assert "P2=可延期" in out


def test_step2_round_n_requires_cumulative_plan_append_or_refine():
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-x", round=3,
        design_doc_path="/ws/foo/designs/d.md",
        user_prompt="P",
        previous_review_path="/ws/foo/devworks/dev-x/feedback/feedback-for-round3.md",
        previous_iteration_note_path="/ws/foo/devworks/dev-x/iteration-round-2.md",
        output_path="/ws/foo/devworks/dev-x/iteration-round-3.md",
    ))
    assert "必须 Read 此文件并继承其中 `## 开发计划`" in out
    assert "必须保留所有历史 PLAN" in out
    assert "必要、未重复、有交付价值" in out
    assert "不要新增主 PLAN，优先沿用原 ID" in out
    assert "不得用不同措辞重复同一验收点" in out
    assert "追加遗漏主 PLAN" in out
    assert "细粒度子 PLAN" in out
    assert "plan_score_a >= 90" in out
    assert "谨慎新增和细化计划" in out
    assert "不得新增主 PLAN" in out
    assert "plan_score_a <= 70" in out
    assert "鼓励新增和细化计划" in out
    assert "主动补齐遗漏主 PLAN" in out
    assert "追加缩进子 PLAN" in out
    assert "DW-02.1" in out
    assert "- [ ] ~~DW-02:" in out


def test_step2_includes_recommended_tech_stack_when_provided():
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-1", round=1,
        design_doc_path="/ws/foo/designs/d.md",
        user_prompt="P",
        previous_review_path=None,
        output_path="/tmp/x.md",
        recommended_tech_stack="React 18, Vite, FastAPI",
    ))
    assert "React 18, Vite, FastAPI" in out
    assert "`## 推荐技术栈`" in out
    assert "尽量包含这些组件" in out
    assert "不是排他约束" in out
    assert "以下 五 个 H2" in out


def test_step2_omits_recommended_tech_stack_section_by_default():
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-1", round=1,
        design_doc_path="/ws/foo/designs/d.md",
        user_prompt="P",
        previous_review_path=None,
        output_path="/tmp/x.md",
    ))
    assert "`## 推荐技术栈`" not in out
    assert "人工推荐技术栈" not in out
    assert "以下 四 个 H2" in out
    assert "`## 上下文发现`" in out


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
    assert len(out.encode("utf-8")) <= 32 * 1024
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


def test_step2_omits_user_prompt_item_when_execution_prompt_omitted():
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-1", round=1,
        design_doc_path="/d.md",
        user_prompt="   ",
        previous_review_path=None,
        output_path="/tmp/x.md",
    ))
    assert "**用户 prompt**" not in out
    assert "未提供执行提示" not in out


def _two_mount_entries() -> tuple[MountTableEntry, ...]:
    """Phase 6: shared two-mount fixture for Step3/Step4/Step5 tests."""
    return (
        MountTableEntry(
            mount_name="backend", repo_id="repo-bbb",
            role="backend", is_primary=True,
            base_branch="main",
            devwork_branch="devwork/ws-foo/aaa111111111",
            worktree_path="/ws/foo/.coop/worktrees/devwork-ws-foo-aaa/backend",
        ),
        MountTableEntry(
            mount_name="frontend", repo_id="repo-aaa",
            role="frontend", is_primary=False,
            base_branch="main",
            devwork_branch="devwork/ws-foo/aaa111111111",
            worktree_path="/ws/foo/.coop/worktrees/devwork-ws-foo-aaa/frontend",
        ),
    )


def test_step2_prompt_includes_readonly_worktree_and_mount_table():
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-1", round=1,
        design_doc_path="/ws/foo/designs/d.md",
        user_prompt="P",
        previous_review_path=None,
        output_path="/tmp/x.md",
        worktree_path="/wt-primary",
        mount_table_entries=_two_mount_entries(),
    ))
    assert "默认只读探查 worktree：`/wt-primary`" in out
    assert "## 多仓改动表" in out
    assert "| `backend` |" in out
    assert "| `frontend` |" in out
    assert "只读扫描 worktree" in out
    assert "不写代码、不修改任何文件" in out
    assert "不扫代码" not in out
    assert "接口/类型" in out
    assert "验证命令候选" in out


def test_step3_prompt_includes_paths():
    out = compose_step3(Step3Inputs(
        worktree_path="/wt", design_doc_path="/d.md",
        iteration_note_path="/n.md", output_path="/o.md",
        mount_table_entries=(),
    ))
    assert "/wt" in out and "/d.md" in out and "/n.md" in out and "/o.md" in out


def test_step3_prompt_includes_mount_table():
    out = compose_step3(Step3Inputs(
        worktree_path="/wt", design_doc_path="/d.md",
        iteration_note_path="/n.md", output_path="/o.md",
        mount_table_entries=_two_mount_entries(),
    ))
    assert "## 多仓改动表" in out
    assert "| `backend` |" in out
    assert "| `frontend` |" in out
    assert "/ws/foo/.coop/worktrees/devwork-ws-foo-aaa/backend" in out
    assert "/ws/foo/.coop/worktrees/devwork-ws-foo-aaa/frontend" in out


def test_step4_prompt_includes_findings_path():
    out = compose_step4(Step4Inputs(
        worktree_path="/wt", iteration_note_path="/n.md",
        context_path="/c.md", findings_output_path="/f.json",
        mount_table_entries=(),
    ))
    assert "/f.json" in out
    assert "不要 `git commit` / `git push`" in out
    assert "退出前检查" in out
    assert "不要只把 JSON 打印到 stdout" in out
    assert "建议探测包管理器和既有 lint / typecheck / 单元测试脚本" in out
    assert "未运行建议测试时" in out


def test_step4_prompt_includes_high_b_execution_strategy():
    out = compose_step4(Step4Inputs(
        worktree_path="/wt", iteration_note_path="/n.md",
        context_path="/c.md", findings_output_path="/f.json",
        mount_table_entries=(),
        previous_actual_score_b=80,
    ))
    assert "上一轮 `actual_score_b`=80" in out
    assert "优先优化高优先级计划" in out
    assert "required_for_exit=true" in out
    assert "actual_score_b >= 80" in out


def test_step4_prompt_includes_low_b_execution_strategy():
    out = compose_step4(Step4Inputs(
        worktree_path="/wt", iteration_note_path="/n.md",
        context_path="/c.md", findings_output_path="/f.json",
        mount_table_entries=(),
        previous_actual_score_b=79,
    ))
    assert "上一轮 `actual_score_b`=79" in out
    assert "优先实现未实现的开发计划" in out
    assert "P0/P1 主流程和阻断缺口" in out
    assert "actual_score_b < 80" in out


def test_step4_prompt_includes_mount_table():
    out = compose_step4(Step4Inputs(
        worktree_path="/wt", iteration_note_path="/n.md",
        context_path="/c.md", findings_output_path="/f.json",
        mount_table_entries=_two_mount_entries(),
    ))
    assert "## 多仓改动表" in out
    assert "| `backend` |" in out
    assert "| `frontend` |" in out
    assert "/ws/foo/.coop/worktrees/devwork-ws-foo-aaa/backend" in out
    assert "/ws/foo/.coop/worktrees/devwork-ws-foo-aaa/frontend" in out


def test_step5_renders_paths_only():
    ctx = "/ws/foo/devworks/dev-x/context/ctx-round-1.md"
    out = compose_step5(Step5Inputs(
        design_doc_path="/ws/foo/designs/d.md",
        iteration_note_path="/ws/foo/devworks/dev-x/iteration-round-1.md",
        step4_findings_path=(
            "/ws/foo/devworks/dev-x/artifacts/step4-findings-round1.json"
        ),
        context_path=ctx,
        mount_table_entries=_two_mount_entries(),
        primary_worktree_path=(
            "/ws/foo/.coop/worktrees/devwork-ws-foo-aaa/backend"
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
    # Mount table renders both rows with their per-mount paths (Phase 6).
    assert "| `backend` |" in out
    assert "| `frontend` |" in out
    assert "✅" in out  # primary marked
    assert "/ws/foo/.coop/worktrees/devwork-ws-foo-aaa/backend" in out
    assert "/ws/foo/.coop/worktrees/devwork-ws-foo-aaa/frontend" in out
    # Phase 6: B-track placeholder + limitation note are gone.
    assert "_(无本地 worktree — 多仓 worker 待上线)_" not in out
    assert "B-track" not in out
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


def test_step5_no_btrack_limitation_note():
    """Phase 6: B-track limitation note + per-mount placeholder are gone."""
    out = compose_step5(_step5_minimal())
    assert "B-track" not in out
    assert "多仓 worker 待上线" not in out
    assert "btrack_limitation" not in out  # template variable not leaked


def test_step5_legacy_in_flight_row_renders_placeholder():
    """Phase 6: rows with worktree_path=None still render gracefully."""
    out = compose_step5(Step5Inputs(
        design_doc_path="/d", iteration_note_path="/n",
        step4_findings_path="/f", context_path="/c.md",
        mount_table_entries=(
            MountTableEntry(
                mount_name="backend", repo_id="repo-x", role="backend",
                is_primary=True, base_branch="main",
                devwork_branch="devwork/legacy/aaa",
                worktree_path=None,
            ),
        ),
        primary_worktree_path=None, rubric_threshold=85,
        output_json_path="/s",
    ))
    assert "历史 DevWork — Phase 6 之前创建" in out


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


# ---------------------------------------------------------------------------
# Phase 5 — step responsibility walls + boundary check + next_round_hints
# ---------------------------------------------------------------------------


def test_step2_prompt_carries_step_wall():
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-1", round=1,
        design_doc_path="/d.md", user_prompt="P",
        previous_review_path=None, output_path="/o.md",
    ))
    assert _STEP_WALL_STEP2 in out
    assert out.index("## 本步职责墙") < out.index("## 必读路径")
    assert "boundary_violation" in out
    assert "- [ ] DW-01" in out
    assert "checkbox checklist" in out


def test_step3_prompt_carries_step_wall():
    out = compose_step3(Step3Inputs(
        worktree_path="/wt", design_doc_path="/d.md",
        iteration_note_path="/n.md", output_path="/o.md",
        mount_table_entries=(),
    ))
    assert _STEP_WALL_STEP3 in out
    assert out.index("## 本步职责墙") < out.index("## 必读路径")
    assert "path/to/file.py:123-145" in out
    assert "推荐做法" in out
    assert "模式镜像" in out
    assert "执行地图" in out
    assert "`## 疑点与风险` ——" not in out


def test_step4_prompt_carries_step_wall():
    out = compose_step4(Step4Inputs(
        worktree_path="/wt", iteration_note_path="/n.md",
        context_path="/c.md", findings_output_path="/f.json",
        mount_table_entries=(),
    ))
    assert _STEP_WALL_STEP4 in out
    assert out.index("## 本步职责墙") < out.index("## 工作树")
    assert "不勾选开发计划 checkbox" in out
    assert "不修改 ctx 文件" in out
    assert "plan_execution" in out
    assert "任务过大" in out
    assert ".gitignore" in out
    assert "node_modules/" in out
    assert "gitignore_maintenance" in out


def test_step5_prompt_carries_step_wall():
    out = compose_step5(_step5_minimal())
    assert _STEP_WALL_STEP5 in out
    assert out.index("## 本步职责墙") < out.index("## 必读顺序")
    assert "缺失的功能" in out
    assert "可优化的代码" in out


def test_step5_prompt_carries_boundary_check_rubric():
    out = compose_step5(_step5_minimal())
    assert _BOUNDARY_CHECK_RUBRIC in out
    assert (
        out.index("## 越界检查")
        > out.index("**最严重的 category 取胜**")
    )
    assert out.index("## 越界检查") < out.index("## 输出要求")
    assert "\"kind\": \"boundary_violation\"" in out
    assert "\"step\": \"step4\"" in out
    assert "擅自勾选开发计划 checkbox" in out
    assert "只读" in out
    assert ".gitignore" in out
    assert "缺少 `## 疑点与风险` 不算缺节" in out


def test_step5_prompt_carries_context_completeness_check():
    out = compose_step5(_step5_minimal())
    assert _CONTEXT_COMPLETENESS_GUIDE in out
    assert "## 上下文完整性检查" in out
    assert "No Prior Knowledge Test" in out
    assert "降低 `plan_score_a`" in out
    assert out.index("## 上下文完整性检查") > out.index("## 越界检查")
    assert out.index("## 上下文完整性检查") < out.index("## 计划执行核验")


def test_step5_prompt_carries_score_formula():
    out = compose_step5(_step5_minimal())
    assert "把设计文档完全满足定义为 100 分" in out
    assert "相对设计文档最多能拿多少分" in out
    assert "预期可实现分值 `a`" in out
    assert "实际实现分值 `b`" in out
    assert "当前实现相对开发计划的完成分" in out
    assert "actual_score_b` 不再等于顶层 `score`" in out
    assert "`a / 100`" in out
    assert "`b / 100`" in out
    assert "score = round(plan_score_a * actual_score_b / 100)" in out
    assert "required_for_exit=true" in out
    assert "P2" in out
    assert "不阻断准出" in out
    assert "存在重大不满足点时必须扣分" in out
    assert "problem_category=null" in out
    assert "score >= 85" in out
    assert "$rubric_threshold" not in out
    assert out.index("## 最终分数制定规则") > out.index("## 打分聚合规则")
    assert out.index("## 最终分数制定规则") < out.index("## 越界检查")


def test_step5_prompt_carries_previous_b_when_available():
    out = compose_step5(Step5Inputs(
        design_doc_path="/d", iteration_note_path="/n",
        step4_findings_path="/f", context_path="/c.md",
        mount_table_entries=(),
        primary_worktree_path=None, rubric_threshold=85,
        output_json_path="/s", previous_actual_score_b=72,
    ))
    assert "上一轮实际实现分值 `b`：72" in out
    assert "通常应高于上一轮" in out


def test_step5_prompt_carries_plan_verification_guide():
    out = compose_step5(_step5_minimal())
    assert _PLAN_VERIFICATION_GUIDE in out
    assert out.index("## 计划执行核验") > out.index("## 越界检查")
    assert out.index("## 计划执行核验") < out.index("## 下一轮提示")
    assert out.index("## 计划审查目标") < out.index("## 下一轮提示")
    assert "\"plan_verification\":" in out
    assert "\"implemented\": true" in out
    assert "\"verified\": true" in out
    assert "\"importance\": \"P0\"" in out
    assert "\"required_for_exit\": true" in out
    assert "implemented` 未显式为 false 的项回写 checkbox" in out
    assert "`verified` 影响评分和后续补证" in out
    assert "必须覆盖下方" in out
    assert "verification_mode=\"carried_forward\"" in out


def test_step5_prompt_carries_plan_audit_targets():
    out = compose_step5(Step5Inputs(
        design_doc_path="/d", iteration_note_path="/n",
        step4_findings_path="/f", context_path="/c.md",
        mount_table_entries=(),
        primary_worktree_path=None, rubric_threshold=85,
        output_json_path="/s",
        plan_audit_targets=(
            "## 计划审查目标（系统轻量预筛）\n\n"
            "| ID | 审查模式 |\n|---|---|\n| `DW-01` | `must_review` |"
        ),
    ))
    assert "| `DW-01` | `must_review` |" in out


def test_step5_prompt_carries_next_round_hints_guide():
    out = compose_step5(_step5_minimal())
    assert _NEXT_ROUND_HINTS_GUIDE in out
    # Hints guide must come AFTER boundary check and BEFORE output spec.
    assert out.index("## 下一轮提示") > out.index("## 计划执行核验")
    assert out.index("## 下一轮提示") < out.index("## 输出要求")
    # JSON example shows the new top-level field.
    assert "\"next_round_hints\":" in out
    assert "\"missing_feature\"" in out
    assert "\"optimization\"" in out


# ---------------------------------------------------------------------------
# Phase 8 — template rewrite size budgets + dropped-section sentinels
# ---------------------------------------------------------------------------
# Budgets are post-substitution rendered byte sizes on a realistic-but-
# minimal input. They lock in the Phase-8 compaction so a future edit
# that re-bloats a template trips a single, well-named test rather than
# a vague success-metric check at the end of Phase 9.


def test_step2_rendered_size_budget():
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-x", round=1,
        design_doc_path="/ws/foo/designs/d.md",
        user_prompt="P",
        previous_review_path=None,
        output_path="/ws/foo/devworks/dev-x/iterations/iteration-round-1.md",
    ))
    # Wall + path-based skeleton + cumulative-plan instructions.
    assert len(out.encode("utf-8")) <= 32 * 1024


def test_step3_rendered_size_budget():
    out = compose_step3(Step3Inputs(
        worktree_path="/wt", design_doc_path="/d.md",
        iteration_note_path="/n.md", output_path="/o.md",
        mount_table_entries=(),
    ))
    assert len(out.encode("utf-8")) <= 32 * 1024


def test_step4_rendered_size_budget():
    out = compose_step4(Step4Inputs(
        worktree_path="/wt", iteration_note_path="/n.md",
        context_path="/c.md", findings_output_path="/f.json",
        mount_table_entries=(),
    ))
    assert len(out.encode("utf-8")) <= 24 * 1024


def test_step5_rendered_size_budget():
    out = compose_step5(_step5_minimal())
    # Budget covers wall + boundary check + next-round-hints guide +
    # aggregation/scoring rules + tail JSON schema + field rules.
    assert len(out.encode("utf-8")) <= 48 * 1024


def test_step3_dropped_reference_paths_heading():
    """Phase 8: STEP3 standardises on '必读路径' (was '参考路径')."""
    out = compose_step3(Step3Inputs(
        worktree_path="/wt", design_doc_path="/d.md",
        iteration_note_path="/n.md", output_path="/o.md",
        mount_table_entries=(),
    ))
    assert "## 必读路径" in out
    assert "## 参考路径" not in out


def test_step4_dropped_constraints_section():
    """Phase 8: STEP4 trailing '## 约束' is removed (duplicates wall)."""
    out = compose_step4(Step4Inputs(
        worktree_path="/wt", iteration_note_path="/n.md",
        context_path="/c.md", findings_output_path="/f.json",
        mount_table_entries=(),
    ))
    assert "## 约束" not in out


def test_step2_dropped_user_prompt_heading():
    """Phase 8: STEP2 inlines $user_prompt under '必读路径' item 3."""
    out = compose_step2(Step2Inputs(
        dev_work_id="dev-1", round=1,
        design_doc_path="/d.md", user_prompt="UNIQUE-MARKER",
        previous_review_path=None, output_path="/o.md",
    ))
    # User-prompt heading is gone; the marker still appears in the body.
    assert "## 用户 prompt" not in out
    assert "UNIQUE-MARKER" in out


def test_all_step_templates_share_uniform_shape():
    """Phase 8: every step's prompt opens H1 → wall → 必读路径."""
    step2 = compose_step2(Step2Inputs(
        dev_work_id="d", round=1, design_doc_path="/d.md", user_prompt="P",
        previous_review_path=None, output_path="/o.md",
    ))
    step3 = compose_step3(Step3Inputs(
        worktree_path="/wt", design_doc_path="/d.md",
        iteration_note_path="/n.md", output_path="/o.md",
        mount_table_entries=(),
    ))
    step4 = compose_step4(Step4Inputs(
        worktree_path="/wt", iteration_note_path="/n.md",
        context_path="/c.md", findings_output_path="/f.json",
        mount_table_entries=(),
    ))
    for out in (step2, step3, step4):
        # Wall is the first H2; 必读路径 is the second H2.
        assert out.index("## 本步职责墙") < out.index("## 必读路径")
