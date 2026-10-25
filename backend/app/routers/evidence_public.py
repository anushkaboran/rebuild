"""Signed, short-lived public URLs for evidence and prior results.

These are the URLs handed to the CREAO agent. They are unauthenticated by
design — CREAO's cloud has no session with us — but they are unguessable and
expire in minutes, which is why private receipts never sit at a permanent
public address.

Deliberately minimal surface:
  GET /api/evidence/{upload_id}/public?exp=…&sig=…
  GET /api/evidence/payloads/{investigation_id}/public?exp=…&sig=…

No listing, no directory traversal, no way to reach another case's evidence
without a valid signature for that exact id.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse, Response

from ..services import evidence_urls, storage
from ..store import store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/evidence", tags=["evidence-public"])

_NO_CACHE = {
    "Cache-Control": "private, no-store, max-age=0",
    "X-Robots-Tag": "noindex, nofollow",
}


def _authorise(path: str, object_id: str, exp: int, sig: str) -> None:
    ok, reason = evidence_urls.verify_signature(path, object_id, exp, sig)
    if not ok:
        # 403 for both expired and bad signature: do not disclose which.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "EVIDENCE_LINK_INVALID", "reason": reason},
        )


@router.get(
    "/payloads/{investigation_id}/public",
    summary="Serve a prior rebuild-result.json to the agent (signed URL)",
    include_in_schema=False,
)
def public_previous_result(
    investigation_id: str,
    exp: int = Query(...),
    sig: str = Query(...),
) -> Response:
    _authorise(evidence_urls.PAYLOAD_PATH, investigation_id, exp, sig)

    investigation = store.investigations.get(investigation_id)
    if investigation is None or not investigation.result_payload:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESULT_PAYLOAD_NOT_FOUND"},
        )

    body = json.dumps(investigation.result_payload).encode()
    return Response(
        content=body,
        media_type="application/json",
        headers={**_NO_CACHE, "Content-Length": str(len(body))},
    )


@router.get(
    "/claims/{submission_id}/pdf/public",
    summary="Serve a claim submission's recovery PDF to the agent (signed URL)",
    include_in_schema=False,
)
def public_claim_pdf(
    submission_id: str,
    exp: int = Query(...),
    sig: str = Query(...),
) -> Response:
    _authorise(evidence_urls.CLAIM_PDF_PATH, submission_id, exp, sig)

    submission = store.claim_submissions.get(submission_id)
    if submission is None or not submission.pdf_bytes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CLAIM_PDF_NOT_FOUND"},
        )

    return Response(
        content=submission.pdf_bytes,
        media_type="application/pdf",
        headers={
            **_NO_CACHE,
            "Content-Disposition": f'attachment; filename="{submission.pdf_filename or "recovery-inventory.pdf"}"',
        },
    )


@router.get(
    "/{upload_id}/public",
    summary="Serve an uploaded photo or receipt to the agent (signed URL)",
    include_in_schema=False,
)
def public_upload(
    upload_id: str,
    exp: int = Query(...),
    sig: str = Query(...),
) -> FileResponse:
    _authorise(evidence_urls.UPLOAD_PATH, upload_id, exp, sig)

    upload = store.uploads.get(upload_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "UPLOAD_NOT_FOUND"},
        )

    # Resolve by id when stored_path is empty (restored uploads).
    path = storage.resolve_upload_path(upload)
    if path is None:
        # Sanitised 404 — never leak the filesystem path, never a 500.
        logger.warning("evidence file missing for upload %s", upload.id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "EVIDENCE_FILE_MISSING"},
        )

    logger.info("served evidence %s to CREAO (%s)", upload.id, upload.content_type)
    return FileResponse(
        path,
        media_type=upload.content_type or storage.guessed_content_type(path),
        headers={"Content-Disposition": f'inline; filename="{upload.original_filename}"', **_NO_CACHE},
    )
