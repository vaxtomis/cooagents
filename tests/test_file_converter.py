import base64
import hashlib

import pytest

from src.file_converter import (
    convert_docx_to_md,
    extract_inline_images,
    validate_upload,
)


def test_validate_upload_md():
    assert validate_upload("REQ-PROJ-1.md") == "md"


def test_validate_upload_docx():
    assert validate_upload("requirement.docx") == "docx"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("brief.pdf", "pdf"),
        ("legacy.doc", "doc"),
        ("sheet.xls", "xls"),
        ("sheet.xlsx", "xlsx"),
        ("sheet.excel", "xlsx"),
        ("mockup.png", "png"),
        ("photo.jpg", "jpg"),
        ("photo.jpeg", "jpg"),
    ],
)
def test_validate_upload_supported_design_attachment_types(filename, expected):
    assert validate_upload(filename) == expected


def test_validate_upload_case_insensitive():
    assert validate_upload("doc.MD") == "md"
    assert validate_upload("doc.DOCX") == "docx"
    assert validate_upload("brief.PDF") == "pdf"
    assert validate_upload("photo.JPEG") == "jpg"


def test_validate_upload_rejects_txt():
    with pytest.raises(Exception):
        validate_upload("file.txt")


def test_validate_upload_rejects_no_extension():
    with pytest.raises(Exception):
        validate_upload("noext")


# 1x1 transparent PNG
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def test_extract_inline_images_writes_and_dedupes(tmp_path):
    img = f"![alt](data:image/png;base64,{_PNG_B64})"
    content = f"# Title\n\n{img}\n\nsame again: {img}\n"

    images_dir = tmp_path / "doc_images"
    new_content, stats = extract_inline_images(content, images_dir, "doc_images")

    assert stats.matched == 2
    assert stats.unique == 1
    assert stats.duplicated == 1
    assert stats.failed == 0

    saved = list(images_dir.iterdir())
    assert len(saved) == 1
    assert saved[0].name == "image_001.png"

    # Reference rewritten to forward-slash relative path, both occurrences.
    assert "doc_images/image_001.png" in new_content
    assert "data:image/png;base64" not in new_content

    # Bytes match decoded base64.
    expected = base64.b64decode(_PNG_B64)
    assert saved[0].read_bytes() == expected
    assert hashlib.sha256(saved[0].read_bytes()).hexdigest() == hashlib.sha256(expected).hexdigest()


def test_extract_inline_images_preserves_non_image_content(tmp_path):
    content = "no images here\n\njust text and [a link](http://example.com)\n"
    new_content, stats = extract_inline_images(content, tmp_path / "x", "x")
    assert new_content == content
    assert stats.matched == 0
    assert stats.unique == 0


async def test_convert_docx_to_md_missing_markitdown(tmp_path, monkeypatch):
    # Simulate markitdown not being installed by masking the import.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "markitdown" or name.startswith("markitdown."):
            raise ImportError("masked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="markitdown"):
        await convert_docx_to_md(tmp_path / "in.docx", tmp_path / "out.md")
