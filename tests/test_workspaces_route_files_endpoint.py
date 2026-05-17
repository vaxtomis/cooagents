"""Phase 8b: ``GET/POST /api/v1/workspaces/{id}/files`` route contract.

These endpoints are the agent worker's read/write plane to cooagents.
Both reuse the standard auth chain — covered by other route tests, so the
fixture skips auth here for brevity.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter

from src.database import Database
from src.exceptions import BadRequestError, ConflictError, EtagMismatch, NotFoundError
from src.request_utils import client_ip
from src.storage import LocalFileStore
from src.storage.registry import WorkspaceFileRegistry, WorkspaceFilesRepo
from src.workspace_manager import WorkspaceManager


@pytest.fixture
async def client(tmp_path: Path):
    test_app = FastAPI(title="cooagents-test-files-endpoint")
    db = Database(db_path=tmp_path / "t.db", schema_path="db/schema.sql")
    await db.connect()
    ws_root = tmp_path / "ws"
    ws_root.mkdir()

    store = LocalFileStore(workspaces_root=ws_root)
    repo = WorkspaceFilesRepo(db)
    registry = WorkspaceFileRegistry(store=store, repo=repo)
    workspaces = WorkspaceManager(
        db, project_root=tmp_path, workspaces_root=ws_root, registry=registry,
    )

    test_app.state.db = db
    test_app.state.workspaces = workspaces
    test_app.state.registry = registry

    limiter = Limiter(key_func=client_ip, default_limits=["10000/minute"])
    test_app.state.limiter = limiter
    limiter.enabled = False
    # Decorators in routes/workspaces.py captured the module-level limiter
    # at import time; that instance accumulates state across tests in the
    # same process. Disable it here so we don't 429 when the full suite
    # runs both this file and tests/test_workspaces_route.py.
    from routes.workspaces import limiter as _module_limiter
    _module_limiter.enabled = False

    @test_app.exception_handler(EtagMismatch)
    async def _etag(request, exc):
        return JSONResponse(
            status_code=412,
            content={
                "error": "etag_mismatch",
                "message": str(exc),
                "current_hash": exc.current_hash,
                "expected_hash": exc.expected_hash,
            },
        )

    @test_app.exception_handler(NotFoundError)
    async def _nf(request, exc):
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": str(exc)},
        )

    @test_app.exception_handler(BadRequestError)
    async def _br(request, exc):
        return JSONResponse(
            status_code=400,
            content={"error": "bad_request", "message": str(exc)},
        )

    @test_app.exception_handler(ConflictError)
    async def _conflict(request, exc):
        return JSONResponse(
            status_code=409,
            content={"error": "conflict", "message": str(exc)},
        )

    from routes.workspaces import router as ws_router
    test_app.include_router(ws_router, prefix="/api/v1")

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        ac.db = db  # type: ignore[attr-defined]
        yield ac, ws_root

    await db.close()


async def _create_workspace(c: AsyncClient, slug: str = "w1") -> dict:
    r = await c.post("/api/v1/workspaces", json={"title": "W", "slug": slug})
    assert r.status_code == 201, r.text
    return r.json()


async def test_get_files_returns_active_index(client):
    c, _ = client
    ws = await _create_workspace(c, slug="get-idx")
    # Workspace creation seeds a workspace.md row; index should include it.
    r = await c.get(f"/api/v1/workspaces/{ws['id']}/files")
    assert r.status_code == 200
    body = r.json()
    assert body["workspace_id"] == ws["id"]
    assert body["slug"] == "get-idx"
    paths = {row["relative_path"] for row in body["files"]}
    assert "workspace.md" in paths


async def test_get_files_404_for_unknown_workspace(client):
    c, _ = client
    r = await c.get("/api/v1/workspaces/ws-doesnotexist/files")
    assert r.status_code == 404


async def test_get_files_filters_paginates_and_marks_selectable(client):
    c, _ = client
    ws = await _create_workspace(c, slug="files-filter")
    await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "notes/a.md", "kind": "other"},
        files={"file": ("a.md", b"a", "text/markdown")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "ctx/b.md", "kind": "context"},
        files={"file": ("b.md", b"b", "text/markdown")},
        headers={"X-Expected-Prior-Hash": "none"},
    )

    r = await c.get(
        f"/api/v1/workspaces/{ws['id']}/files",
        params={"selectable": "true", "query": "notes", "limit": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pagination"]["total"] == 1
    assert body["files"][0]["relative_path"] == "notes/a.md"
    assert body["files"][0]["selectable"] is True


async def test_upload_workspace_file_alias_saves_file(client):
    c, ws_root = client
    ws = await _create_workspace(c, slug="file-upload")
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files/upload",
        files={"file": ("brief.md", b"# Brief", "text/markdown")},
    )
    assert r.status_code == 201, r.text
    rel = r.json()["attachment_path"]
    assert rel.startswith("attachments/brief-")
    assert (ws_root / "file-upload" / rel).read_bytes() == b"# Brief"


async def test_delete_workspace_file_removes_unreferenced_selectable_file(client):
    c, ws_root = client
    ws = await _create_workspace(c, slug="delete-ok")
    upload = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "notes/delete-me.md", "kind": "other"},
        files={"file": ("delete-me.md", b"bye", "text/markdown")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    assert upload.status_code == 201, upload.text

    r = await c.delete(
        f"/api/v1/workspaces/{ws['id']}/files",
        params={"relative_path": "notes/delete-me.md"},
    )
    assert r.status_code == 204, r.text
    assert not (ws_root / "delete-ok" / "notes" / "delete-me.md").exists()
    idx = await c.get(f"/api/v1/workspaces/{ws['id']}/files")
    assert "notes/delete-me.md" not in {
        row["relative_path"] for row in idx.json()["files"]
    }


async def test_delete_workspace_file_rejects_protected_kind(client):
    c, _ = client
    ws = await _create_workspace(c, slug="delete-protected")
    r = await c.delete(
        f"/api/v1/workspaces/{ws['id']}/files",
        params={"relative_path": "workspace.md"},
    )
    assert r.status_code == 400, r.text
    assert "protected kind" in r.json()["message"]


async def test_delete_workspace_file_rejects_referenced_file(client):
    c, _ = client
    ws = await _create_workspace(c, slug="delete-ref")
    upload = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "notes/keep.md", "kind": "other"},
        files={"file": ("keep.md", b"keep", "text/markdown")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    assert upload.status_code == 201, upload.text
    await c.db.execute(  # type: ignore[attr-defined]
        "INSERT INTO workspace_file_refs(id,workspace_id,relative_path,"
        "referrer_kind,referrer_id,created_at) VALUES(?,?,?,?,?,?)",
        (
            "wfr-delete",
            ws["id"],
            "notes/keep.md",
            "dev_work",
            "dev-1",
            "2026-04-24T00:00:00Z",
        ),
    )

    r = await c.delete(
        f"/api/v1/workspaces/{ws['id']}/files",
        params={"relative_path": "notes/keep.md"},
    )
    assert r.status_code == 409, r.text
    assert "dev_work:dev-1" in r.json()["message"]


async def test_delete_workspace_file_rejects_archived_workspace(client):
    c, _ = client
    ws = await _create_workspace(c, slug="delete-archived")
    await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "notes/a.md", "kind": "other"},
        files={"file": ("a.md", b"a", "text/markdown")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    archive = await c.delete(f"/api/v1/workspaces/{ws['id']}")
    assert archive.status_code == 204, archive.text

    r = await c.delete(
        f"/api/v1/workspaces/{ws['id']}/files",
        params={"relative_path": "notes/a.md"},
    )
    assert r.status_code == 400, r.text
    assert "not active" in r.json()["message"]


async def test_upload_markdown_attachment_saves_workspace_file(client):
    c, ws_root = client
    ws = await _create_workspace(c, slug="att-md")
    payload = b"# Brief\n\nMore details"
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/attachments",
        files={"file": ("brief.md", payload, "text/markdown")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["filename"] == "brief.md"
    assert body["markdown_path"].startswith("attachments/brief-")
    assert body["markdown_path"].endswith(".md")
    assert body["converted_from"] == "md"

    on_disk = ws_root / "att-md" / body["markdown_path"]
    assert on_disk.read_bytes() == payload

    idx = await c.get(f"/api/v1/workspaces/{ws['id']}/files")
    rows = {
        row["relative_path"]: row
        for row in idx.json()["files"]
    }
    assert rows[body["markdown_path"]]["kind"] == "attachment"


async def test_upload_docx_attachment_converts_to_markdown(client, monkeypatch):
    from routes import workspaces as ws_route

    async def fake_convert_document_to_md(input_path, output_path, **_kwargs):
        assert input_path.read_bytes() == b"docx bytes"
        assert input_path.suffix == ".docx"
        output_path.write_text("# Converted\n\nDoc text", encoding="utf-8")
        images_dir = output_path.parent / f"{output_path.stem}_images"
        images_dir.mkdir()
        (images_dir / "image_001.png").write_bytes(b"png")

    monkeypatch.setattr(
        ws_route, "convert_document_to_md", fake_convert_document_to_md,
    )

    c, ws_root = client
    ws = await _create_workspace(c, slug="att-docx")
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/attachments",
        files={
            "file": (
                "requirements.docx",
                b"docx bytes",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["converted_from"] == "docx"
    assert body["markdown_path"].startswith("attachments/requirements-")
    assert body["image_paths"] and body["image_paths"][0].endswith("image_001.png")
    assert (ws_root / "att-docx" / body["markdown_path"]).read_text(
        encoding="utf-8"
    ) == "# Converted\n\nDoc text"
    assert (ws_root / "att-docx" / body["image_paths"][0]).read_bytes() == b"png"


async def test_upload_doc_attachment_converts_to_markdown(client, monkeypatch):
    from routes import workspaces as ws_route

    async def fake_convert_document_to_md(input_path, output_path, **_kwargs):
        assert input_path.read_bytes() == b"document bytes"
        assert input_path.suffix == ".doc"
        output_path.write_text("# Converted doc", encoding="utf-8")

    monkeypatch.setattr(
        ws_route, "convert_document_to_md", fake_convert_document_to_md,
    )

    c, ws_root = client
    ws = await _create_workspace(c, slug="att-doc")
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/attachments",
        files={"file": ("legacy.doc", b"document bytes", "application/msword")},
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["converted_from"] == "doc"
    assert body["attachment_path"] == body["markdown_path"]
    assert body["markdown_path"].startswith("attachments/legacy-")
    assert body["markdown_path"].endswith(".md")
    assert body["image_paths"] == []
    assert (ws_root / "att-doc" / body["markdown_path"]).read_text(
        encoding="utf-8"
    ) == "# Converted doc"


@pytest.mark.parametrize(
    ("filename", "expected_path_suffix", "expected_from", "content_type"),
    [
        ("brief.pdf", ".pdf", "pdf", "application/pdf"),
        ("sheet.xls", ".xls", "xls", "application/vnd.ms-excel"),
        (
            "sheet.xlsx",
            ".xlsx",
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (
            "sheet.excel",
            ".xlsx",
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        ("mockup.png", ".png", "png", "image/png"),
        ("photo.jpg", ".jpg", "jpg", "image/jpeg"),
        ("photo.jpeg", ".jpg", "jpg", "image/jpeg"),
    ],
)
async def test_upload_original_attachment_saves_original_file(
    client, monkeypatch, filename, expected_path_suffix, expected_from, content_type,
):
    from routes import workspaces as ws_route

    async def fail_convert_document_to_md(*_args, **_kwargs):
        raise AssertionError("original attachments must not be converted")

    monkeypatch.setattr(
        ws_route, "convert_document_to_md", fail_convert_document_to_md,
    )

    c, ws_root = client
    ws = await _create_workspace(c, slug=f"att-{expected_from}")
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/attachments",
        files={"file": (filename, b"original bytes", content_type)},
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["converted_from"] == expected_from
    assert body["attachment_path"] == body["markdown_path"]
    assert body["attachment_path"].startswith(
        f"attachments/{Path(filename).stem}-"
    )
    assert body["attachment_path"].endswith(expected_path_suffix)
    expected_images = [body["attachment_path"]] if expected_from in {"jpg", "png"} else []
    assert body["image_paths"] == expected_images
    assert (
        ws_root / f"att-{expected_from}" / body["attachment_path"]
    ).read_bytes() == b"original bytes"

    idx = await c.get(f"/api/v1/workspaces/{ws['id']}/files")
    rows = {row["relative_path"]: row for row in idx.json()["files"]}
    assert rows[body["attachment_path"]]["kind"] == "attachment"


async def test_upload_docx_attachment_rejects_large_converted_markdown(
    client, monkeypatch
):
    from routes import workspaces as ws_route

    async def fake_convert_document_to_md(input_path, output_path, **_kwargs):
        output_path.write_text("12345", encoding="utf-8")

    monkeypatch.setattr(
        ws_route, "convert_document_to_md", fake_convert_document_to_md,
    )
    monkeypatch.setattr(ws_route, "MAX_ATTACHMENT_CONVERTED_MARKDOWN_BYTES", 4)

    c, _ = client
    ws = await _create_workspace(c, slug="att-docx-large-md")
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/attachments",
        files={
            "file": (
                "requirements.docx",
                b"docx bytes",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert r.status_code == 400, r.text
    assert "converted markdown attachment exceeds" in r.json()["message"]
    idx = await c.get(f"/api/v1/workspaces/{ws['id']}/files")
    paths = {row["relative_path"] for row in idx.json()["files"]}
    assert not any(path.startswith("attachments/") for path in paths)


async def test_upload_docx_attachment_rejects_large_converted_images(
    client, monkeypatch
):
    from routes import workspaces as ws_route

    async def fake_convert_document_to_md(input_path, output_path, **_kwargs):
        output_path.write_text("# Converted", encoding="utf-8")
        images_dir = output_path.parent / f"{output_path.stem}_images"
        images_dir.mkdir()
        (images_dir / "image_001.png").write_bytes(b"12")
        (images_dir / "image_002.png").write_bytes(b"34")

    monkeypatch.setattr(
        ws_route, "convert_document_to_md", fake_convert_document_to_md,
    )
    monkeypatch.setattr(ws_route, "MAX_ATTACHMENT_TOTAL_IMAGE_BYTES", 3)

    c, _ = client
    ws = await _create_workspace(c, slug="att-docx-large-img")
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/attachments",
        files={
            "file": (
                "requirements.docx",
                b"docx bytes",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert r.status_code == 400, r.text
    assert "converted attachment images exceed" in r.json()["message"]
    idx = await c.get(f"/api/v1/workspaces/{ws['id']}/files")
    paths = {row["relative_path"] for row in idx.json()["files"]}
    assert not any(path.startswith("attachments/") for path in paths)


async def test_upload_attachment_rejects_unsupported_extension(client):
    c, _ = client
    ws = await _create_workspace(c, slug="att-bad")
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/attachments",
        files={"file": ("brief.txt", b"nope", "text/plain")},
    )
    assert r.status_code == 400, r.text


async def test_post_first_write_creates_row_and_file(client):
    c, ws_root = client
    ws = await _create_workspace(c, slug="post-fw")
    payload = b"hello world"
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "notes/n1.md", "kind": "iteration_note"},
        files={"file": ("n1.md", payload, "text/markdown")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["relative_path"] == "notes/n1.md"
    assert row["byte_size"] == len(payload)
    # File hit local FS too.
    on_disk = (ws_root / "post-fw" / "notes" / "n1.md").read_bytes()
    assert on_disk == payload


async def test_post_first_write_collides_when_row_exists(client):
    c, _ = client
    ws = await _create_workspace(c, slug="post-coll")
    await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "a.md", "kind": "other"},
        files={"file": ("a.md", b"v1", "text/plain")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "a.md", "kind": "other"},
        files={"file": ("a.md", b"v2", "text/plain")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    assert r.status_code == 412, r.text
    body = r.json()
    assert body["error"] == "etag_mismatch"
    assert body["expected_hash"] is None
    assert body["current_hash"]


async def test_post_overwrite_with_correct_prior_hash_succeeds(client):
    c, _ = client
    ws = await _create_workspace(c, slug="post-ov")
    r1 = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "b.md", "kind": "other"},
        files={"file": ("b.md", b"v1", "text/plain")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    prior = r1.json()["content_hash"]
    r2 = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "b.md", "kind": "other"},
        files={"file": ("b.md", b"v2", "text/plain")},
        headers={"X-Expected-Prior-Hash": prior},
    )
    assert r2.status_code == 201, r2.text


async def test_post_overwrite_with_stale_prior_hash_412(client):
    c, _ = client
    ws = await _create_workspace(c, slug="post-stale")
    await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "c.md", "kind": "other"},
        files={"file": ("c.md", b"v1", "text/plain")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "c.md", "kind": "other"},
        files={"file": ("c.md", b"v2", "text/plain")},
        headers={"X-Expected-Prior-Hash": "0" * 64},
    )
    assert r.status_code == 412


async def test_post_missing_cas_header_rejected(client):
    """H2 — endpoint must require X-Expected-Prior-Hash so a misbehaving
    worker cannot silently clobber files."""
    c, _ = client
    ws = await _create_workspace(c, slug="post-no-hdr")
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "x.md", "kind": "other"},
        files={"file": ("x.md", b"v1", "text/plain")},
        # No X-Expected-Prior-Hash header.
    )
    assert r.status_code == 400, r.text
    assert "X-Expected-Prior-Hash" in r.json()["message"]


async def test_post_archived_workspace_rejected(client):
    """H1 — writes to archived workspaces must be refused."""
    c, _ = client
    ws = await _create_workspace(c, slug="post-arch")
    archive = await c.delete(f"/api/v1/workspaces/{ws['id']}")
    assert archive.status_code == 204, archive.text
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "y.md", "kind": "other"},
        files={"file": ("y.md", b"v1", "text/plain")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    assert r.status_code == 400, r.text
    assert "not active" in r.json()["message"]


async def test_post_empty_payload_rejected(client):
    """H3 — empty bytes (b'') must produce 400, not a zero-byte row."""
    c, _ = client
    ws = await _create_workspace(c, slug="post-empty")
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "z.md", "kind": "other"},
        files={"file": ("z.md", b"", "text/plain")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    assert r.status_code == 400, r.text
    assert "empty" in r.json()["message"]


async def test_post_oversized_upload_rejected(client, monkeypatch):
    """H3 — Content-Length above the cap must be refused without buffering."""
    from routes import workspaces as ws_route

    monkeypatch.setattr(ws_route, "MAX_WORKER_UPLOAD_BYTES", 16)
    c, _ = client
    ws = await _create_workspace(c, slug="post-big")
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": "big.md", "kind": "other"},
        files={"file": ("big.md", b"x" * 1024, "text/plain")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    assert r.status_code == 400, r.text
    assert "16 byte limit" in r.json()["message"]


async def test_post_location_header_url_encodes_path(client):
    """H4 — relative_path with reserved chars must be URL-encoded in Location."""
    c, _ = client
    ws = await _create_workspace(c, slug="post-loc")
    rel = "notes/sub dir/a&b.md"
    r = await c.post(
        f"/api/v1/workspaces/{ws['id']}/files",
        data={"relative_path": rel, "kind": "other"},
        files={"file": ("a.md", b"v1", "text/plain")},
        headers={"X-Expected-Prior-Hash": "none"},
    )
    assert r.status_code == 201, r.text
    loc = r.headers["Location"]
    # Reserved chars are escaped; '/' is preserved as a path separator.
    assert "sub%20dir" in loc
    assert "a%26b.md" in loc
