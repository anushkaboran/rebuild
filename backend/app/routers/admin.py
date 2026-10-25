"""State snapshot export / import for in-memory deployments.

`STORE_BACKEND=memory` loses everything on restart. These two routes are the
supported way to take a backup and restore it with the **original IDs** intact.

Both are local-operations endpoints. If `ADMIN_TOKEN` is set in the environment,
the `X-Admin-Token` header must match it; otherwise they are only reachable from
the machine running the server.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status

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
