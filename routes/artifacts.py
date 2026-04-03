import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from src.exceptions import NotFoundError
from src.file_converter import convert_md_to_docx

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


@router.get("/runs/{run_id}/artifacts/{artifact_id}/download")
async def download_artifact(run_id: str, artifact_id: int, request: Request, format: str = "md"):
    db = request.app.state.db
    am = request.app.state.artifacts
    art = await db.fetchone("SELECT * FROM artifacts WHERE id=? AND run_id=?", (artifact_id, run_id))
    if not art:
        raise NotFoundError(f"Artifact {artifact_id} not found")

    content = await am.get_content(artifact_id)
    filename_stem = Path(art["path"]).stem

    if format == "docx":
        with tempfile.TemporaryDirectory() as tmp_dir:
            md_path = Path(tmp_dir) / "source.md"
            docx_path = Path(tmp_dir) / f"{filename_stem}.docx"
            md_path.write_text(content, encoding="utf-8")
            await convert_md_to_docx(md_path, docx_path)
            docx_bytes = docx_path.read_bytes()
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename_stem}.docx"'},
        )

    # Default: return as markdown
    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename_stem}.md"'},
    )


@router.get("/runs/{run_id}/artifacts/{artifact_id}/diff")
async def get_artifact_diff(run_id: str, artifact_id: int, request: Request):
    am = request.app.state.artifacts
    diff = await am.get_diff(artifact_id)
    return {"artifact_id": artifact_id, "diff": diff}
