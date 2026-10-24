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
from dataclasses import dataclass
from pathlib import Path

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


def path_for(rel_path: str) -> Path:
    """Resolve a stored relative path, refusing to escape the upload dir."""
    root = settings.upload_dir.resolve()
    resolved = (root / rel_path).resolve()
    if not str(resolved).startswith(str(root)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path.")
    return resolved
