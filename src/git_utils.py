"""Async git utilities for cooagents workflow management."""
from __future__ import annotations

import re
from pathlib import Path


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
    ticket: str,
    phase: str,
    run_suffix: str = "",
) -> tuple[str, str]:
    """Create or reuse a git worktree for *ticket*/*phase*.

    Returns
    -------
    (branch_name, worktree_path)
        The branch is ``feat/{ticket}-{phase}`` (or with a ``-{run_suffix}``
        appended when *run_suffix* is non-empty).
    """
    branch = (
        f"feat/{ticket}-{phase}"
        if not run_suffix
        else f"feat/{ticket}-{phase}-{run_suffix}"
    )
    wt_path = str(Path(repo_path).parent / f".worktrees/{ticket}-{phase}")

    # Check if worktree already exists.
    # On Windows git outputs forward-slash paths in --porcelain output, so
    # normalise both sides to forward slashes for a reliable comparison.
    out, _, _ = await run_git("worktree", "list", "--porcelain", cwd=repo_path)
    wt_path_forward = wt_path.replace("\\", "/")
    if wt_path_forward in out or wt_path in out:
        return branch, wt_path

    # Create branch if it doesn't exist
    _, _, rc = await run_git(
        "rev-parse", "--verify", branch, cwd=repo_path, check=False
    )
    if rc != 0:
        await run_git("branch", branch, cwd=repo_path)

    Path(wt_path).parent.mkdir(parents=True, exist_ok=True)
    await run_git("worktree", "add", wt_path, branch, cwd=repo_path)
    return branch, wt_path


async def get_diff_stat(worktree: str, base_commit: str) -> str:
    """Return ``git diff --stat base_commit..HEAD`` output."""
    out, _, _ = await run_git(
        "diff", "--stat", f"{base_commit}..HEAD", cwd=worktree
    )
    return out


async def get_commit_log(worktree: str, base_commit: str) -> list[dict]:
    """Return a list of commit dicts from *base_commit* to HEAD.

    Each dict has keys: ``hash``, ``message``, ``files_changed``,
    ``insertions``, ``deletions``.
    """
    out, _, _ = await run_git(
        "log",
        f"{base_commit}..HEAD",
        "--format=%H|%s",
        "--shortstat",
        cwd=worktree,
    )
    commits: list[dict] = []
    lines = out.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if "|" in line and len(line.split("|")[0]) == 40:
            parts = line.split("|", 1)
            commit: dict = {
                "hash": parts[0],
                "message": parts[1],
                "files_changed": 0,
                "insertions": 0,
                "deletions": 0,
            }
            # git log --shortstat places a blank line between the subject and
            # the stat summary, so look for the next *non-empty* line.
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and lines[j].strip() and "|" not in lines[j].split()[0]:
                stat = lines[j].strip()
                fc = re.search(r"(\d+) file", stat)
                ins = re.search(r"(\d+) insertion", stat)
                dels = re.search(r"(\d+) deletion", stat)
                if fc:
                    commit["files_changed"] = int(fc.group(1))
                if ins:
                    commit["insertions"] = int(ins.group(1))
                if dels:
                    commit["deletions"] = int(dels.group(1))
                i = j  # advance past the consumed stat line
            commits.append(commit)
        i += 1
    return commits


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


async def cleanup_worktree(repo_path: str, worktree: str, branch: str) -> None:
    """Remove *worktree* directory and delete the local *branch*."""
    await run_git(
        "worktree", "remove", worktree, "--force",
        cwd=repo_path,
        check=False,
    )
    await run_git("branch", "-D", branch, cwd=repo_path, check=False)


async def get_head_commit(worktree: str) -> str:
    """Return the HEAD commit hash (40-char hex string)."""
    out, _, _ = await run_git("rev-parse", "HEAD", cwd=worktree)
    return out
