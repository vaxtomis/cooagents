"""Design document validator shared by DesignWork and DevWork.

The validator remains structural rather than semantic, but v2 tightens a few
sections so the generated DesignDoc is easier for both humans and downstream
automation to consume.

Rules:
  * Front-matter must include:
      title, goal, version, rubric_threshold, needs_frontend_mockup
  * Markdown H2 sections must include:
      用户故事, 场景案例, 详细操作流程, 验收标准, 打分 rubric
  * If ``needs_frontend_mockup: true``:
      页面结构 + a ``设计图链接或路径:`` line are also required
  * ``场景案例`` must contain at least one ``### SC-xx <title>`` case with:
      Actor, Main Flow, Expected Result
  * ``验收标准`` must contain checklist items with ``AC-xx`` numbering
  * ``打分 rubric`` must be a markdown table with at least:
      维度 | 权重 | 判定标准
    and every ``权重`` cell must be integer-like
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_REQUIRED_FRONT_MATTER = (
    "title",
    "goal",
    "version",
    "rubric_threshold",
    "needs_frontend_mockup",
)
_MOCKUP_FIELD_KEY = "设计图链接或路径"
_SCENARIO_SECTION = "场景案例"
_ACCEPTANCE_SECTION = "验收标准"
_RUBRIC_SECTION = "打分 rubric"

_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_H3_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
_SCENARIO_CASE_TITLE_RE = re.compile(r"^SC-\d{2,}\s+\S.+$")
_SCENARIO_FIELD_NAMES = r"Actor|Trigger|Preconditions|Main Flow|Expected Result"
_SCENARIO_FIELD_LINE_RE = re.compile(
    rf"^(?:[-*]\s*)?(?:\*\*)?(?:{_SCENARIO_FIELD_NAMES})(?:\*\*)?"
    r"\s*[:：](?:\*\*)?",
)
_SCENARIO_FIELD_RE = {
    "Actor": re.compile(
        r"^(?:[-*]\s*)?(?:\*\*)?Actor(?:\*\*)?\s*[:：](?:\*\*)?\s*(.+)?$",
        re.MULTILINE,
    ),
    "Main Flow": re.compile(
        r"^(?:[-*]\s*)?(?:\*\*)?Main Flow(?:\*\*)?\s*[:：](?:\*\*)?\s*(.+)?$",
        re.MULTILINE,
    ),
    "Expected Result": re.compile(
        r"^(?:[-*]\s*)?(?:\*\*)?Expected Result(?:\*\*)?\s*[:：](?:\*\*)?\s*(.+)?$",
        re.MULTILINE,
    ),
}
_AC_ITEM_RE = re.compile(r"^\s*-\s*\[[ xX]?\]\s*AC-\d{2,}\s*:\s*\S.+$", re.MULTILINE)
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")
_INT_RE = re.compile(r"^\d+$")


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    missing_fm_keys: tuple[str, ...] = ()
    missing_sections: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    front_matter: dict[str, str] = field(default_factory=dict)

    def all_missing(self) -> list[str]:
        out = [f"front_matter.{k}" for k in self.missing_fm_keys]
        out.extend(self.missing_sections)
        return out

    def feedback_items(self) -> list[str]:
        out = self.all_missing()
        out.extend(f"validation_error: {err}" for err in self.errors)
        return out


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Return ``(parsed_kv, body)``. Empty dict if no front-matter block."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    out: dict[str, str] = {}
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            body = "\n".join(lines[i + 1 :])
            return out, body
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
        i += 1
    return out, text


def extract_h2_sections(body: str) -> list[str]:
    return [m.strip() for m in _H2_RE.findall(body)]


def _extract_section_body(body: str, title: str) -> str | None:
    pattern = re.compile(
        rf"^##\s+{re.escape(title)}\s*$\n?(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(body)
    return match.group(1).strip() if match else None


def _iter_scenario_cases(section_body: str) -> list[tuple[str, str]]:
    matches = list(_H3_RE.finditer(section_body))
    cases: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section_body)
        cases.append((title, section_body[start:end].strip()))
    return cases


def _field_has_content(case_body: str, field_name: str) -> bool:
    match = _SCENARIO_FIELD_RE[field_name].search(case_body)
    if not match:
        return False
    inline = (match.group(1) or "").strip()
    if inline:
        return True
    tail = case_body[match.end() :]
    for line in tail.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _SCENARIO_FIELD_LINE_RE.match(stripped):
            return False
        return True
    return False


def _validate_scenario_section(section_body: str) -> list[str]:
    errors: list[str] = []
    cases = _iter_scenario_cases(section_body)
    if not cases:
        return ["场景案例 must contain at least one `### SC-xx <title>` case"]
    for title, case_body in cases:
        if not _SCENARIO_CASE_TITLE_RE.match(title):
            errors.append(
                f"scenario case heading must match `### SC-xx <title>`; got {title!r}"
            )
            continue
        for field_name in ("Actor", "Main Flow", "Expected Result"):
            if not _field_has_content(case_body, field_name):
                errors.append(f"scenario case {title!r} missing required field `{field_name}`")
    return errors


def _validate_acceptance_section(section_body: str) -> list[str]:
    if _AC_ITEM_RE.search(section_body):
        return []
    return [
        "验收标准 must contain checklist items in the form `- [ ] AC-xx: ...`"
    ]


def _split_table_row(line: str) -> list[str]:
    raw = line.strip().strip("|")
    return [cell.strip() for cell in raw.split("|")]


def _extract_first_table(section_body: str) -> tuple[list[str], list[list[str]]] | None:
    lines = [line.rstrip() for line in section_body.splitlines()]
    for index in range(len(lines) - 1):
        if "|" not in lines[index]:
            continue
        if not _TABLE_SEPARATOR_RE.match(lines[index + 1]):
            continue
        header = _split_table_row(lines[index])
        rows: list[list[str]] = []
        cursor = index + 2
        while cursor < len(lines) and "|" in lines[cursor]:
            rows.append(_split_table_row(lines[cursor]))
            cursor += 1
        return header, rows
    return None


def _validate_rubric_section(section_body: str) -> list[str]:
    table = _extract_first_table(section_body)
    if table is None:
        return ["打分 rubric must contain a markdown table"]
    header, rows = table
    required_columns = ("维度", "权重", "判定标准")
    missing_columns = [col for col in required_columns if col not in header]
    if missing_columns:
        return [f"打分 rubric table missing required columns: {missing_columns}"]
    if not rows:
        return ["打分 rubric table must contain at least one data row"]

    weight_index = header.index("权重")
    errors: list[str] = []
    for row in rows:
        if len(row) <= weight_index:
            errors.append("打分 rubric row missing `权重` cell")
            continue
        weight = row[weight_index].strip()
        if not _INT_RE.match(weight):
            errors.append(f"打分 rubric 权重 must be an integer; got {weight!r}")
    return errors


def validate_design_markdown(
    text: str,
    *,
    required_sections: list[str],
    mockup_sections: list[str],
) -> ValidationReport:
    """Return a ValidationReport. Never raises on malformed input."""
    errors: list[str] = []
    fm, body = parse_front_matter(text)

    if not fm and not text.startswith("---"):
        errors.append("missing front-matter block (document must start with '---')")

    missing_fm = tuple(k for k in _REQUIRED_FRONT_MATTER if k not in fm)

    mockup_requested = fm.get("needs_frontend_mockup", "").strip().lower() in {
        "true",
        "1",
        "yes",
    }

    sections_found = set(extract_h2_sections(body))
    wanted = list(required_sections)
    if mockup_requested:
        wanted += list(mockup_sections)
    missing_sections = tuple(s for s in wanted if s not in sections_found)

    if mockup_requested and _MOCKUP_FIELD_KEY not in body:
        errors.append(f"mockup required but '{_MOCKUP_FIELD_KEY}' line missing")

    if "rubric_threshold" in fm:
        try:
            value = int(fm["rubric_threshold"])
            if not 1 <= value <= 100:
                errors.append("rubric_threshold must be in [1,100]")
        except ValueError:
            errors.append("rubric_threshold must be an integer")

    if _SCENARIO_SECTION in sections_found:
        errors.extend(_validate_scenario_section(_extract_section_body(body, _SCENARIO_SECTION) or ""))
    if _ACCEPTANCE_SECTION in sections_found:
        errors.extend(_validate_acceptance_section(_extract_section_body(body, _ACCEPTANCE_SECTION) or ""))
    if _RUBRIC_SECTION in sections_found:
        errors.extend(_validate_rubric_section(_extract_section_body(body, _RUBRIC_SECTION) or ""))

    ok = not (missing_fm or missing_sections or errors)
    return ValidationReport(
        ok=ok,
        missing_fm_keys=missing_fm,
        missing_sections=missing_sections,
        errors=tuple(errors),
        front_matter=fm,
    )
