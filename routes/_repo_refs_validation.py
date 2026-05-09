"""Phase 4 (repo-registry): creation-time validation chain for repo refs.

Two coroutines:

- :func:`validate_dev_repo_refs` — for ``CreateDevWorkRequest``. Runs the
  4-step chain: existence → fetch_status='healthy' → branch resolves
  (``inspector.rev_parse``) and in-payload ``mount_name`` uniqueness, then
  returns that SHA for ``dev_work_repos.base_rev``.
- :func:`validate_design_repo_refs` — for ``CreateDesignWorkRequest``.
  Subset chain (no ``mount_name``): existence → health → branch resolves.

Both raise :class:`BadRequestError` (400) with the failing ``repo_id``
named in the message so operators see exactly which entry tripped.

Validation order is fixed: existence first so unknown ids surface before
the inspector touches a missing bare clone; health before rev_parse so a
401-style "not healthy" never gets confused with "branch missing"; mount
uniqueness last because it's a payload-shape check that doesn't need the
DB. Don't reorder.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.exceptions import BadRequestError
from src.models import DevRepoRef, RepoRef

if TYPE_CHECKING:  # pragma: no cover
    from src.repos.inspector import RepoInspector
    from src.repos.registry import RepoRegistryRepo


async def _check_existence_and_health(
    repo_id: str, registry: "RepoRegistryRepo"
) -> dict[str, Any]:
    row = await registry.get(repo_id)
    if row is None:
        raise BadRequestError(f"repo not registered: {repo_id!r}")
    fetch_status = row.get("fetch_status")
    if fetch_status != "healthy":
        raise BadRequestError(
            f"repo {repo_id!r} not healthy "
            f"(fetch_status={fetch_status!r}); "
            f"call POST /api/v1/repos/{repo_id}/fetch first"
        )
    return row


async def validate_design_repo_refs(
    refs: list[RepoRef],
    registry: "RepoRegistryRepo",
    inspector: "RepoInspector",
) -> list[tuple[RepoRef, str | None]]:
    """Three-step chain for DesignWork: existence, health, branch resolves.

    Returns a list of ``(ref, head_sha)`` — DesignWork persists the SHA on
    ``design_work_repos.rev`` for snapshot stability.
    """
    out: list[tuple[RepoRef, str | None]] = []
    for ref in refs:
        row = await _check_existence_and_health(ref.repo_id, registry)
        sha = await inspector.rev_parse(ref.repo_id, ref.base_branch, _row=row)
        if sha is None:
            raise BadRequestError(
                f"branch {ref.base_branch!r} not found in repo {ref.repo_id!r}"
            )
        out.append((ref, sha))
    return out


async def validate_dev_repo_refs(
    refs: list[DevRepoRef],
    registry: "RepoRegistryRepo",
    inspector: "RepoInspector",
) -> list[tuple[DevRepoRef, str | None]]:
    """Four-step chain: existence → health → branch resolves → mount unique.

    Stores the resolved ``base_branch`` SHA in the returned tuple so the
    caller can persist it on ``dev_work_repos.base_rev``.

    Returns ``[(ref, base_rev), ...]`` in the input order.
    """
    out: list[tuple[DevRepoRef, str | None]] = []
    seen_mounts: set[str] = set()
    for ref in refs:
        row = await _check_existence_and_health(ref.repo_id, registry)
        sha = await inspector.rev_parse(ref.repo_id, ref.base_branch, _row=row)
        if sha is None:
            raise BadRequestError(
                f"branch {ref.base_branch!r} not found in repo {ref.repo_id!r}"
            )
        # Step 4: mount uniqueness within the payload (boundary check —
        # the CreateDevWorkRequest model_validator runs first; we keep the
        # check here so direct callers can't bypass the regex/uniqueness
        # contract by skipping the DTO).
        if ref.mount_name in seen_mounts:
            raise BadRequestError(
                f"duplicate mount_name in repo_refs: {ref.mount_name!r}"
            )
        seen_mounts.add(ref.mount_name)

        out.append((ref, sha))
    return out
