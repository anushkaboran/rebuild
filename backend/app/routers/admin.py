"""State snapshot export / import for in-memory deployments.

`STORE_BACKEND=memory` loses everything on restart. These two routes are the
supported way to take a backup and restore it with the **original IDs** intact.

Both are local-operations endpoints. If `ADMIN_TOKEN` is set in the environment,
the `X-Admin-Token` header must match it; otherwise they are only reachable from
the machine running the server.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from ..config import settings
from ..services import evidence_urls, state_io, storage
from ..store import store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


def _authorise(token: str | None, request: Request) -> None:
    expected = settings.admin_token
    if expected:
        if token != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "ADMIN_TOKEN_INVALID", "message": "Missing or wrong X-Admin-Token."},
            )
        return
    # No token configured: restrict to loopback callers.
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ADMIN_LOCAL_ONLY",
                "message": "Set ADMIN_TOKEN to use these routes from a non-local client.",
            },
        )


def _require_token(token: str | None) -> None:
    """Fail closed. Used by endpoints that must not rely on network locality."""
    expected = settings.admin_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ADMIN_TOKEN_NOT_CONFIGURED",
                "message": "Set ADMIN_TOKEN before starting FastAPI; this endpoint is "
                           "disabled until then.",
            },
        )
    if not token or not hmac.compare_digest(str(token), str(expected)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "ADMIN_TOKEN_INVALID", "message": "Missing or wrong X-Admin-Token."},
        )


def _allowed_netloc() -> tuple[str, str]:
    """The one origin this endpoint may call (from REBUILD_PUBLIC_BASE_URL)."""
    base = (settings.rebuild_public_base_url or "").rstrip("/")
    if not base:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PUBLIC_BASE_URL_NOT_CONFIGURED",
                "message": "Set REBUILD_PUBLIC_BASE_URL to the public origin to check.",
            },
        )
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "PUBLIC_BASE_URL_INVALID", "message": "Public base URL must be http(s)."},
        )
    # HTTPS is required unless the explicit test/demo flag allows private hosts.
    if parsed.scheme != "https" and not settings.creao_allow_private_evidence_hosts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PUBLIC_BASE_URL_INSECURE",
                "message": "REBUILD_PUBLIC_BASE_URL must use https.",
            },
        )
    host = parsed.hostname or ""
    if not settings.creao_allow_private_evidence_hosts:
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "PUBLIC_BASE_URL_NOT_PUBLIC",
                        "message": "Public base URL must not point at a private address.",
                    },
                )
        except ValueError:
            pass
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "PUBLIC_BASE_URL_NOT_PUBLIC",
                    "message": "Public base URL must not point at localhost.",
                },
            )
    return base, parsed.netloc


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


def _clean_suffix(filename: str | None, stored_path: str | None, content_type: str | None) -> str:
    """Derive a safe, allow-listed extension from the saved metadata."""
    for candidate in (stored_path, filename):
        if not candidate:
            continue
        suffix = Path(candidate).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".pdf"}:
            return suffix
    return {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "image/gif": ".gif", "image/heic": ".heic", "application/pdf": ".pdf",
    }.get((content_type or "").lower(), "")


def _sha256_of(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _fail(code: str, message: str, http: int, **extra) -> HTTPException:
    return HTTPException(
        status_code=http, detail={"code": code, "message": message, **extra}
    )


@router.post(
    "/uploads/{upload_id}/restore-file",
    summary="Restore the original bytes for an existing upload (admin only)",
    responses={
        401: {"description": "Missing or wrong X-Admin-Token"},
        404: {"description": "Unknown upload"},
        409: {"description": "Integrity metadata missing, case mismatch, or a different file exists"},
        422: {"description": "Size or SHA-256 does not match the saved metadata"},
        503: {"description": "ADMIN_TOKEN is not configured"},
    },
)
async def restore_upload_file(
    upload_id: str,
    request: Request,
    file: UploadFile = File(..., description="The original image/PDF bytes"),
    case_id: str | None = Form(default=None),
    x_admin_token: str | None = Header(default=None),
) -> dict:
    """Put the original file back under the existing upload record.

    Creates nothing: no new Upload, EvidenceItem or Case. Every id, evidence
    reference, revision and human decision is left untouched. The file is only
    accepted when its size **and** SHA-256 match the values saved with the upload
    at ingest time — a client-supplied checksum is never trusted on its own.
    """
    _require_token(x_admin_token)

    upload = store.uploads.get(upload_id)
    if upload is None:
        raise _fail("UPLOAD_NOT_FOUND", f"Upload '{upload_id}' is not known.", status.HTTP_404_NOT_FOUND)

    if case_id and case_id != upload.case_id:
        raise _fail(
            "CASE_MISMATCH",
            "This upload does not belong to the supplied case; nothing was changed.",
            status.HTTP_409_CONFLICT,
        )

    expected_sha = (upload.sha256 or "").strip().lower()
    expected_size = int(upload.size_bytes or 0)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha) or expected_size <= 0:
        raise _fail(
            "UPLOAD_INTEGRITY_METADATA_MISSING",
            "This upload record has no trusted SHA-256 / size to verify against. "
            "Restore the value from the original upload API response (it returns "
            "sha256 and size_bytes) or from a known-good backup copy of the file; "
            "no checksum will be invented.",
            status.HTTP_409_CONFLICT,
        )

    if not _SAFE_ID.match(upload.case_id) or not _SAFE_ID.match(upload.id):
        raise _fail("UNSAFE_PATH", "Upload identifiers are not safe for path construction.",
                    status.HTTP_400_BAD_REQUEST)

    suffix = _clean_suffix(upload.original_filename, upload.stored_path, upload.content_type)
    if not suffix:
        raise _fail(
            "UNSUPPORTED_FILE_TYPE",
            "The saved upload has no supported image/PDF type to restore to.",
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    root = settings.upload_dir.resolve()
    directory = (root / upload.case_id).resolve()
    target = (directory / f"{upload.id}{suffix}").resolve()
    # Belt and braces: refuse anything that escapes the upload root.
    if root not in target.parents or directory != target.parent:
        raise _fail("UNSAFE_PATH", "Resolved path escapes the upload directory.",
                    status.HTTP_400_BAD_REQUEST)
    directory.mkdir(parents=True, exist_ok=True)

    max_bytes = settings.max_upload_bytes
    temp = directory / f".restore-{uuid.uuid4().hex}.tmp"

    digest = hashlib.sha256()
    size = 0
    try:
        with temp.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise _fail(
                        "FILE_TOO_LARGE",
                        f"Uploaded file exceeds the {max_bytes} byte limit.",
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    )
                digest.update(chunk)
                out.write(chunk)

        if size == 0:
            raise _fail("EMPTY_FILE", "The uploaded file is empty.", status.HTTP_400_BAD_REQUEST)

        actual_sha = digest.hexdigest()
        if size != expected_size:
            raise _fail(
                "SIZE_MISMATCH",
                "Uploaded file size does not match the size saved with this upload.",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                expected_size=expected_size,
                actual_size=size,
            )
        if actual_sha != expected_sha:
            raise _fail(
                "SHA256_MISMATCH",
                "Uploaded file SHA-256 does not match the digest saved with this upload.",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                expected_sha256=expected_sha,
                actual_sha256=actual_sha,
            )

        # --- destination handling ----------------------------------------- #
        if target.exists():
            existing_sha, existing_size = _sha256_of(target)
            if existing_sha == expected_sha and existing_size == expected_size:
                _mark_restored(upload, target, root)
                return _restored(upload, size, actual_sha, already_present=True)
            raise _fail(
                "DESTINATION_EXISTS",
                "A different file already exists at the destination; it was not "
                "overwritten. Move it aside before restoring.",
                status.HTTP_409_CONFLICT,
            )

        # Atomic within the same directory (same filesystem).
        os.replace(temp, target)
        _mark_restored(upload, target, root)
        logger.info("restored file for upload %s (%d bytes)", upload.id, size)
        return _restored(upload, size, actual_sha, already_present=False)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:  # pragma: no cover - best effort cleanup
                logger.warning("could not remove temp file for %s", upload_id)


def _mark_restored(upload, target: Path, root: Path) -> None:
    """Record the relative path on the existing record — nothing else changes."""
    relative = str(target.relative_to(root))
    updated = upload.model_copy(
        update={"stored_path": relative, "url": f"/api/uploads/{upload.id}/file"}
    )
    with store.lock:
        store.uploads[upload.id] = updated


def _restored(upload, size: int, sha: str, *, already_present: bool) -> dict:
    return {
        "upload_id": upload.id,
        "case_id": upload.case_id,
        "restored": True,
        "already_present": already_present,
        "size_bytes": size,
        "sha256": sha,
        "content_type": upload.content_type,
        "filename": upload.original_filename,
        "evidence_id": upload.evidence_id,
    }


@router.get(
    "/evidence/{upload_id}/check",
    summary="Diagnose the signed public evidence URL for one upload",
    responses={
        401: {"description": "Missing or wrong X-Admin-Token"},
        404: {"description": "Unknown upload, or its file is missing"},
        503: {"description": "ADMIN_TOKEN is not configured"},
    },
)
async def check_evidence_url(
    upload_id: str, x_admin_token: str | None = Header(default=None)
) -> dict:
    """Fetch our own signed evidence URL server-side and report what came back.

    Never returns, logs or accepts a URL: the caller names only an upload id.
    """
    _require_token(x_admin_token)

    upload = store.uploads.get(upload_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "UPLOAD_NOT_FOUND", "message": f"Upload '{upload_id}' is not known."},
        )

    path = storage.resolve_upload_path(upload)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "EVIDENCE_FILE_MISSING",
                "message": "The image file for this upload is not on disk.",
            },
        )

    _, allowed_netloc = _allowed_netloc()

    # Fresh short-lived link, signed by THIS process's key.
    try:
        url = evidence_urls.upload_url(upload_id)
    except evidence_urls.EvidenceUrlError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "PUBLIC_BASE_URL_NOT_CONFIGURED", "message": exc.message},
        ) from exc

    result: dict = {
        "upload_id": upload_id,
        "http_status": None,
        "content_type": None,
        "byte_count": 0,
        "valid_png_signature": False,
    }

    timeout = httpx.Timeout(settings.admin_check_timeout_seconds)
    current = url
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for _hop in range(3):
                # No Authorization header: this is a public URL by design.
                async with client.stream("GET", current, headers={"Cache-Control": "no-store"}) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        target = urljoin(current, location or "")
                        if urlparse(target).netloc != allowed_netloc:
                            result.update(
                                error_code="REDIRECT_NOT_ALLOWED",
                                message="The public origin redirected to a different host.",
                            )
                            return result
                        current = target
                        continue

                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > settings.admin_check_max_bytes:
                            result.update(
                                error_code="RESPONSE_TOO_LARGE",
                                message="The response exceeded the configured byte limit.",
                                http_status=response.status_code,
                                content_type=response.headers.get("content-type"),
                            )
                            return result

                    result["http_status"] = response.status_code
                    result["content_type"] = response.headers.get("content-type")
                    result["byte_count"] = len(body)
                    result["valid_png_signature"] = bytes(body[:8]) == b"\x89PNG\r\n\x1a\n"
                    if response.status_code >= 400:
                        result.update(
                            error_code="PUBLIC_URL_HTTP_ERROR",
                            message=f"The public URL answered HTTP {response.status_code}.",
                        )
                    return result
    except httpx.TimeoutException:
        result.update(error_code="FETCH_TIMEOUT", message="The public URL did not answer in time.")
        return result
    except Exception:  # noqa: BLE001 - never leak internals
        logger.exception("evidence check failed for %s", upload_id)
        result.update(error_code="FETCH_FAILED", message="The public URL could not be reached.")
        return result

    result.update(error_code="REDIRECT_LIMIT", message="Too many redirects.")
    logger.info("evidence check %s: %s", upload_id, result.get("error_code") or "ok")
    return result


@router.get(
    "/export",
    summary="Full state snapshot (IDs preserved, no signed URLs or secrets)",
)
def export_state(request: Request, x_admin_token: str | None = Header(default=None)) -> dict:
    _authorise(x_admin_token, request)
    snapshot = state_io.build_snapshot()
    snapshot["source"] = {
        "exported_from": "live server",
        "store_backend": settings.store_backend,
    }
    snapshot["warnings"] = [
        "Investigation.result_payload is not exposed by the API and is not in this snapshot.",
        "EvidenceItem.agent_record is not exposed and is not in this snapshot.",
        "Upload.stored_path is not exposed; copy backend/uploads/ separately.",
        "ResearchRun.input_snapshot (signed URLs) is intentionally excluded.",
    ]
    return snapshot


@router.post(
    "/import",
    summary="Restore a snapshot with original IDs (validated, conflict-safe)",
    responses={
        400: {"description": "Snapshot invalid"},
        409: {"description": "Conflicting records; nothing imported"},
    },
)
def import_state(
    payload: dict,
    request: Request,
    on_conflict: str = "error",
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _authorise(x_admin_token, request)
    try:
        report = state_io.import_snapshot(payload, on_conflict=on_conflict)
    except state_io.SnapshotError as exc:
        detail = {"code": exc.code, "message": exc.message}
        if exc.conflicts:
            detail["conflicts"] = exc.conflicts
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    return {"ok": True, **report}


@router.post(
    "/verify",
    summary="Validate a snapshot without importing it",
)
def verify_state(
    payload: dict,
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> dict:
    _authorise(x_admin_token, request)
    try:
        return state_io.verify_snapshot(payload)
    except state_io.SnapshotError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
