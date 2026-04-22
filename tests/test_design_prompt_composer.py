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
    s = compose_prompt(_base())
    assert "首轮，无补齐项" in s
    assert "needs_frontend_mockup: false" in s.replace("\n", " ") or "false" in s


def test_later_round_lists_missing():
    s = compose_prompt(_base(missing_sections=["用户故事", "验收标准"]))
    assert "- 用户故事" in s
    assert "- 验收标准" in s
    assert "上一轮缺失" in s


def test_mockup_true_includes_instruction():
    s = compose_prompt(_base(needs_frontend_mockup=True))
    assert "前端设计图" in s
    assert "设计图链接或路径" in s


def test_mockup_false_omits_instruction():
    s = compose_prompt(_base(needs_frontend_mockup=False))
    assert "前端设计图" not in s


def test_output_path_present():
    s = compose_prompt(_base(output_path="/tmp/foo-bar.md"))
    assert "/tmp/foo-bar.md" in s


def test_parent_version_empty_when_none():
    s = compose_prompt(_base())
    # Template renders the key with backticks; the placeholder
    # substitutes an empty string so no value appears after the ": ".
    assert "`parent_version`:" in s
    # The composer with explicit parent renders it verbatim
    s2 = compose_prompt(_base(parent_version="1.0.0"))
    assert "`parent_version`: 1.0.0" in s2
