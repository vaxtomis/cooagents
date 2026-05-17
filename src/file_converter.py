"""Document conversion helpers.

Document -> markdown uses Microsoft's ``markitdown`` library with
``keep_data_uris`` turned on, then extracts inline base64 images into a
sibling folder and rewrites references to relative paths. This mirrors the
manual workflow of ``markitdown ... --keep-data-uris`` followed by the
``extract_md_images.py`` post-processor.

markdown -> docx still relies on ``pandoc`` since markitdown is one-way.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from src.exceptions import BadRequestError

ALLOWED_EXTENSIONS = {
    "doc",
    "docx",
    "excel",
    "jpg",
    "jpeg",
    "md",
    "pdf",
    "png",
    "xls",
    "xlsx",
}
CONVERTIBLE_UPLOAD_EXTENSIONS = {"doc", "docx"}
ORIGINAL_UPLOAD_EXTENSIONS = {"jpg", "pdf", "png", "xls", "xlsx"}
_EXTENSION_ALIASES = {
    "excel": "xlsx",
    "jpeg": "jpg",
}
_SUPPORTED_UPLOAD_EXTENSIONS = (
    ".md, .doc, .docx, .pdf, .xls, .xlsx, .excel, .png, .jpg"
)

# ![alt](data:image/TYPE;base64,DATA)
_INLINE_IMAGE_RE = re.compile(
    r"!\[([^\]]*)\]\(data:image/([a-zA-Z0-9+\-.]+);base64,([A-Za-z0-9+/=\s]+?)\)",
    re.DOTALL,
)

_MIME_EXT = {
    "png": ".png",
    "jpeg": ".jpg",
    "jpg": ".jpg",
    "gif": ".gif",
    "webp": ".webp",
    "svg+xml": ".svg",
    "bmp": ".bmp",
    "x-icon": ".ico",
    "tiff": ".tiff",
}


@dataclass(frozen=True)
class ImageExtractionStats:
    matched: int = 0
    unique: int = 0
    duplicated: int = 0
    failed: int = 0


def validate_upload(filename: str) -> str:
    """Return normalized extension for a supported attachment upload."""
    suffix = Path(filename).suffix.lstrip(".").lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise BadRequestError(
            f"Only {_SUPPORTED_UPLOAD_EXTENSIONS} files are supported, "
            f"got '.{suffix}'"
        )
    return _EXTENSION_ALIASES.get(suffix, suffix)


def _mime_to_ext(mime_subtype: str) -> str:
    return _MIME_EXT.get(mime_subtype.lower(), ".bin")


def extract_inline_images(
    content: str,
    images_dir: Path,
    image_subdir_name: str,
) -> tuple[str, ImageExtractionStats]:
    """Extract base64 data-URI images into ``images_dir`` and rewrite refs.

    References are rewritten to ``{image_subdir_name}/image_NNN.ext`` using
    forward slashes so they work across viewers and platforms. Identical
    payloads (by sha256) are deduplicated.
    """
    images_dir.mkdir(parents=True, exist_ok=True)

    matched = unique = duplicated = failed = 0
    hash_to_name: dict[str, str] = {}
    saved_counter = 0

    def replace(match: re.Match) -> str:
        nonlocal matched, unique, duplicated, failed, saved_counter
        matched += 1
        alt = match.group(1)
        mime_subtype = match.group(2)
        b64_clean = re.sub(r"\s+", "", match.group(3))

        try:
            data = base64.b64decode(b64_clean, validate=False)
        except Exception:  # noqa: BLE001 - base64 raises binascii.Error or ValueError
            failed += 1
            return match.group(0)

        digest = hashlib.sha256(data).hexdigest()
        existing = hash_to_name.get(digest)
        if existing is not None:
            duplicated += 1
            filename = existing
        else:
            saved_counter += 1
            filename = f"image_{saved_counter:03d}{_mime_to_ext(mime_subtype)}"
            (images_dir / filename).write_bytes(data)
            hash_to_name[digest] = filename
            unique += 1

        return f"![{alt}]({image_subdir_name}/{filename})"

    new_content = _INLINE_IMAGE_RE.sub(replace, content)
    return new_content, ImageExtractionStats(matched, unique, duplicated, failed)


async def convert_md_to_docx(input_path: Path, output_path: Path) -> None:
    """Convert a .md file to docx using pandoc.

    markitdown does not handle markdown->docx, so pandoc remains the backend.
    """
    if not shutil.which("pandoc"):
        raise RuntimeError(
            "pandoc is required for .docx conversion but not found on PATH"
        )
    proc = await asyncio.create_subprocess_exec(
        "pandoc", "-f", "markdown", "-t", "docx",
        "-o", str(output_path), str(input_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Document conversion failed: {stderr.decode()}")


def _convert_document_sync(input_path: Path) -> str:
    """Blocking document->markdown with keep_data_uris=True. Import is lazy so
    the rest of the module can be used in environments without markitdown."""
    try:
        from markitdown import MarkItDown
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "markitdown is required for attachment conversion. "
            "Install it with: pip install markitdown[docx]"
        ) from e

    md = MarkItDown()
    try:
        result = md.convert(str(input_path), keep_data_uris=True)
    except Exception as e:  # noqa: BLE001 - external converters vary
        suffix = input_path.suffix.lower() or "file"
        raise RuntimeError(
            f"attachment conversion failed for {suffix}: {e}"
        ) from e
    return result.text_content


def _find_libreoffice() -> str | None:
    for name in ("soffice.com", "soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    return None


async def _convert_legacy_doc_to_docx(input_path: Path, output_dir: Path) -> Path:
    converter = _find_libreoffice()
    if converter is None:
        raise RuntimeError(
            "legacy .doc attachment conversion requires LibreOffice "
            "(soffice) on PATH"
        )

    proc = await asyncio.create_subprocess_exec(
        converter,
        "--headless",
        "--convert-to",
        "docx",
        "--outdir",
        str(output_dir),
        str(input_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = (stderr or stdout).decode(errors="replace").strip()
        raise RuntimeError(
            f"legacy .doc attachment conversion failed: {detail}"
        )

    expected = output_dir / f"{input_path.stem}.docx"
    if expected.exists():
        return expected
    matches = sorted(output_dir.glob("*.docx"))
    if matches:
        return matches[0]
    raise RuntimeError("legacy .doc attachment conversion did not produce .docx")


async def convert_document_to_md(
    input_path: Path,
    output_path: Path,
    *,
    extract_images: bool = True,
    image_subdir_name: str = "images",
) -> ImageExtractionStats | None:
    """Convert a supported document to markdown; optionally extract images.

    When ``extract_images`` is True, base64 images are written to
    ``output_path.parent / <output_stem>_<image_subdir_name>`` and references
    in the markdown are rewritten to relative paths.

    Returns the extraction stats when images were processed, else ``None``.
    """
    conversion_input = input_path
    if input_path.suffix.lower() == ".doc":
        conversion_input = await _convert_legacy_doc_to_docx(
            input_path, input_path.parent,
        )

    content = await asyncio.to_thread(_convert_document_sync, conversion_input)

    stats: ImageExtractionStats | None = None
    if extract_images:
        images_dir_name = f"{output_path.stem}_{image_subdir_name}"
        images_dir = output_path.parent / images_dir_name
        content, stats = extract_inline_images(content, images_dir, images_dir_name)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        f.write(content)
    return stats


async def convert_docx_to_md(
    input_path: Path,
    output_path: Path,
    *,
    extract_images: bool = True,
    image_subdir_name: str = "images",
) -> ImageExtractionStats | None:
    """Backward-compatible wrapper for existing .docx call sites."""
    return await convert_document_to_md(
        input_path,
        output_path,
        extract_images=extract_images,
        image_subdir_name=image_subdir_name,
    )
