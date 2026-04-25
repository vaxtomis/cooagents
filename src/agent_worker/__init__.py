"""Agent worker package — runs on remote agent hosts (Phase 8b).

The worker is the ``cooagents-worker`` CLI entry point. It is shipped in the
same repo as cooagents itself and installed on agent hosts via
``pip install cooagents[worker]``. The worker:

  1. Calls ``GET /workspaces/{id}/files`` on the cooagents control plane
     to obtain the active file index.
  2. Compares against the local ``WORKSPACES_ROOT/<slug>/`` tree and,
     where DB-only files are missing locally or the local hash diverges,
     pulls bytes from OSS to repopulate the working tree.
  3. Spawns ``acpx <agent> exec --cwd <slug> --file <task>`` and streams
     its stdout to the SSH parent (cooagents).
  4. Diffs the working tree post-execution and POSTs each new/changed
     file back to ``POST /workspaces/{id}/files`` with a CAS predicate.
  5. Exits with the acpx return code.

The worker NEVER writes to the cooagents SQLite DB and NEVER PUTs OSS
directly — both writes flow through cooagents so the "single writer"
invariant from Phase 7b is preserved.
"""
from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.2.0"
