#!/usr/bin/env python3
"""Worker simulator for the Phase 5 (repo-registry) handoff contract.

Talks to the real cooagents HTTP API. For a given DevWork id, fetches
``GET /api/v1/dev-works/{id}``, walks ``repos[]``, ensures a node-shared
bare clone at ``<AGENT_ROOT>/.coop/repos/<repo_id>.git``, adds a
worktree under ``<AGENT_ROOT>/.coop/worktrees/<dw_id>/<mount_name>/``
checked out at ``devwork_branch``, then POSTs the outcome to
``/dev-works/{id}/repos/{mount_name}/push-state``.

Used by:
  * the manual MVP success-signal validation in the Phase 5 plan, and
  * an integration test
    (``tests/integration/test_phase5_worker_handoff.py``).

This is **not** the production worker — it documents the contract by
example. The real worker lives in the agent host process (out of repo).
Therefore this script intentionally has zero ``src.*`` imports and
takes only stdlib + ``httpx`` so it can be dropped on any agent host.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

# Mirrors src.models._REPO_NAME_PATTERN. Defensive duplication: this script
# must not import from ``src.*``, and a future server change relaxing the
# server-side check should not silently expand the worker's filesystem
# blast radius.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$")
# Restrict ``dev_id`` to the server's own ``dev-<hex>`` shape.
_SAFE_DEV_ID_RE = re.compile(r"^dev-[A-Za-z0-9]{1,40}$")


def _run_git(*args: str, cwd: str | None = None) -> tuple[int, str, str]:
    """Run a git subcommand and return (rc, stdout, stderr)."""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _ensure_bare_clone(bare_path: Path, url: str) -> tuple[bool, str]:
    """Idempotently ensure a bare clone exists at ``bare_path``.

    Returns ``(ok, msg_or_err)``. Reuses an existing bare directory
    silently — second DevWorks pointing at the same ``repo_id`` reuse
    the cached clone, which is the whole point of the
    ``<AGENT_ROOT>/.coop/repos/<repo_id>.git`` convention.
    """
    if bare_path.exists():
        # Sanity-check it's a real bare clone (has HEAD), but don't
        # re-clone — operator runs this on a node that may already
        # have done the heavy lift for another DevWork.
        if (bare_path / "HEAD").exists():
            return True, "reused"
        # Stale dir from an aborted prior run. Wipe and re-clone.
        shutil.rmtree(bare_path)
    bare_path.parent.mkdir(parents=True, exist_ok=True)
    # ``--`` separator: defends against CVE-2017-1000117-style argument
    # injection if ``url`` ever starts with ``-`` / ``--upload-pack=...``.
    rc, _, err = _run_git("clone", "--bare", "--", url, str(bare_path))
    if rc != 0:
        return False, err.strip() or f"git clone --bare failed (rc={rc})"
    return True, "cloned"


def _ensure_worktree(
    bare_path: Path, worktree_path: Path, branch: str,
) -> tuple[bool, str]:
    """Idempotently add a worktree at ``worktree_path`` checked out at branch.

    Equivalent shape to ``src.git_utils.ensure_worktree`` but inlined
    (this script must not import from ``src.*``).
    """
    if worktree_path.exists() and (worktree_path / ".git").exists():
        return True, "reused"
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    # ``--`` separator: ``branch`` flows from the API and could begin with
    # ``-`` in a misconfigured registry; guard against option confusion.
    rc, _, err = _run_git(
        "--git-dir", str(bare_path),
        "worktree", "add", str(worktree_path), "--", branch,
    )
    if rc != 0:
        return False, err.strip() or f"git worktree add failed (rc={rc})"
    return True, "added"


def _simulate_push(
    bare_path: Path, branch: str, dry_run: bool,
) -> tuple[bool, str]:
    """Simulate (or run) a push to the configured remote.

    The default ``--dry-run`` mode is what the integration test uses —
    we only need to prove the worker can *call* push-state with the
    right outcome. ``--no-dry-run`` is for the manual MVP success-signal
    validation against a real remote.
    """
    if dry_run:
        return True, "dry-run"
    rc, _, err = _run_git(
        "--git-dir", str(bare_path), "push", "origin", "--", branch,
    )
    if rc != 0:
        return False, err.strip() or f"git push failed (rc={rc})"
    return True, "pushed"


async def _post_push_state(
    client: httpx.AsyncClient,
    dev_id: str,
    mount_name: str,
    *,
    push_state: str,
    error_msg: str | None = None,
) -> httpx.Response:
    body: dict[str, object] = {"push_state": push_state}
    if error_msg is not None:
        body["error_msg"] = error_msg[:2048]  # boundary cap; server trims to 256
    return await client.post(
        f"/api/v1/dev-works/{dev_id}/repos/{mount_name}/push-state",
        json=body,
    )


async def _process_repo(
    client: httpx.AsyncClient,
    *,
    dev_id: str,
    repo_entry: dict,
    agent_root: Path,
    workspaces_root: Path,
    workspace_slug: str,
    dry_run: bool,
) -> tuple[bool, str]:
    """Run the per-repo handoff for one entry of ``repos[]``."""
    repo_id = repo_entry["repo_id"]
    mount_name = repo_entry["mount_name"]
    url = repo_entry["url"]
    devwork_branch = repo_entry["devwork_branch"]

    # Defensive: refuse path-component values that would break the
    # ``<root>/.coop/...`` layout. The server enforces these patterns
    # today; the worker keeps its own check so a future relaxation
    # doesn't silently let API-provided paths escape.
    if not _SAFE_NAME_RE.match(repo_id):
        return False, f"reject repo_id={repo_id!r}: unsafe path component"
    if not _SAFE_NAME_RE.match(mount_name):
        return False, f"reject mount_name={mount_name!r}: unsafe path component"
    if not _SAFE_DEV_ID_RE.match(dev_id):
        return False, f"reject dev_id={dev_id!r}: unsafe path component"

    bare_path = agent_root / ".coop" / "repos" / f"{repo_id}.git"
    worktree_path = (
        workspaces_root / workspace_slug
        / ".coop" / "worktrees" / dev_id / mount_name
    )

    ok, msg = _ensure_bare_clone(bare_path, url)
    if not ok:
        await _post_push_state(
            client, dev_id, mount_name,
            push_state="failed", error_msg=f"ensure_bare_clone: {msg}",
        )
        return False, f"bare-clone: {msg}"

    ok, msg = _ensure_worktree(bare_path, worktree_path, devwork_branch)
    if not ok:
        await _post_push_state(
            client, dev_id, mount_name,
            push_state="failed", error_msg=f"ensure_worktree: {msg}",
        )
        return False, f"worktree: {msg}"

    ok, msg = _simulate_push(bare_path, devwork_branch, dry_run)
    if not ok:
        await _post_push_state(
            client, dev_id, mount_name,
            push_state="failed", error_msg=f"push: {msg}",
        )
        return False, f"push: {msg}"

    resp = await _post_push_state(
        client, dev_id, mount_name, push_state="pushed",
    )
    if resp.status_code != 200:
        return False, f"push-state HTTP {resp.status_code}: {resp.text}"
    return True, "ok"


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev-work-id", required=True, dest="dev_id")
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8321",
        help="cooagents API base URL",
    )
    parser.add_argument(
        "--agent-root",
        default=os.environ.get("AGENT_ROOT"),
        help=(
            "Node-shared root for cached bare clones "
            "(<AGENT_ROOT>/.coop/repos/<repo_id>.git). "
            "Falls back to AGENT_ROOT env var."
        ),
    )
    parser.add_argument(
        "--workspaces-root",
        default=os.environ.get("WORKSPACES_ROOT"),
        help="Local root containing per-workspace worktrees.",
    )
    parser.add_argument(
        "--workspace-slug",
        default="ws",
        help=(
            "Slug used to compose worktree path; in the real worker "
            "this comes from the DevWork's workspace."
        ),
    )
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("COOAGENTS_TOKEN"),
        help="Bearer / X-Auth-Token for the cooagents API.",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
    )
    args = parser.parse_args(argv)

    if not args.agent_root:
        parser.error("--agent-root or AGENT_ROOT env var required")
    if not args.workspaces_root:
        parser.error("--workspaces-root or WORKSPACES_ROOT env var required")

    agent_root = Path(args.agent_root).resolve()
    workspaces_root = Path(args.workspaces_root).resolve()

    headers: dict[str, str] = {}
    if args.auth_token:
        headers["X-Auth-Token"] = args.auth_token

    async with httpx.AsyncClient(
        base_url=args.base_url, headers=headers, timeout=30.0,
    ) as client:
        r = await client.get(f"/api/v1/dev-works/{args.dev_id}")
        if r.status_code != 200:
            print(
                f"GET dev-works/{args.dev_id} → HTTP {r.status_code}: "
                f"{r.text}",
                file=sys.stderr,
            )
            return 2
        repos = r.json().get("repos") or []
        if not repos:
            print(
                f"dev_work {args.dev_id} has no repos[]; nothing to do",
                file=sys.stderr,
            )
            return 0

        # Sequential is fine — a DevWork has at most a handful of repos
        # and order doesn't matter for state writeback.
        any_failed = False
        for entry in repos:
            ok, msg = await _process_repo(
                client,
                dev_id=args.dev_id,
                repo_entry=entry,
                agent_root=agent_root,
                workspaces_root=workspaces_root,
                workspace_slug=args.workspace_slug,
                dry_run=args.dry_run,
            )
            tag = "OK" if ok else "FAIL"
            print(f"[{tag}] {entry['mount_name']}: {msg}")
            if not ok:
                any_failed = True
        return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
