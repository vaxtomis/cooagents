"""D3 PROMPT_COMPOSE — assemble the LLM prompt for a DesignWork loop.

Kept pure so unit tests can assert on exact strings without spinning up a
state machine.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Sequence

_TPL_PATH = Path(__file__).resolve().parents[1] / "templates" / "design_prompt.md.tpl"


@dataclass(frozen=True)
class PromptAttachment:
    path: str
    content: str
    truncated: bool = False
    original_chars: int | None = None


@dataclass(frozen=True)
class PromptInputs:
    workspace_slug: str
    title: str
    version: str
    user_input: str
    needs_frontend_mockup: bool
    output_path: str
    parent_version: str | None = None
    missing_sections: Sequence[str] = ()
    attachments: Sequence[PromptAttachment] = ()


def _render_attachments(attachments: Sequence[PromptAttachment]) -> str:
    if not attachments:
        return ""
    parts = [
        "## Supplemental Materials",
        "",
        "The following uploaded attachments are additional source material. "
        "Use them to make the design more specific and complete.",
    ]
    for attachment in attachments:
        parts.extend(["", f"### Attachment: `{attachment.path}`", ""])
        parts.append(attachment.content.strip() or "(empty attachment)")
        if attachment.truncated:
            parts.extend([
                "",
                (
                    f"[Truncated from {attachment.original_chars} characters "
                    "to fit the prompt budget.]"
                ),
            ])
    return "\n".join(parts) + "\n"


def compose_prompt(inputs: PromptInputs) -> str:
    text = _TPL_PATH.read_text(encoding="utf-8")
    mockup_instruction = (
        "**前端设计图**：额外产出 `## 页面结构` 章节，并在其中写一行 "
        "`设计图链接或路径: <图片路径或 URL>`（v1 接受人工上传的路径或可访问 URL）。"
        if inputs.needs_frontend_mockup
        else ""
    )
    if inputs.missing_sections:
        bullets = "\n".join(f"- {s}" for s in inputs.missing_sections)
        missing_hint = f"上一轮缺失或格式错误：\n{bullets}\n请本轮务必修正上述项。"
    else:
        missing_hint = "(首轮，无补齐项)"

    return Template(text).safe_substitute(
        workspace_slug=inputs.workspace_slug,
        title=inputs.title,
        version=inputs.version,
        user_input=inputs.user_input,
        supplemental_materials=_render_attachments(inputs.attachments),
        needs_frontend_mockup="true" if inputs.needs_frontend_mockup else "false",
        output_path=inputs.output_path,
        parent_version_or_empty=inputs.parent_version or "",
        mockup_instruction=mockup_instruction,
        missing_sections_hint=missing_hint,
    )
