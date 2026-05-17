"""Coverage target: high coverage of src/design_validator.py."""
import pytest

from src.design_validator import (
    extract_h2_sections,
    parse_front_matter,
    validate_design_markdown,
)

REQ = [
    "问题与目标",
    "用户故事",
    "场景案例",
    "范围与非目标",
    "详细操作流程",
    "验收标准",
    "技术约束与集成边界",
    "交付切片",
    "决策记录",
    "打分 rubric",
]
MOC = ["页面结构"]


def _fm(mockup: str = "false", threshold: str = "85") -> str:
    return (
        "---\n"
        "title: T\n"
        "goal: G\n"
        "version: 1.0.0\n"
        f"rubric_threshold: {threshold}\n"
        f"needs_frontend_mockup: {mockup}\n"
        "---\n\n"
    )


def _scenario_section(valid: bool = True, bad_heading: bool = False) -> str:
    title = "### BAD 标题" if bad_heading else "### SC-01 登录成功"
    actor = "- Actor: User\n" if valid else ""
    main_flow = "- Main Flow:\n  1. 输入账号密码\n  2. 提交登录\n"
    expected = "- Expected Result: 登录成功并跳转首页\n" if valid else ""
    trigger = "- Trigger: 用户点击登录入口\n"
    preconditions = "- Preconditions: 账号已注册\n"
    return (
        "## 场景案例\n\n"
        f"{title}\n\n"
        f"{actor}"
        f"{trigger}"
        f"{preconditions}"
        f"{main_flow}"
        f"{expected}\n"
    )


def _problem_section(valid: bool = True) -> str:
    if not valid:
        return "## 问题与目标\n\n- 问题: 用户无法完成登录。\n"
    return (
        "## 问题与目标\n\n"
        "- 问题: 用户无法完成登录。\n"
        "- 证据: 用户诉求要求登录闭环。\n"
        "- 关键假设: Assumption - needs validation: 邮箱密码是主登录方式。\n"
        "- 成功信号: 登录成功和失败提示都可观察。\n"
    )


def _scope_section(valid: bool = True, missing_col: bool = False) -> str:
    if missing_col:
        table = (
            "| 优先级 | 范围项 |\n"
            "|---|---|\n"
            "| Must | 登录成功 |\n"
        )
    else:
        table = (
            "| 优先级 | 范围项 | 说明 |\n"
            "|---|---|---|\n"
            "| Must | 登录成功 | 核心闭环 |\n"
            "| Won't | 注册 | 非本版范围 |\n"
        )
    non_goal = "非目标:\n- 不实现注册流程。\n" if valid else ""
    return f"## 范围与非目标\n\n{table}\n{non_goal}"


def _tech_boundary_section(valid: bool = True) -> str:
    if not valid:
        return "## 技术约束与集成边界\n\n- 依赖系统: 认证服务。\n"
    return (
        "## 技术约束与集成边界\n\n"
        "- 依赖系统: 认证服务。\n"
        "- 权限/数据/兼容约束: 不泄露账号存在性。\n"
        "- 不可破坏行为: 既有会话不能回归。\n"
        "- 建议验证入口: 登录 API 测试。\n"
    )


def _delivery_section(valid: bool = True, bad_id: bool = False) -> str:
    ph_id = "P1" if bad_id else "PH-01"
    if not valid:
        return "## 交付切片\n\n- 登录成功\n"
    return (
        "## 交付切片\n\n"
        "| PH ID | 能力 | 依赖 | 可并行性 | 完成信号 |\n"
        "|---|---|---|---|---|\n"
        f"| {ph_id} | 登录成功 | 认证服务 | - | AC-01 通过 |\n"
    )


def _decisions_section(valid: bool = True, missing_col: bool = False) -> str:
    if missing_col:
        header = "| 决策 | 选择 | 理由 |\n|---|---|---|\n"
        rows = "| 登录方式 | 邮箱密码 | 最小闭环 |\n"
    else:
        header = "| 决策 | 选择 | 备选 | 理由 |\n|---|---|---|---|\n"
        rows = "| 登录方式 | 邮箱密码 | OAuth | 最小闭环 |\n"
    body = header + rows if valid or missing_col else "登录方式: 邮箱密码\n"
    return f"## 决策记录\n\n{body}\n"


def _acceptance_section(valid: bool = True) -> str:
    if valid:
        body = (
            "- [ ] AC-01: 当账号密码正确时，应跳转首页\n"
            "- [ ] AC-02: 当密码错误时，应展示可观察错误提示\n"
        )
    else:
        body = "- 登录成功\n- 登录失败有提示\n"
    return f"## 验收标准\n\n{body}\n"


def _rubric_section(valid: bool = True, bad_weight: bool = False, missing_col: bool = False) -> str:
    if missing_col:
        header = "| 维度 | 权重 |\n|---|---:|\n"
        rows = "| 完整性 | 20 |\n"
    else:
        header = "| 维度 | 权重 | 判定标准 |\n|---|---:|---|\n"
        weight = "二十" if bad_weight else "20"
        rows = (
            f"| 完整性 | {weight} | 章节齐全且字段完整 |\n"
            "| 对齐度 | 30 | 场景、流程、验收标准互相对齐 |\n"
        )
    body = header + rows if valid or bad_weight or missing_col else "完整性 20 分\n对齐度 30 分\n"
    return f"## 打分 rubric\n\n{body}\n"


def _body(
    *,
    sections: list[str] | None = None,
    mockup_line: bool = False,
    scenario_valid: bool = True,
    bad_scenario_heading: bool = False,
    acceptance_valid: bool = True,
    problem_valid: bool = True,
    scope_valid: bool = True,
    scope_missing_col: bool = False,
    tech_boundary_valid: bool = True,
    delivery_valid: bool = True,
    delivery_bad_id: bool = False,
    decisions_valid: bool = True,
    decisions_missing_col: bool = False,
    rubric_valid: bool = True,
    rubric_bad_weight: bool = False,
    rubric_missing_col: bool = False,
) -> str:
    ordered = sections or REQ
    parts: list[str] = []
    for section in ordered:
        if section == "问题与目标":
            parts.append(_problem_section(valid=problem_valid))
        elif section == "用户故事":
            parts.append("## 用户故事\n\n作为用户，我希望使用账号密码登录。\n")
        elif section == "场景案例":
            parts.append(_scenario_section(valid=scenario_valid, bad_heading=bad_scenario_heading))
        elif section == "范围与非目标":
            parts.append(_scope_section(valid=scope_valid, missing_col=scope_missing_col))
        elif section == "详细操作流程":
            parts.append("## 详细操作流程\n\n1. 打开登录页\n2. 输入账号密码\n3. 提交并校验结果\n")
        elif section == "验收标准":
            parts.append(_acceptance_section(valid=acceptance_valid))
        elif section == "技术约束与集成边界":
            parts.append(_tech_boundary_section(valid=tech_boundary_valid))
        elif section == "交付切片":
            parts.append(_delivery_section(valid=delivery_valid, bad_id=delivery_bad_id))
        elif section == "决策记录":
            parts.append(
                _decisions_section(
                    valid=decisions_valid,
                    missing_col=decisions_missing_col,
                )
            )
        elif section == "打分 rubric":
            parts.append(
                _rubric_section(
                    valid=rubric_valid,
                    bad_weight=rubric_bad_weight,
                    missing_col=rubric_missing_col,
                )
            )
        elif section == "页面结构":
            parts.append("## 页面结构\n\n登录页由表单区和帮助区组成。\n")
    if mockup_line:
        parts.append("\n设计图链接或路径: /tmp/mock.png\n")
    return "\n".join(parts)


def test_all_ok():
    md = _fm() + _body()
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert report.ok is True
    assert report.all_missing() == []


@pytest.mark.parametrize(
    "drop", ["title", "goal", "version", "rubric_threshold", "needs_frontend_mockup"]
)
def test_missing_front_matter(drop):
    md = _fm()
    md = (
        "\n".join(line for line in md.splitlines() if not line.startswith(drop + ":"))
        + "\n"
        + _body()
    )
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert drop in report.missing_fm_keys
    assert report.ok is False


@pytest.mark.parametrize("idx", range(len(REQ)))
def test_missing_required_section(idx):
    sections = [s for i, s in enumerate(REQ) if i != idx]
    md = _fm() + _body(sections=sections)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert REQ[idx] in report.missing_sections
    assert report.ok is False


def test_mockup_requires_extra_section_and_link():
    md = _fm(mockup="true") + _body()
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert "页面结构" in report.missing_sections
    assert any("设计图链接或路径" in err for err in report.errors)


def test_mockup_full_ok():
    md = _fm(mockup="true") + _body(sections=REQ + MOC, mockup_line=True)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert report.ok is True


def test_mockup_accepts_yes_and_1():
    md_yes = _fm(mockup="yes") + _body(sections=REQ + MOC, mockup_line=True)
    md_one = _fm(mockup="1") + _body(sections=REQ + MOC, mockup_line=True)
    assert validate_design_markdown(md_yes, required_sections=REQ, mockup_sections=MOC).ok
    assert validate_design_markdown(md_one, required_sections=REQ, mockup_sections=MOC).ok


def test_invalid_rubric_threshold_non_int():
    md = (
        "---\n"
        "title: T\n"
        "goal: G\n"
        "version: 1.0.0\n"
        "rubric_threshold: abc\n"
        "needs_frontend_mockup: false\n"
        "---\n\n"
    ) + _body()
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("rubric_threshold" in err for err in report.errors)
    assert report.ok is False


def test_rubric_threshold_out_of_range():
    md = _fm(threshold="0") + _body()
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("[1,100]" in err for err in report.errors)


def test_no_front_matter():
    md = _body()
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("front-matter" in err for err in report.errors)
    assert set(report.missing_fm_keys) == {
        "title",
        "goal",
        "version",
        "rubric_threshold",
        "needs_frontend_mockup",
    }


def test_parse_front_matter_minimal():
    fm, body = parse_front_matter("---\nk: v\n---\nhello")
    assert fm == {"k": "v"}
    assert body == "hello"


def test_parse_front_matter_missing_close():
    fm, _ = parse_front_matter("---\nk: v\nno close")
    assert fm == {"k": "v"}


def test_parse_front_matter_no_leading_marker():
    fm, body = parse_front_matter("just body\nhere")
    assert fm == {}
    assert body == "just body\nhere"


def test_extract_h2_sections():
    body = "## A\n\n## B\n### C\n##   D  \n"
    assert extract_h2_sections(body) == ["A", "B", "D"]


def test_scenario_case_requires_required_fields():
    md = _fm() + _body(scenario_valid=False)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("missing required field `Actor`" in err for err in report.errors)
    assert any("missing required field `Expected Result`" in err for err in report.errors)


def test_scenario_case_accepts_bold_required_fields():
    md = _fm() + _body()
    md = md.replace("- Actor:", "- **Actor:**")
    md = md.replace("- Main Flow:", "- **Main Flow:**")
    md = md.replace("- Expected Result:", "- **Expected Result:**")
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert report.ok is True


def test_scenario_case_heading_must_match_sc_pattern():
    md = _fm() + _body(bad_scenario_heading=True)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("SC-xx" in err for err in report.errors)


def test_acceptance_requires_ac_checklist():
    md = _fm() + _body(acceptance_valid=False)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("AC-xx" in err for err in report.errors)


def test_problem_section_requires_core_labels():
    md = _fm() + _body(problem_valid=False)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("问题与目标 missing required labels" in err for err in report.errors)


def test_scope_requires_moscow_table_and_non_goals():
    md = _fm() + _body(scope_valid=False, scope_missing_col=True)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("范围与非目标 table missing required columns" in err for err in report.errors)
    assert any("非目标" in err for err in report.errors)


def test_tech_boundary_requires_boundary_labels():
    md = _fm() + _body(tech_boundary_valid=False)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("技术约束与集成边界 missing required labels" in err for err in report.errors)


def test_delivery_requires_ph_table_and_valid_ids():
    md = _fm() + _body(delivery_bad_id=True)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("PH-xx" in err for err in report.errors)


def test_decisions_requires_decision_table_columns():
    md = _fm() + _body(decisions_missing_col=True)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("决策记录 table missing required columns" in err for err in report.errors)


def test_design_doc_rejects_devwork_task_ids():
    md = _fm() + _body() + "\nDW-01\n"
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("DW-xx" in err for err in report.errors)


def test_rubric_requires_markdown_table():
    md = _fm() + _body(rubric_valid=False)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("markdown table" in err for err in report.errors)


def test_rubric_requires_required_columns():
    md = _fm() + _body(rubric_missing_col=True)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("missing required columns" in err for err in report.errors)


def test_rubric_weight_must_be_integer():
    md = _fm() + _body(rubric_bad_weight=True)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert any("权重 must be an integer" in err for err in report.errors)


def test_all_missing_aggregates_front_matter_and_sections():
    md = _fm()
    md = "\n".join(line for line in md.splitlines() if not line.startswith("goal:")) + "\n"
    md += _body(sections=REQ[:3])
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    missing = report.all_missing()
    assert "front_matter.goal" in missing
    assert "验收标准" in missing
    assert "打分 rubric" in missing


def test_feedback_items_include_validation_errors():
    md = _fm() + _body(acceptance_valid=False)
    report = validate_design_markdown(md, required_sections=REQ, mockup_sections=MOC)
    assert report.all_missing() == []
    assert any(item.startswith("validation_error:") for item in report.feedback_items())
    assert any("AC-xx" in item for item in report.feedback_items())
