from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter

from src.exceptions import BadRequestError
from src.models import EnsureRepoRequest
from src.path_validation import (
    RepoPathError,
    RepoUrlError,
    validate_repo_path,
    validate_repo_url,
)
from src.request_utils import client_ip

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
