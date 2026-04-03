import pytest
from pathlib import Path
from src.file_converter import validate_upload, convert_docx_to_md


def test_validate_upload_md():
    assert validate_upload("REQ-PROJ-1.md") == "md"


def test_validate_upload_docx():
    assert validate_upload("requirement.docx") == "docx"


def test_validate_upload_case_insensitive():
    assert validate_upload("doc.MD") == "md"
    assert validate_upload("doc.DOCX") == "docx"


def test_validate_upload_rejects_txt():
    with pytest.raises(Exception):
        validate_upload("file.txt")


def test_validate_upload_rejects_no_extension():
    with pytest.raises(Exception):
        validate_upload("noext")


async def test_convert_docx_to_md_missing_pandoc(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    with pytest.raises(RuntimeError, match="pandoc"):
        await convert_docx_to_md(tmp_path / "in.docx", tmp_path / "out.md")
