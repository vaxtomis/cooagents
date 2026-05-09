"""Async git utilities for cooagents workflow management."""
from __future__ import annotations

import re
from pathlib import Path

# Allowlist for branch names passed to ``git branch`` / ``git worktree add``.
# ``asyncio.create_subprocess_exec`` does not spawn a shell, so this is not
# about shell injection — it is about preventing argument confusion (e.g., a
# branch name starting with ``-`` being parsed as an option flag) and
# rejecting control characters that ``git`` would reject later anyway.
_BRANCH_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9/_.\-]{0,199}$")

# Phase 4 (repo-registry): canonical formats for DevWork-owned branches and
# commit messages. Both ``_s0_init`` and the Phase 5 worker import these so
# the control plane and execution worker cannot drift on branch naming.
# - ``slug``: Workspace.slug (kebab-case, ≤63 chars)
# - ``dw_short``: dev_works.id with the "dev-" prefix removed (hex12)
# - ``round``, ``step``: per-iteration round counter / step tag
DEVWORK_BRANCH_FMT = "devwork/{slug}/{dw_short}"
COMMIT_FMT = "[devwork/{slug}/{dw_short}] round {round}: {step}"


async def run_git(
    *args,
    cwd=None,
    check=True,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> tuple[str, str, int]:
    """Run a git command, return (stdout, stderr, returncode).

    When ``env`` is ``None``, the child inherits the parent environment (the
    documented :func:`asyncio.create_subprocess_exec` default). Pass an
    explicit dict only when overriding a value such as ``GIT_SSH_COMMAND``;
    never pass an empty dict, which would unset ``PATH`` and break ``git``.

    ``timeout`` (seconds) bounds wall-clock time. On timeout or cancellation
    the child is killed before propagating the exception so a hung remote
    cannot leak a zombie ``git`` process and an indefinitely held fd.
    """
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        if timeout is not None:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        else:
            stdout, stderr = await proc.communicate()
    except (asyncio.CancelledError, asyncio.TimeoutError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except ProcessLookupError:
            pass
        raise
    out = stdout.decode().strip()
    err = stderr.decode().strip()
    if check and proc.returncode != 0:
        # Surface only the first arg (the git subcommand) and stderr — embedding
        # the full arg list leaks bare-clone paths and refs into API error
        # responses when this RuntimeError bubbles up via BadRequestError.
        subcmd = str(args[0]) if args else "<no-args>"
        raise RuntimeError(f"git {subcmd} failed (rc={proc.returncode}): {err}")
    return out, err, proc.returncode


async def _ensure_branch_descends_from(
    repo_path: str,
    branch_name: str,
    start_point: str | None,
) -> None:
    if start_point is None:
        return
    base_sha, _, _ = await run_git(
        "rev-parse", "--verify", f"{start_point}^{{commit}}",
        cwd=repo_path,
    )
    branch_sha, _, _ = await run_git(
        "rev-parse", "--verify", f"refs/heads/{branch_name}^{{commit}}",
        cwd=repo_path,
    )
    _, _, rc = await run_git(
        "merge-base", "--is-ancestor", base_sha, branch_sha,
        cwd=repo_path,
        check=False,
    )
    if rc != 0:
        raise RuntimeError(
            f"branch {branch_name!r} is not based on start_point "
            f"{start_point!r}"
        )


async def ensure_worktree(
    repo_path: str,
    branch_name: str,
    worktree_path: str,
    *,
    start_point: str | None = None,
) -> tuple[str, str]:
    """Create or reuse a git worktree at *worktree_path* tracking *branch_name*.

    Callers supply *worktree_path* so placement is decoupled from repo
    layout — Phase 4 uses ``<workspace_root>/.coop/worktrees/<branch_safe>``
    so worktrees live alongside cooagents state instead of polluting the
    user's repo parent directory.

    When the branch does not exist yet, ``start_point`` is passed to
    ``git branch`` as the branch creation base. Existing branches are reused
    as-is so retries do not rewrite operator-visible refs.

    Returns
    -------
    (branch_name, worktree_path)
    """
    # Defense in depth: route-layer validates repo_path against workspace_root,
    # but guard against a repo_path that resolves to a filesystem root (which
    # would place the worktree directory outside any expected boundary).
    resolved_repo = Path(repo_path).resolve()
    if resolved_repo.parent == resolved_repo:
        raise ValueError(f"repo_path cannot be a filesystem root: {repo_path}")
    if not _BRANCH_RE.match(branch_name):
        raise ValueError(
            f"invalid branch_name {branch_name!r}: must match "
            f"[a-zA-Z0-9][a-zA-Z0-9/_.-]{{0,199}}"
        )
    if start_point is not None and not _BRANCH_RE.match(start_point):
        raise ValueError(
            f"invalid start_point {start_point!r}: must match "
            f"[a-zA-Z0-9][a-zA-Z0-9/_.-]{{0,199}}"
        )
    wt_path = str(Path(worktree_path))

    # Check if worktree already exists.
    # On Windows git outputs forward-slash paths in --porcelain output, so
    # normalise both sides to forward slashes for a reliable comparison.
    out, _, _ = await run_git("worktree", "list", "--porcelain", cwd=repo_path)
    wt_path_forward = wt_path.replace("\\", "/")
    if wt_path_forward in out or wt_path in out:
        head, _, _ = await run_git(
            "rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path,
        )
        if head != branch_name:
            raise RuntimeError(
                f"worktree {wt_path!r} is checked out at {head!r}, "
                f"expected {branch_name!r}"
            )
        await _ensure_branch_descends_from(
            repo_path, branch_name, start_point,
        )
        return branch_name, wt_path

    # Create branch if it doesn't exist
    _, _, rc = await run_git(
        "rev-parse", "--verify", f"refs/heads/{branch_name}",
        cwd=repo_path, check=False
    )
    if rc != 0:
        if start_point is None:
            await run_git("branch", branch_name, cwd=repo_path)
        else:
            await run_git("branch", branch_name, start_point, cwd=repo_path)
    else:
        await _ensure_branch_descends_from(
            repo_path, branch_name, start_point,
        )

    Path(wt_path).parent.mkdir(parents=True, exist_ok=True)
    await run_git("worktree", "add", wt_path, branch_name, cwd=repo_path)
    return branch_name, wt_path


async def ensure_repo(repo_path: str, repo_url: str | None = None) -> str:
    """Ensure a git repo exists at *repo_path*.

    Returns
    -------
    str
        ``"exists"`` if repo already present, ``"cloned"`` if cloned from
        *repo_url*, or ``"initialized"`` if created via ``git init``.

    Raises
    ------
    ValueError
        If *repo_path* exists but is not a git repository.
    RuntimeError
        If cloning fails.
    """
    p = Path(repo_path)
    if p.exists():
        _, _, rc = await run_git("rev-parse", "--git-dir", cwd=repo_path, check=False)
        if rc == 0:
            return "exists"
        raise ValueError(f"{repo_path} exists but is not a git repository")

    if repo_url:
        await run_git("clone", repo_url, repo_path)
        return "cloned"

    p.mkdir(parents=True, exist_ok=True)
    await run_git("init", cwd=repo_path)
    return "initialized"
