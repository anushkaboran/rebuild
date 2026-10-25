"""Local filesystem storage for uploads.

Writes go to `settings.upload_dir/<case_id>/<upload_id><ext>`. Nothing here is
publicly reachable except through `GET /api/uploads/{upload_id}/file`, which
checks that the upload exists first.

If you later move to S3/R2, replace `save_upload` / `path_for` and keep the
signatures — nothing else in the app touches the filesystem.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, UploadFile, status

from ..config import settings

logger = logging.getLogger(__name__)

# Magic-number sniffing: a browser's Content-Type is a hint, not proof.
_MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"%PDF-": "application/pdf",
    b"RIFF": "image/webp",  # refined below
}


@dataclass
class StoredFile:
    rel_path: str
    size_bytes: int
    sha256: str
    content_type: str


def sniff_content_type(head: bytes, declared: str | None) -> str:
    """Prefer the sniffed magic number; fall back to the declared type."""
    declared = (declared or "").lower()
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1"):
        return "image/heic"
    for magic, ctype in _MAGIC.items():
        if head.startswith(magic) and not (magic == b"RIFF"):
            return ctype
    return declared or "application/octet-stream"


def _extension_for(content_type: str, original_name: str) -> str:
    known = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/heic": ".heic",
        "application/pdf": ".pdf",
    }
    if content_type in known:
        return known[content_type]
    return Path(original_name).suffix[:10] or ".bin"


async def save_upload(case_id: str, upload_id: str, file: UploadFile) -> StoredFile:
    """Stream an UploadFile to disk, enforcing the size limit as we go."""
    head = await file.read(4096)
    content_type = sniff_content_type(head, file.content_type)
    await file.seek(0)

    if content_type not in settings.allowed_content_type_set:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported file type '{content_type}'. "
                f"Allowed: {', '.join(sorted(settings.allowed_content_type_set))}"
            ),
        )

    directory = settings.upload_dir / case_id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{upload_id}{_extension_for(content_type, file.filename or '')}"

    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                out.close()
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File exceeds the {settings.max_upload_bytes} byte limit.",
                )
            digest.update(chunk)
            out.write(chunk)

    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty."
        )

    logger.info("stored upload %s (%s, %d bytes)", upload_id, content_type, size)
    return StoredFile(
        rel_path=str(target.relative_to(settings.upload_dir)),
        size_bytes=size,
        sha256=digest.hexdigest(),
        content_type=content_type,
    )


def _case_dir(case_id: str) -> Path:
    return settings.upload_dir / case_id


def resolve_upload_path(upload) -> Path | None:
    """Find the bytes for an upload, even when `stored_path` was never exported.

    `Upload.stored_path` is excluded from every API response (it is an internal
    path), so an upload restored from a snapshot has `stored_path == ""`. Rather
    than trusting that field alone we fall back to the on-disk naming convention
    `<upload_id>.<ext>` inside the case directory, which is how uploads are
    written in the first place.

    Returns None when no *file* exists — never a directory, which is what used to
    produce a 500 from FileResponse.
    """
    raw = getattr(upload, "stored_path", "") or ""
    if raw:
        try:
            candidate = path_for(raw)
        except HTTPException:
            candidate = None
        if candidate is not None and candidate.is_file():
            return candidate

    case_id = getattr(upload, "case_id", "") or ""
    upload_id = upload.id
    directories = [d for d in (_case_dir(case_id), settings.upload_dir) if d.is_dir()]
    for directory in directories:
        for candidate in sorted(directory.iterdir()):
            if candidate.is_file() and candidate.stem == upload_id:
                return candidate
    return None


def ascii_filename(filename: str | None) -> str:
    """An ASCII-safe, header-safe fallback for a download filename."""
    name = (filename or "").strip() or "download"
    suffix = Path(name).suffix
    stem_source = name[: -len(suffix)] if suffix else name

    stem = unicodedata.normalize("NFKD", stem_source).encode("ascii", "ignore").decode("ascii")
    # Never let quotes, backslashes or newlines break out of the header value.
    stem = re.sub(r'[\r\n"\\]', "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._-")
    if not stem:
        # Nothing ASCII survived (e.g. a fully CJK name): keep the extension so
        # the fallback still says what the file is.
        stem = "download"
    return stem + (suffix if suffix.isascii() else "")


def content_disposition(filename: str | None, *, disposition: str = "attachment") -> str:
    """RFC 6266 / RFC 5987 Content-Disposition.

    Every HTTP header value is latin-1 encodable. Uploaded filenames are not:
    `Screenshot … 11.31.08\u202fPM.png` contains U+202F (narrow no-break space)
    and writing it raw raises `UnicodeEncodeError` deep inside the ASGI server,
    surfacing as HTTP 500. So the header always carries an ASCII `filename`
    fallback, plus `filename*=UTF-8''…` when the real name is not ASCII — which
    keeps the download understandable without ever putting raw Unicode in a
    latin-1 header.
    """
    fallback = ascii_filename(filename)
    if filename is None or fallback == filename:
        return f'{disposition}; filename="{fallback}"'
    return (
        f'{disposition}; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def guessed_content_type(path: Path) -> str:
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".heic": "image/heic",
        ".pdf": "application/pdf",
    }.get(path.suffix.lower(), "application/octet-stream")


def path_for(rel_path: str) -> Path:
    """Resolve a stored relative path, refusing to escape the upload dir."""
    root = settings.upload_dir.resolve()
    resolved = (root / rel_path).resolve()
    if not str(resolved).startswith(str(root)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path.")
    return resolved
