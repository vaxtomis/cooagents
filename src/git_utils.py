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


async def run_git(*args, cwd=None, check=True) -> tuple[str, str, int]:
    """Run a git command, return (stdout, stderr, returncode)."""
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode().strip()
    err = stderr.decode().strip()
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(str(a) for a in args)} failed: {err}")
    return out, err, proc.returncode


async def ensure_worktree(
    repo_path: str,
    branch_name: str,
    worktree_path: str,
) -> tuple[str, str]:
    """Create or reuse a git worktree at *worktree_path* tracking *branch_name*.

    Callers supply *worktree_path* so placement is decoupled from repo
    layout — Phase 4 uses ``<workspace_root>/.coop/worktrees/<branch_safe>``
    so worktrees live alongside cooagents state instead of polluting the
    user's repo parent directory.

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
    wt_path = str(Path(worktree_path))

    # Check if worktree already exists.
    # On Windows git outputs forward-slash paths in --porcelain output, so
    # normalise both sides to forward slashes for a reliable comparison.
    out, _, _ = await run_git("worktree", "list", "--porcelain", cwd=repo_path)
    wt_path_forward = wt_path.replace("\\", "/")
    if wt_path_forward in out or wt_path in out:
        return branch_name, wt_path

    # Create branch if it doesn't exist
    _, _, rc = await run_git(
        "rev-parse", "--verify", branch_name, cwd=repo_path, check=False
    )
    if rc != 0:
        await run_git("branch", branch_name, cwd=repo_path)

    Path(wt_path).parent.mkdir(parents=True, exist_ok=True)
    await run_git("worktree", "add", wt_path, branch_name, cwd=repo_path)
    return branch_name, wt_path


async def check_conflicts(worktree: str, target_branch: str = "main") -> list[str]:
    """Dry-run merge to detect conflicts.

    Returns a list of conflicted file paths (empty if no conflicts).
    """
    _, _, rc = await run_git(
        "merge", "--no-commit", "--no-ff", target_branch,
        cwd=worktree,
        check=False,
    )
    if rc == 0:
        await run_git("merge", "--abort", cwd=worktree, check=False)
        return []

    # Collect conflicted files
    out, _, _ = await run_git(
        "diff", "--name-only", "--diff-filter=U",
        cwd=worktree,
        check=False,
    )
    await run_git("merge", "--abort", cwd=worktree, check=False)
    return [f for f in out.split("\n") if f.strip()]


async def rebase_on_main(worktree: str) -> bool:
    """Rebase the current branch on *main*.

    Returns ``True`` if clean, ``False`` if conflicts were detected.
    """
    _, _, rc = await run_git("rebase", "main", cwd=worktree, check=False)
    if rc != 0:
        await run_git("rebase", "--abort", cwd=worktree, check=False)
        return False
    return True


async def merge_to_main(repo_path: str, branch: str) -> tuple[bool, str]:
    """Merge *branch* into *main*.

    Returns
    -------
    (success, merge_commit_hash_or_error_message)
    """
    await run_git("checkout", "main", cwd=repo_path)
    _, err, rc = await run_git(
        "merge", "--no-ff", branch, cwd=repo_path, check=False
    )
    if rc != 0:
        await run_git("merge", "--abort", cwd=repo_path, check=False)
        return False, err
    out, _, _ = await run_git("rev-parse", "HEAD", cwd=repo_path)
    return True, out


async def get_head_commit(worktree: str) -> str:
    """Return the HEAD commit hash (40-char hex string)."""
    out, _, _ = await run_git("rev-parse", "HEAD", cwd=worktree)
    return out


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
