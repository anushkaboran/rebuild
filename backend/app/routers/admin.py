"""State snapshot export / import for in-memory deployments.

`STORE_BACKEND=memory` loses everything on restart. These two routes are the
supported way to take a backup and restore it with the **original IDs** intact.

Both are local-operations endpoints. If `ADMIN_TOKEN` is set in the environment,
the `X-Admin-Token` header must match it; otherwise they are only reachable from
the machine running the server.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from ..config import settings
from ..services import state_io
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
