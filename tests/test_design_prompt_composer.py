"""Phase 3: D3 prompt composer tests."""
from src.design_prompt_composer import PromptInputs, compose_prompt


def _base(**overrides):
    base = dict(
        workspace_slug="ws-slug",
        title="Title",
        version="1.0.0",
        user_input="user needs",
        needs_frontend_mockup=False,
        output_path="/tmp/out.md",
    )
    base.update(overrides)
    return PromptInputs(**base)


def test_first_round_has_no_missing_hint():
    rendered = compose_prompt(_base())
    assert "首轮" in rendered
    assert "无补齐项" in rendered
    assert "`needs_frontend_mockup`: false" in rendered.replace("\n", " ")


def test_later_round_lists_missing():
    rendered = compose_prompt(_base(missing_sections=["用户故事", "验收标准"]))
    assert "- 用户故事" in rendered
    assert "- 验收标准" in rendered
    assert "上一轮缺失" in rendered


def test_prompt_mentions_new_contract_sections():
    rendered = compose_prompt(_base())
    assert "## 场景案例" in rendered
    assert "### SC-xx <标题>" in rendered
    assert "- [ ] AC-xx: ..." in rendered
    assert "维度 | 权重 | 判定标准" in rendered


def test_mockup_true_includes_instruction():
    rendered = compose_prompt(_base(needs_frontend_mockup=True))
    assert "前端设计图" in rendered
    assert "设计图链接或路径" in rendered


def test_mockup_false_omits_instruction():
    rendered = compose_prompt(_base(needs_frontend_mockup=False))
    assert "前端设计图" not in rendered


def test_output_path_present():
    rendered = compose_prompt(_base(output_path="/tmp/foo-bar.md"))
    assert "/tmp/foo-bar.md" in rendered
    assert "将最终 markdown 写入" in rendered


def test_parent_version_empty_when_none():
    rendered = compose_prompt(_base())
    assert "`parent_version`:" in rendered
    rendered_with_parent = compose_prompt(_base(parent_version="1.0.0"))
    assert "`parent_version`: 1.0.0" in rendered_with_parent
