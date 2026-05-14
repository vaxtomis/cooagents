"""Lightweight DevWork plan audit helpers.

Step5 reviews the latest iteration note, whose ``## 开发计划`` is expected to
carry the full cumulative checklist. These helpers keep the expensive review
focused by classifying each active plan item before the reviewer session starts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PLAN_CHECKBOX_RE = re.compile(
    r"^(\s*[-*]\s+\[)([ xX])(\]\s+)"
    r"((?:~~\s*)?)([A-Za-z][A-Za-z0-9_-]*-\d+(?:\.\d+)*)(\s*[:：].*)$"
)
_PLAN_IMPORTANCE_RE = re.compile(r"^\s*[:：]\s*\[(P[0-2])\]\s*(.*)$")
_PLAN_ID_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]*-\d+(?:\.\d+)*\b")


@dataclass(frozen=True)
class PlanChecklistItem:
    id: str
    checked: bool
    cancelled: bool
    importance: str | None
    label: str


def _normalise_path(path: str) -> str:
    return path.replace("\\", "/").strip()


def extract_plan_checklist_items(markdown: str) -> list[PlanChecklistItem]:
    """Parse active/cancelled checkbox plans from the ``## 开发计划`` section."""
    items: list[PlanChecklistItem] = []
    in_plan = False
    for line in markdown.splitlines():
        body = line.rstrip("\r\n")
        h2 = re.match(r"^##\s+(.+?)\s*$", body)
        if h2:
            in_plan = h2.group(1) == "开发计划"
            continue
        if not in_plan:
            continue
        match = PLAN_CHECKBOX_RE.match(body)
        if not match:
            continue
        suffix = match.group(6)
        importance_match = _PLAN_IMPORTANCE_RE.match(suffix)
        importance = importance_match.group(1) if importance_match else None
        label = (
            importance_match.group(2).strip()
            if importance_match
            else suffix.lstrip(" :：").strip()
        ).strip("~").strip()
        items.append(
            PlanChecklistItem(
                id=match.group(5),
                checked=match.group(2).lower() == "x",
                cancelled="~~" in body,
                importance=importance,
                label=label,
            )
        )
    return items


def extract_plan_ids_from_value(value: Any) -> set[str]:
    """Find plan IDs in structured Step4 fields without parsing free text."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"id", "plan_id", "planId"}:
                if isinstance(child, str):
                    found.update(_PLAN_ID_RE.findall(child))
                elif isinstance(child, list):
                    for entry in child:
                        if isinstance(entry, str):
                            found.update(_PLAN_ID_RE.findall(entry))
            elif key in {"ids", "plan_ids", "planIds"}:
                found.update(extract_plan_ids_from_value(child))
            elif isinstance(child, (dict, list)):
                found.update(extract_plan_ids_from_value(child))
    elif isinstance(value, list):
        for child in value:
            found.update(extract_plan_ids_from_value(child))
    return found


def _plan_evidence_paths(item: dict[str, Any] | None) -> set[str]:
    if not isinstance(item, dict):
        return set()
    raw = item.get("evidence") or item.get("files") or item.get("paths")
    if not isinstance(raw, list):
        return set()
    paths: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        token = entry.strip().replace("\\", "/")
        if not token:
            continue
        if ":" in token and not re.match(r"^[A-Za-z]:/", token):
            token = token.split(":", 1)[0]
        token = token.strip()
        if token:
            paths.add(_normalise_path(token))
    return paths


def _is_done_verified_plan(item: dict[str, Any] | None) -> bool:
    return (
        isinstance(item, dict)
        and item.get("status") == "done"
        and item.get("implemented") is not False
        and item.get("verified") is True
    )


def format_plan_sample(ids: list[str], limit: int = 10) -> str:
    shown = ids[:limit]
    suffix = "" if len(ids) <= limit else f", ... +{len(ids) - limit}"
    return ", ".join(shown) + suffix


def render_step5_plan_audit_targets(
    *,
    plan_items: list[PlanChecklistItem],
    previous_ledger: dict[str, dict[str, Any]],
    touched_plan_ids: set[str],
    changed_paths: set[str],
) -> str:
    active = [item for item in plan_items if not item.cancelled]
    if not active:
        return (
            "## 计划审查目标（系统轻量预筛）\n\n"
            "未检测到 active checkbox plan ID；按迭代设计内容自行核验。"
        )

    lines = [
        "## 计划审查目标（系统轻量预筛）",
        "",
        "后端已解析当前 `## 开发计划`，并用上一轮 plan_verification、"
        "Step4 plan_execution 与本轮 changed paths 做轻量预筛。"
        "请输出覆盖下表每个 ID 的 `plan_verification`；"
        "`carry_forward` 项可低成本继承，`must_review` 和 "
        "`watch_unfinished` 项需要本轮核验。",
        "",
        "| ID | 优先级 | 当前勾选 | 审查模式 | 原因 | 历史证据 |",
        "|---|---|---:|---|---|---|",
    ]
    for item in active:
        previous = previous_ledger.get(item.id)
        evidence_paths = _plan_evidence_paths(previous)
        evidence_changed = bool(evidence_paths & changed_paths)
        if item.id in touched_plan_ids:
            mode = "must_review"
            reason = "Step4 plan_execution 本轮提到"
        elif _is_done_verified_plan(previous) and evidence_paths and not evidence_changed:
            mode = "carry_forward"
            reason = "上一轮 done+verified，历史证据文件本轮未变"
        elif _is_done_verified_plan(previous) and not evidence_paths:
            mode = "watch_unfinished"
            reason = "上一轮 done+verified 但缺少可比对证据路径"
        elif _is_done_verified_plan(previous) and evidence_changed:
            mode = "must_review"
            reason = "历史证据文件本轮发生变化"
        else:
            mode = "watch_unfinished"
            reason = "未完成/未验证/无历史 ledger，常态化检查"
        evidence = ", ".join(sorted(evidence_paths)[:4]) if evidence_paths else "-"
        if len(evidence_paths) > 4:
            evidence += f", ... +{len(evidence_paths) - 4}"
        checked = "yes" if item.checked else "no"
        importance = item.importance or "-"
        lines.append(
            f"| `{item.id}` | {importance} | {checked} | `{mode}` | "
            f"{reason} | {evidence} |"
        )
    return "\n".join(lines)


def missing_plan_verification_ids(
    *,
    plan_items: list[PlanChecklistItem],
    plan_verification: list[dict],
) -> list[str]:
    expected = {item.id for item in plan_items if not item.cancelled}
    if not expected:
        return []
    seen = {
        item.get("id")
        for item in plan_verification
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    return sorted(expected - seen)
