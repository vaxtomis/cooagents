"""Mockup rendering abstraction.

v1 (U6): only ``PathMockupRenderer`` ships — it trusts whatever path or URL
the LLM stuffed into the ``设计图链接或路径:`` line and does NOT actually
render an image. This keeps the MVP closed-loop; automatic drawing via
pencil/stitch MCP is deferred.

Future implementations plug in by implementing ``MockupRenderer``:

    class PencilMockupRenderer:
        def __init__(self, pencil_client): ...
        async def render(self, spec: MockupSpec) -> MockupResult: ...

The DesignWorkStateMachine receives an instance in its ``__init__``; tests
substitute a stub. Keeping this file tiny (no pencil dep in v1) means the
hook exists without pulling any new third-party imports.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MockupSpec:
    """Input contract for any mockup renderer.

    The DesignWork state machine constructs this from the validated design
    doc markdown at D4.5 MOCKUP. ``page_structure_md`` is the body of the
    ``## 页面结构`` section so auto-renderers (Phase 4+) have context.
    """

    workspace_slug: str
    design_sub_slug: str
    version: str
    page_structure_md: str
    user_provided_link: str | None  # value of 设计图链接或路径 line, if any


@dataclass(frozen=True)
class MockupResult:
    """Path or URL of the final image, plus a note for the design doc."""

    link: str
    note: str = ""


class MockupRenderer(Protocol):
    async def render(self, spec: MockupSpec) -> MockupResult: ...


class PathMockupRenderer:
    """v1 renderer — passes through whatever the LLM wrote.

    Rationale (U6): v1 explicitly does NOT generate an image. The design
    validator already asserts the ``设计图链接或路径`` line is present; this
    renderer's only job is to report back what was there so the state
    machine can emit a ``design_work.mockup_recorded`` event for audit.
    """

    async def render(self, spec: MockupSpec) -> MockupResult:
        if not spec.user_provided_link:
            # Should not happen: validator enforces this before D4.5.
            return MockupResult(link="", note="missing user-provided link")
        return MockupResult(link=spec.user_provided_link, note="passthrough")
