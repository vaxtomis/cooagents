"""SemVer generation for DesignDoc versions.

v1 scope (PRD L237): only ``mode=new -> "1.0.0"`` is supported; ``optimize``
(minor/patch derivation from parent) raises NotImplementedError. Keep this
module minimal so Phase 3.5/4.x can expand without breaking imports.
"""
from __future__ import annotations

from typing import Literal

SemVerKind = Literal["new", "patch", "minor", "major"]


def next_version(parent: str | None, kind: SemVerKind) -> str:
    if kind == "new":
        if parent is not None:
            raise ValueError(
                "next_version(parent=<set>, kind='new') is invalid; "
                "'new' must have no parent_version"
            )
        return "1.0.0"
    raise NotImplementedError(
        f"SemVer kind={kind!r} not supported in Phase 3 (v1 scope)"
    )


def parse(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"invalid SemVer {version!r}; expected 'X.Y.Z'")
    a, b, c = parts
    return int(a), int(b), int(c)
