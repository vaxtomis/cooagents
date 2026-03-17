from fastapi import APIRouter, Request
from src.exceptions import NotFoundError

router = APIRouter(tags=["artifacts"])


@router.get("/runs/{run_id}/artifacts")
async def list_artifacts(run_id: str, request: Request, kind: str = None, status: str = None):
    am = request.app.state.artifacts
    return await am.get_by_run(run_id, kind=kind, status=status)


@router.get("/runs/{run_id}/artifacts/{artifact_id}")
async def get_artifact(run_id: str, artifact_id: int, request: Request):
    db = request.app.state.db
    art = await db.fetchone("SELECT * FROM artifacts WHERE id=? AND run_id=?", (artifact_id, run_id))
    if not art:
        raise NotFoundError(f"Artifact {artifact_id} not found")
    return dict(art)


@router.get("/runs/{run_id}/artifacts/{artifact_id}/content")
async def get_artifact_content(run_id: str, artifact_id: int, request: Request):
    am = request.app.state.artifacts
    db = request.app.state.db
    art = await db.fetchone("SELECT * FROM artifacts WHERE id=? AND run_id=?", (artifact_id, run_id))
    if not art:
        raise NotFoundError(f"Artifact {artifact_id} not found")
    content = await am.get_content(artifact_id)
    result = dict(art)
    result["content"] = content
    return result


@router.get("/runs/{run_id}/artifacts/{artifact_id}/diff")
async def get_artifact_diff(run_id: str, artifact_id: int, request: Request):
    am = request.app.state.artifacts
    diff = await am.get_diff(artifact_id)
    return {"artifact_id": artifact_id, "diff": diff}
