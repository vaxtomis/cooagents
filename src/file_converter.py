from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from src.exceptions import BadRequestError

ALLOWED_EXTENSIONS = {"md", "docx"}


def validate_upload(filename: str) -> str:
    """Return normalised extension ('md' or 'docx'). Raise on invalid."""
    suffix = Path(filename).suffix.lstrip(".").lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise BadRequestError(
            f"Only .md and .docx files are supported, got '.{suffix}'"
        )
    return suffix


async def convert_docx_to_md(input_path: Path, output_path: Path) -> None:
    """Convert a .docx file to markdown using pandoc.

    Raises RuntimeError if pandoc is not installed or conversion fails.
    """
    if not shutil.which("pandoc"):
        raise RuntimeError(
            "pandoc is required for .docx conversion but not found on PATH"
        )
    proc = await asyncio.create_subprocess_exec(
        "pandoc", "-f", "docx", "-t", "markdown", "-o", str(output_path), str(input_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Document conversion failed: {stderr.decode()}")
