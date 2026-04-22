"""Coverage target (PRD L369): >=95% of src/design_validator.py."""
import pytest

from src.design_validator import (
    extract_h2_sections,
    parse_front_matter,
    validate_design_markdown,
)

REQ = ["用户故事", "用户案例", "详细操作流程", "验收标准", "打分 rubric"]
MOC = ["页面结构"]


def _fm(mockup="false", threshold="85"):
    return (
        "---\n"
        "title: T\n"
        "goal: G\n"
        "version: 1.0.0\n"
        f"rubric_threshold: {threshold}\n"
        f"needs_frontend_mockup: {mockup}\n"
        "---\n\n"
    )


def _body(sections=None, mockup_line=False):
    parts = []
    for s in sections or REQ:
        parts.append(f"## {s}\n\nbody {s}\n")
    if mockup_line:
        parts.append("\n设计图链接或路径: /tmp/mock.png\n")
    return "\n".join(parts)


def test_all_ok():
    md = _fm() + _body()
    r = validate_design_markdown(
        md, required_sections=REQ, mockup_sections=MOC
    )
    assert r.ok is True
    assert r.all_missing() == []


@pytest.mark.parametrize(
    "drop", ["title", "goal", "version", "rubric_threshold", "needs_frontend_mockup"]
)
def test_missing_front_matter(drop):
    md = _fm()
    md = (
        "\n".join(
            line
            for line in md.splitlines()
            if not line.startswith(drop + ":")
        )
        + "\n"
        + _body()
    )
    r = validate_design_markdown(
        md, required_sections=REQ, mockup_sections=MOC
    )
    assert drop in r.missing_fm_keys
    assert r.ok is False


@pytest.mark.parametrize("idx", range(5))
def test_missing_section(idx):
    sections = [s for i, s in enumerate(REQ) if i != idx]
    md = _fm() + _body(sections=sections)
    r = validate_design_markdown(
        md, required_sections=REQ, mockup_sections=MOC
    )
    assert REQ[idx] in r.missing_sections
    assert r.ok is False


def test_mockup_requires_extra_section():
    md = _fm(mockup="true") + _body()
    r = validate_design_markdown(
        md, required_sections=REQ, mockup_sections=MOC
    )
    assert "页面结构" in r.missing_sections
    assert any("设计图链接或路径" in e for e in r.errors)


def test_mockup_full_ok():
    md = _fm(mockup="true") + _body(
        sections=REQ + MOC, mockup_line=True
    )
    r = validate_design_markdown(
        md, required_sections=REQ, mockup_sections=MOC
    )
    assert r.ok is True


def test_mockup_accepts_yes_and_1():
    md_yes = _fm(mockup="yes") + _body(sections=REQ + MOC, mockup_line=True)
    md_one = _fm(mockup="1") + _body(sections=REQ + MOC, mockup_line=True)
    assert validate_design_markdown(
        md_yes, required_sections=REQ, mockup_sections=MOC
    ).ok
    assert validate_design_markdown(
        md_one, required_sections=REQ, mockup_sections=MOC
    ).ok


def test_invalid_rubric_threshold_non_int():
    md = (
        "---\n"
        "title: T\ngoal: G\nversion: 1.0.0\n"
        "rubric_threshold: abc\n"
        "needs_frontend_mockup: false\n---\n"
    ) + _body()
    r = validate_design_markdown(
        md, required_sections=REQ, mockup_sections=MOC
    )
    assert any("rubric_threshold" in e for e in r.errors)
    assert r.ok is False


def test_rubric_threshold_out_of_range():
    md = _fm(threshold="0") + _body()
    r = validate_design_markdown(
        md, required_sections=REQ, mockup_sections=MOC
    )
    assert any("[1,100]" in e for e in r.errors)


def test_rubric_threshold_too_high():
    md = _fm(threshold="200") + _body()
    r = validate_design_markdown(
        md, required_sections=REQ, mockup_sections=MOC
    )
    assert any("[1,100]" in e for e in r.errors)


def test_no_front_matter():
    md = _body()
    r = validate_design_markdown(
        md, required_sections=REQ, mockup_sections=MOC
    )
    assert any("front-matter" in e for e in r.errors)
    assert set(r.missing_fm_keys) == {
        "title", "goal", "version", "rubric_threshold", "needs_frontend_mockup"
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


def test_all_missing_aggregates_fm_and_sections():
    md = _fm() + _body(sections=REQ[:3])
    r = validate_design_markdown(
        md, required_sections=REQ, mockup_sections=MOC
    )
    missing = r.all_missing()
    assert "验收标准" in missing
    assert "打分 rubric" in missing
    assert not any(m.startswith("front_matter.") for m in missing)
