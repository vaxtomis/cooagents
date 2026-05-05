#!/usr/bin/env python
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _maybe_reexec() -> None:
    if sys.version_info >= (3, 11):
        return
    for name in ("python3.11", "python3"):
        candidate = shutil.which(name)
        if not candidate:
            continue
        os.execv(candidate, [candidate, __file__, *sys.argv[1:]])


def main() -> int:
    _maybe_reexec()
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from src.deployment import main as deployment_main

    return deployment_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
