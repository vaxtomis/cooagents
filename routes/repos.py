import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter

from src.exceptions import BadRequestError, NotFoundError
from src.models import EnsureRepoRequest
from src.path_validation import (
    RepoPathError,
    RepoUrlError,
    validate_repo_path,
    validate_repo_url,
)
from src.request_utils import client_ip

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=client_ip)

router = APIRouter(tags=["repos"])


@router.post("/repos/ensure")
@limiter.limit("10/minute")
async def ensure_repo(req: EnsureRepoRequest, request: Request):
    from src.git_utils import ensure_repo as _ensure_repo

    security = request.app.state.settings.security
    try:
        safe_path = validate_repo_path(req.repo_path, security.resolved_workspace_root())
    except RepoPathError as exc:
        raise BadRequestError(str(exc))

    if req.repo_url:
        try:
            validate_repo_url(
                req.repo_url,
                security.allowed_repo_hosts,
                security.allowed_repo_schemes,
            )
        except RepoUrlError as exc:
            raise BadRequestError(str(exc))

    try:
        result = await _ensure_repo(str(safe_path), req.repo_url)
    except ValueError as e:
        raise BadRequestError(str(e))
    status_code = 200 if result == "exists" else 201
    return JSONResponse(status_code=status_code, content={"status": result})


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
