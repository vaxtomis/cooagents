"""Design document validator — shared by DesignWork D2/D5 and DevWork Step1.

Rules (PRD L230-234, L357):
  * Front-matter (YAML-ish, key:value lines between ``---``) must include:
      title, goal, version, rubric_threshold, needs_frontend_mockup
  * Markdown H2 sections must include (order does not matter):
      用户故事, 用户案例, 详细操作流程, 验收标准, 打分 rubric
  * If ``needs_frontend_mockup: true`` -> additional mandatory section:
      页面结构  + a ``设计图链接或路径:`` line (can be a URL or a path)

The validator is *structural* — it does not judge content quality. Hollow-
section risk is handled by the Step5 rubric (PRD R9) in Phase 4.

Coverage target: >=95% (each missing field / section has a dedicated test).
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

# Match ``## <title>`` lines; title may contain spaces.
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    missing_fm_keys: tuple[str, ...] = ()
    missing_sections: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    front_matter: dict[str, str] = field(default_factory=dict)

    def all_missing(self) -> list[str]:
        """Flat list of 'missing X' keys for prompt composition."""
        out = [f"front_matter.{k}" for k in self.missing_fm_keys]
        out.extend(self.missing_sections)
        return out


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Return ``(parsed_kv, body)``. Empty dict if no front-matter block.

    Intentionally minimal: only ``key: value`` lines. Reuses the Phase 2
    workspace_manager parser contract (safe, no pyyaml dependency).
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    out: dict[str, str] = {}
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            body = "\n".join(lines[i + 1:])
            return out, body
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
        i += 1
    # No closing ``---`` — treat as malformed; return what we parsed.
    return out, text


def extract_h2_sections(body: str) -> list[str]:
    return [m.strip() for m in _H2_RE.findall(body)]


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

    # needs_frontend_mockup interpretation: accept 'true' / '1' / 'yes'.
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

    # Mockup extra rule: body must contain ``设计图链接或路径:`` line.
    if mockup_requested and _MOCKUP_FIELD_KEY not in body:
        errors.append(f"mockup required but '{_MOCKUP_FIELD_KEY}' line missing")

    # rubric_threshold must be an int in [1,100] when present.
    if "rubric_threshold" in fm:
        try:
            v = int(fm["rubric_threshold"])
            if not 1 <= v <= 100:
                errors.append("rubric_threshold must be in [1,100]")
        except ValueError:
            errors.append("rubric_threshold must be an integer")

    ok = not (missing_fm or missing_sections or errors)
    return ValidationReport(
        ok=ok,
        missing_fm_keys=missing_fm,
        missing_sections=missing_sections,
        errors=tuple(errors),
        front_matter=fm,
    )
