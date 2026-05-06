"""Repo registry HTTP routes (Phase 1 + Phase 2 + Phase 3 + Phase 4).

Endpoints (all under ``/api/v1``):
  POST   /repos/{id}/fetch              - on-demand fetch (Phase 2)
  GET    /repos                         - list registered repos
  GET    /repos/{id}                    - one repo
  POST   /repos                         - register a new repo
  PATCH  /repos/{id}                    - partial update
  DELETE /repos/{id}                    - delete (refuses if FK-referenced)
  POST   /repos/sync                    - reload config/repos.yaml
  GET    /repos/{id}/branches           - list branches (Phase 3 inspector)
  GET    /repos/{id}/tree               - bounded tree listing
  GET    /repos/{id}/blob               - file content (1 MiB cap)
  GET    /repos/{id}/log                - commit log (default 50)

Phase 4 deleted ``POST /repos/ensure`` — DevWork creation now binds repos
through the registry-backed ``repo_refs`` payload, so the legacy
workspace-side clone has no remaining caller.

Auth: this module is mounted with ``auth_required`` at the app level
(``src/app.py``); no per-route Depends is needed.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from slowapi import Limiter

from src.exceptions import BadRequestError, NotFoundError
from src.models import (
    CreateRepoRequest,
    Repo,
    RepoBlob,
    RepoBranches,
    RepoLog,
    RepoLogPage,
    RepoPage,
    RepoRole,
    RepoTree,
    UpdateRepoRequest,
)
from src.request_utils import client_ip

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=client_ip)

router = APIRouter(tags=["repos"])


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("/repos", response_model=list[Repo] | RepoPage)
async def list_repos(
    request: Request,
    role: str | None = None,
    fetch_status: str | None = None,
    query: str | None = None,
    sort: str = "name_asc",
    limit: int = Query(12, ge=1, le=100),
    offset: int = Query(0, ge=0),
    paginate: bool = False,
) -> list[dict[str, Any]] | RepoPage:
    registry = request.app.state.repo_registry_repo
    if role and role not in {r.value for r in RepoRole}:
        raise BadRequestError(
            f"role must be one of {sorted(r.value for r in RepoRole)}"
        )
    if fetch_status and fetch_status not in {"unknown", "healthy", "error"}:
        raise BadRequestError(
            "fetch_status must be 'unknown', 'healthy', or 'error'"
        )
    if paginate:
        page = await registry.list_page(
            role=role,
            fetch_status=fetch_status,
            query=query,
            sort=sort,
            limit=limit,
            offset=offset,
        )
        return RepoPage(**page)
    return await registry.list_all(
        role=role,
        fetch_status=fetch_status,
        query=query,
        sort=sort,
    )


@router.get("/repos/{repo_id}", response_model=Repo)
async def get_repo(repo_id: str, request: Request) -> dict[str, Any]:
    registry = request.app.state.repo_registry_repo
    row = await registry.get(repo_id)
    if row is None:
        raise NotFoundError(f"repo not found: {repo_id!r}")
    return dict(row)


@router.post("/repos", status_code=201, response_model=Repo)
async def create_repo(
    payload: CreateRepoRequest, request: Request, response: Response,
) -> dict[str, Any]:
    registry = request.app.state.repo_registry_repo
    repo_id = (
        payload.id if payload.id is not None
        else f"repo-{uuid.uuid4().hex[:12]}"
    )
    if await registry.get(repo_id) is not None:
        raise BadRequestError(f"repo id already exists: {repo_id!r}")
    # Surface DB UNIQUE(name) as a clean 400 instead of letting sqlite3
    # raise IntegrityError (which would 500 without a dedicated handler).
    if await registry.get_by_name(payload.name) is not None:
        raise BadRequestError(f"repo name already exists: {payload.name!r}")
    row = await registry.upsert(
        id=repo_id,
        name=payload.name,
        url=payload.url,
        default_branch=payload.default_branch,
        ssh_key_path=payload.ssh_key_path,
        role=payload.role.value,
    )
    response.headers["Location"] = f"/api/v1/repos/{repo_id}"
    return dict(row)


@router.patch("/repos/{repo_id}", response_model=Repo)
async def update_repo(
    repo_id: str, payload: UpdateRepoRequest, request: Request,
) -> dict[str, Any]:
    registry = request.app.state.repo_registry_repo
    row = await registry.get(repo_id)
    if row is None:
        raise NotFoundError(f"repo not found: {repo_id!r}")
    new_name = payload.name if payload.name is not None else row["name"]
    if payload.name is not None and payload.name != row["name"]:
        clash = await registry.get_by_name(payload.name)
        if clash is not None and clash["id"] != repo_id:
            raise BadRequestError(
                f"repo name already exists: {payload.name!r}"
            )
    merged = await registry.upsert(
        id=repo_id,
        name=new_name,
        url=payload.url if payload.url is not None else row["url"],
        default_branch=(
            payload.default_branch if payload.default_branch is not None
            else row["default_branch"]
        ),
        ssh_key_path=(
            payload.ssh_key_path if payload.ssh_key_path is not None
            else row.get("ssh_key_path")
        ),
        bare_clone_path=row.get("bare_clone_path"),
        role=(
            payload.role.value if payload.role is not None
            else row.get("role", "other")
        ),
    )
    return dict(merged)


@router.delete("/repos/{repo_id}", status_code=204)
async def delete_repo(repo_id: str, request: Request) -> Response:
    registry = request.app.state.repo_registry_repo
    await registry.delete(repo_id)  # raises NotFoundError / ConflictError
    return Response(status_code=204)


@router.post("/repos/sync")
async def sync_repos(request: Request) -> dict[str, list[str]]:
    """Reload ``config/repos.yaml`` and reconcile with the DB."""
    registry = request.app.state.repo_registry_repo
    settings = request.app.state.settings
    return await registry.sync_from_config(settings.repos)


# ---------------------------------------------------------------------------
# Phase 2 fetch trigger
# ---------------------------------------------------------------------------

@router.post("/repos/{repo_id}/fetch")
@limiter.limit("20/minute")
async def fetch_repo(repo_id: str, request: Request):
    """Trigger an immediate clone-or-fetch for *repo_id*.

    Mirrors what ``RepoHealthLoop`` does on its tick, on demand. Status
    writes go through the same registry call so the loop and this route
    cannot disagree on contract.
    """
    registry = request.app.state.repo_registry_repo
    fetcher = request.app.state.repo_fetcher
    repo = await registry.get(repo_id)
    if repo is None:
        raise NotFoundError(f"repo not found: {repo_id!r}")
    try:
        outcome = await fetcher.fetch_or_clone(repo)
    except Exception as exc:
        # Record the error so the row reflects reality even when an
        # operator-triggered fetch fails. Then surface 502 — the request
        # itself failed against an upstream (the git remote). If the
        # secondary write also fails (DB down), surface the original git
        # error rather than masking it with a 500, but log the secondary
        # failure so ops sees both signals.
        try:
            await registry.update_fetch_status(
                repo_id, status="error", err=str(exc),
            )
        except Exception:
            logger.exception(
                "could not record fetch error for %s", repo_id,
            )
        raise HTTPException(
            status_code=502,
            detail=f"git fetch failed: {exc}",
        )
    await registry.update_fetch_status(
        repo_id,
        status="healthy",
        err=None,
        bare_clone_path=str(fetcher.bare_path(repo_id)),
    )
    # Tolerate a concurrent DELETE between the write and the read — the
    # successful fetch already happened, so report the known healthy state
    # without forcing the route to 500.
    row = await registry.get(repo_id) or {}
    return JSONResponse(
        status_code=200,
        content={
            "outcome": outcome,
            "fetch_status": row.get("fetch_status", "healthy"),
            "last_fetched_at": row.get("last_fetched_at"),
        },
    )


# ---------------------------------------------------------------------------
# Phase 3 inspector
# ---------------------------------------------------------------------------

@router.get("/repos/{repo_id}/branches", response_model=RepoBranches)
async def repo_branches(repo_id: str, request: Request) -> RepoBranches:
    inspector = request.app.state.repo_inspector
    return await inspector.branches(repo_id)


@router.get("/repos/{repo_id}/tree", response_model=RepoTree)
async def repo_tree(
    repo_id: str,
    request: Request,
    ref: str,
    path: str = "",
    depth: int | None = None,
    max_entries: int | None = None,
) -> RepoTree:
    inspector = request.app.state.repo_inspector
    kwargs: dict[str, Any] = {"ref": ref, "path": path}
    if depth is not None:
        kwargs["depth"] = depth
    if max_entries is not None:
        kwargs["max_entries"] = max_entries
    return await inspector.tree(repo_id, **kwargs)


@router.get("/repos/{repo_id}/blob", response_model=RepoBlob)
async def repo_blob(
    repo_id: str, request: Request, ref: str, path: str,
) -> RepoBlob:
    inspector = request.app.state.repo_inspector
    return await inspector.blob(repo_id, ref=ref, path=path)


@router.get("/repos/{repo_id}/log", response_model=RepoLog | RepoLogPage)
async def repo_log(
    repo_id: str,
    request: Request,
    ref: str,
    path: str | None = None,
    limit: int | None = None,
    offset: int = Query(0, ge=0),
    paginate: bool = False,
) -> RepoLog | RepoLogPage:
    inspector = request.app.state.repo_inspector
    kwargs: dict[str, Any] = {"ref": ref, "path": path, "offset": offset}
    if limit is not None:
        kwargs["limit"] = limit
    if paginate:
        log = await inspector.log(repo_id, **kwargs)
        total = await inspector.log_count(repo_id, ref=ref, path=path)
        page_limit = limit if limit is not None else 50
        return RepoLogPage(
            ref=log.ref,
            path=log.path,
            items=log.entries,
            pagination={
                "limit": page_limit,
                "offset": offset,
                "total": total,
                "has_more": (offset + page_limit) < total,
            },
        )
    return await inspector.log(repo_id, **kwargs)
