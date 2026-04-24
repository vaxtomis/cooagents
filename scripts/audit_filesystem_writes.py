#!/usr/bin/env python3
"""CI guard: forbid raw filesystem writes outside ``WorkspaceFileRegistry``.

Greps ``src/`` for ``.write_text(``, ``.write_bytes(``, and
``.open(..., "w"...)`` call sites. Any match outside the allowlist below is
a violation — Phase 3's contract says every workspace artifact write goes
through the registry.

Exit 0 = clean, 1 = violations (list printed to stdout).

Allowlist rationale (see phase-3-filestore-adoption.plan.md Task 13):
  * ``src/storage/*`` — FileStore implementation itself.
  * ``src/file_converter.py`` — standalone utility with no live caller in
    workspace flows; future upload routes that wire it in must route the
    resulting image bytes through ``registry.put_bytes(..., kind='image')``.
  * ``src/config.py`` — reads YAML only (no writes here).
  * ``src/database.py`` — reads schema.sql only.
  * ``src/skill_deployer.py`` — deploys skills to project root (outside
    workspaces_root); out of scope for the registry invariant.
  * ``src/design_prompt_composer.py`` / ``src/dev_prompt_composer.py`` /
    ``src/reviewer.py`` — template reads / ephemeral worktree reads only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[1]

# Files (relative to src/) where raw writes are permitted with justification.
_ALLOWLIST = frozenset({
    Path("storage") / "local.py",
    Path("storage") / "base.py",
    Path("storage") / "registry.py",
    Path("storage") / "__init__.py",
    Path("file_converter.py"),
    # config.py / database.py / skill_deployer.py have no writes today but are
    # listed so that if a future patch adds one, reviewers have context.
    Path("config.py"),
    Path("database.py"),
    Path("skill_deployer.py"),
    Path("design_prompt_composer.py"),
    Path("dev_prompt_composer.py"),
    Path("reviewer.py"),
})

_PATTERNS = (
    re.compile(r"\.write_text\("),
    re.compile(r"\.write_bytes\("),
    # `foo.open("w" / 'wb' / 'w+')` — positional write-mode flag.
    re.compile(r"\.open\s*\([^)]*[\"'][wax][b+t]*[\"']"),
    # `foo.open(mode="w")` / `io.open(p, mode='wb')` — kwarg form.
    re.compile(r"\bopen\s*\([^)]*mode\s*=\s*[\"'][wax][b+t]*[\"']"),
)


def _iter_py_files(src_root: Path):
    for path in src_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def scan(src_root: Path) -> list[tuple[Path, int, str]]:
    violations: list[tuple[Path, int, str]] = []
    for path in _iter_py_files(src_root):
        rel = path.relative_to(src_root)
        if rel in _ALLOWLIST:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            for pattern in _PATTERNS:
                if pattern.search(line):
                    violations.append((rel, lineno, line.strip()))
                    break
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forbid raw FS writes outside WorkspaceFileRegistry."
    )
    parser.add_argument(
        "--src", type=Path, default=_DEFAULT_ROOT / "src",
        help="Root of the source tree to audit (default: <repo>/src).",
    )
    args = parser.parse_args(argv)
    src_root = args.src.resolve()
    if not src_root.is_dir():
        print(f"error: {src_root!r} is not a directory", file=sys.stderr)
        return 2
    violations = scan(src_root)
    if not violations:
        print("audit_filesystem_writes: OK (0 violations)")
        return 0
    print(
        "audit_filesystem_writes: FAIL — route these writes through "
        "WorkspaceFileRegistry or add an explicit allowlist entry:"
    )
    for rel, lineno, snippet in violations:
        print(f"  src/{rel.as_posix()}:{lineno}: {snippet}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
