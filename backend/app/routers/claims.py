"""Claim submission endpoints — Gmail DRAFT only, never sent.

The backend itself never contacts Gmail: the agent creates one unsent draft and
reports a real `gmail_draft_id`. Everything here is about building safe inputs,
preventing duplicate drafts and exposing the result.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status

from ..config import settings
from ..deps import get_case_or_404
from ..models import (
    ClaimSubmission,
    ClaimSubmissionRequest,
    ClaimSubmissionResponse,
    InvestigationStatus,
    utcnow,
)
from ..services import claim, creao, events
from ..store import new_id, store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["claims"])

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _error(exc: claim.ClaimError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
    )


def _response(submission: ClaimSubmission) -> ClaimSubmissionResponse:
    return ClaimSubmissionResponse(
        submission=submission,
        draft_created=bool(submission.gmail_draft_id),
        sent=False,     # the agent may never send, and neither may we
    )


@router.post(
    "/cases/{case_id}/claim-submission",
    response_model=ClaimSubmissionResponse,
    summary="Prepare a claim email DRAFT for a reviewed case (never sends)",
    responses={
        200: {"description": "A submission is already active; it was returned as-is"},
        202: {"description": "New submission queued"},
        400: {"description": "Invalid recipient email"},
        409: {"description": "A draft already exists or a prior attempt is uncertain"},
        503: {"description": "Claim assistant not configured"},
    },
)
async def start_claim_submission(
    case_id: str,
    body: ClaimSubmissionRequest,
    background: BackgroundTasks,
    response: Response,
) -> ClaimSubmissionResponse:
    case = get_case_or_404(case_id)

    if not _EMAIL.match(body.recipient_email.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_RECIPIENT_EMAIL",
                "message": "recipient_email is not a valid address; it is never corrected "
                           "or substituted.",
            },
        )

    try:
        claim.ensure_ready()
    except claim.ClaimError as exc:
        raise _error(exc) from exc

    # ---- duplicate protection (no new paid run) --------------------------- #
    blocker, existing = claim.existing_blocker(case_id, body.recipient_email)
    if blocker == "active" and existing is not None:
        response.status_code = status.HTTP_200_OK
        return _response(existing)
    if blocker in {"draft_exists", "uncertain"} and existing is not None and not body.allow_resubmit:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CLAIM_DUPLICATE",
                "message": (
                    "A previous submission for this recipient already exists "
                    f"({existing.result_status or existing.status.value}); it may have created "
                    "a Gmail draft. Review it before resubmitting with allow_resubmit=true."
                ),
                "existing_submission_id": existing.id,
                "existing_status": existing.result_status or existing.status.value,
                "gmail_draft_id": existing.gmail_draft_id,
            },
        )

    submission = ClaimSubmission(
        id=new_id("clm"),
        case_id=case_id,
        status=InvestigationStatus.queued,
        mode="live",
        app_id=settings.creao_claim_app_id,
        recipient_email=body.recipient_email.strip(),
        requested_by=body.requested_by,
        pdf_supplied=body.include_recovery_pdf,
        pdf_filename="recovery-inventory.pdf" if body.include_recovery_pdf else None,
    )

    try:
        pdf_url = None
        if body.include_recovery_pdf:
            submission = submission.model_copy(
                update={"pdf_bytes": claim.build_recovery_pdf(case)}
            )
            pdf_url = claim.pdf_url_for(submission.id)
    except claim.ClaimError as exc:
        raise _error(exc) from exc

    request_data = body.model_dump()
    inputs = claim.build_inputs(
        submission=submission, case=case, request_data=request_data, pdf_url=pdf_url
    )

    with store.lock:
        store.claim_submissions[submission.id] = submission
    events.publish("claim.started", case_id, {"claim_submission": submission.model_dump(mode="json")})

    response.status_code = status.HTTP_202_ACCEPTED
    background.add_task(_execute, submission.id, inputs)
    return _response(submission)


@router.get(
    "/claim-submissions/{submission_id}",
    response_model=ClaimSubmissionResponse,
    summary="Claim submission status and result",
)
def get_claim_submission(submission_id: str) -> ClaimSubmissionResponse:
    submission = store.claim_submissions.get(submission_id)
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CLAIM_SUBMISSION_NOT_FOUND",
                    "message": f"Claim submission '{submission_id}' was not found."},
        )
    return _response(submission)


@router.get(
    "/cases/{case_id}/claim-submissions",
    response_model=list[ClaimSubmission],
    summary="Saved claim draft history for a case (newest first)",
)
def list_claim_submissions(case_id: str) -> list[ClaimSubmission]:
    get_case_or_404(case_id)
    return store.list_claim_submissions(case_id)


async def _execute(submission_id: str, inputs: dict) -> None:
    submission = store.claim_submissions[submission_id]
    try:
        started = await claim.start(submission, inputs=inputs)
        await claim.poll_and_collect(started.id)
    except claim.ClaimError as exc:
        logger.error("claim %s failed: %s (%s)", submission_id, exc.code, exc.message)
        _fail(submission_id, f"{exc.code}: {exc.message}")
    except creao.CreaoError as exc:
        # Transport-level failure (auth, run failed, artifact, timeout).
        logger.error("claim %s failed: %s (%s)", submission_id, exc.code, exc.message)
        _fail(submission_id, f"{exc.code}: {exc.message}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("claim %s failed unexpectedly", submission_id)
        _fail(submission_id, f"unexpected error: {exc}")


def _fail(submission_id: str, message: str) -> None:
    """Terminal. Never retried automatically — the outcome may be uncertain."""
    submission = store.claim_submissions[submission_id]
    failed = submission.model_copy(
        update={
            "status": InvestigationStatus.failed,
            "result_status": "draft_failed",
            "error": message[:1000],
            "finished_at": utcnow(),
        }
    )
    with store.lock:
        store.claim_submissions[submission_id] = failed
    events.publish("claim.completed", failed.case_id, {"claim_submission": failed.model_dump(mode="json")})
