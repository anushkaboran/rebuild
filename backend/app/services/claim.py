"""Rebuild Claim Submission Assistant — Gmail DRAFT only.

The agent's SKILL.md is the contract; this module only builds inputs, validates
the result file and tracks state. The backend never talks to Gmail: the agent
creates one unsent draft, and a successful run is only `draft_ready` when the
agent reports a real `gmail_draft_id`.

Safety rules implemented here:
  * duplicate prevention — one active submission per case+recipient, and no new
    agent run when a previous attempt may already have produced a draft;
  * no automatic retries after an uncertain outcome (`draft_failed` is terminal
    for that submission; starting another requires `allow_resubmit`);
  * `draft_ready` requires a non-empty `gmail_draft_id`, and the other statuses
    require it to be null;
  * the ten-field result schema is enforced exactly — extras are rejected;
  * `reviewed_record` separates confirmed facts from agent observations, and a
    human confirmation exists only when a person recorded one.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import settings
from ..models import (
    CLAIM_RESULT_KEYS,
    CLAIM_RESULT_STATUSES,
    Case,
    ClaimSubmission,
    EvidenceItem,
    InvestigationStatus,
    ItemStatus,
    utcnow,
)
from ..store import store
from . import creao, evidence_urls, pdf

logger = logging.getLogger(__name__)

ACTIVE = {InvestigationStatus.queued, InvestigationStatus.running}


class ClaimError(RuntimeError):
    code = "CLAIM_ERROR"
    status_code = 502

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class ClaimNotConfigured(ClaimError):
    code = "CLAIM_NOT_CONFIGURED"
    status_code = 503


class ClaimInvalidResult(ClaimError):
    code = "CLAIM_INVALID_RESULT"
    status_code = 502


class ClaimDuplicate(ClaimError):
    code = "CLAIM_DUPLICATE"
    status_code = 409


def ensure_ready() -> None:
    if creao.is_mock():
        raise ClaimError(
            "Claim submissions are disabled in mock mode (CREAO_MODE=mock).",
            code="CREAO_MODE_MOCK",
            status_code=409,
        )
    missing = settings.claim_missing_settings
    if missing:
        raise ClaimNotConfigured(
            "Claim assistant is not configured: " + ", ".join(missing) + "."
        )


def client() -> creao.CreaoAppClient:
    return creao.CreaoAppClient(
        app_id=settings.creao_claim_app_id, api_key=settings.creao_claim_api_key
    )


# --------------------------------------------------------------------------- #
# reviewed_record
# --------------------------------------------------------------------------- #
def _human_confirmed_values(item: EvidenceItem) -> dict[str, Any]:
    """Only fields a person actually confirmed, with who and when."""
    confirmed: dict[str, Any] = {}
    for revision in store.list_revisions(item.id):
        for field, value in (revision.changes or {}).items():
            if field == "status":
                continue
            confirmed[field] = {
                "value": value,
                "confirmed_by": revision.editor,
                "confirmed_at": revision.created_at.isoformat(),
            }
    return confirmed


def _inventory_item(item: EvidenceItem) -> dict[str, Any]:
    confirmed = _human_confirmed_values(item)
    # A value is only "confirmed" when a human recorded it, or the agent itself
    # marked the field in user_confirmed_fields.
    agent_confirmed = set(item.user_confirmed_fields or [])

    def value(field: str, raw: Any) -> dict[str, Any]:
        if field in confirmed:
            return {"value": confirmed[field]["value"], "confirmed": True,
                    "confirmed_by": confirmed[field]["confirmed_by"]}
        if field in agent_confirmed:
            return {"value": raw, "confirmed": True, "confirmed_by": None}
        return {"value": raw, "confirmed": False}

    return {
        "item_id": item.id,
        "agent_item_id": item.agent_item_id,
        "name": item.name,
        "room": item.room,
        "review_status": item.review_status,
        "item_status": item.status.value,
        "brand": value("brand", item.brand),
        "model": value("model", item.model),
        "condition": value("condition", item.condition),
        "estimated_value": value("estimated_value", item.estimated_value),
        "purchase_price": value("purchase_price", item.purchase_price),
        "supporting_evidence_ids": item.supporting_evidence_ids,
    }


def build_reviewed_record(case: Case) -> dict[str, Any]:
    """The four sections the agent requires, built only from saved state."""
    items = store.list_items(case.id)
    uploads = {u.id: u for u in store.list_uploads(case.id)}

    inventory = [_inventory_item(item) for item in items]

    evidence = [
        {
            "evidence_id": upload.evidence_id,
            "upload_id": upload.id,
            "filename": upload.original_filename,
            "evidence_type": "photo" if upload.kind.value == "photo" else "receipt",
            "readable": None,
        }
        for upload in uploads.values()
    ]

    human_confirmations: list[dict[str, Any]] = []
    for item in items:
        for field, entry in _human_confirmed_values(item).items():
            human_confirmations.append(
                {
                    "target_type": "item",
                    "item_id": item.id,
                    "agent_item_id": item.agent_item_id,
                    "field": field,
                    "value": entry["value"],
                    "confirmed_by": entry["confirmed_by"],
                    "confirmed_at": entry["confirmed_at"],
                }
            )
    for question in store.list_questions(case.id):
        if question.answer:
            human_confirmations.append(
                {
                    "target_type": "question",
                    "question_id": question.question_id,
                    "field": "answer",
                    "value": question.answer,
                    "confirmed_by": question.answered_by,
                    "confirmed_at": question.answered_at.isoformat()
                    if question.answered_at
                    else None,
                }
            )

    unresolved: list[dict[str, Any]] = []
    for question in store.list_questions(case.id):
        if not question.answer:
            unresolved.append(
                {
                    "kind": "question",
                    "question_id": question.question_id,
                    "text": question.question,
                }
            )
    for finding in store.list_findings(case.id):
        if finding.resolution in (None, "", "needs_more_evidence", "unresolved"):
            unresolved.append(
                {
                    "kind": "finding",
                    "finding_id": finding.finding_id,
                    "type": finding.finding_type,
                    "text": finding.explanation,
                }
            )
    for item in items:
        if item.reconciliation == "identity_conflict":
            unresolved.append(
                {
                    "kind": "identity_conflict",
                    "item_id": item.id,
                    "agent_item_id": item.agent_item_id,
                    "possible_match_item_id": item.possible_match_item_id,
                }
            )

    return {
        "case_ref": case.id,
        "inventory": inventory,
        "evidence_references": evidence,
        "human_confirmations": human_confirmations,
        "unresolved_issues": unresolved,
    }


def build_case_summary(case: Case) -> str:
    items = store.list_items(case.id)
    confirmed = sum(1 for i in items if _human_confirmed_values(i))
    parts = [f"Post-flood renter claim for {case.title}."]
    if case.address:
        parts.append(f"Property: {case.address}.")
    parts.append(f"Disaster: {case.disaster_type}.")
    if case.notes:
        parts.append(f"Case notes: {case.notes}")
    parts.append(
        f"{len(items)} belonging(s) recorded; {confirmed} with human-confirmed fields."
    )
    return " ".join(parts)[:4000]


# --------------------------------------------------------------------------- #
# Result validation
# --------------------------------------------------------------------------- #
def validate_result(payload: Any, *, submission: ClaimSubmission) -> dict[str, Any]:
    """Enforce the ten-key schema and the status-specific rules."""
    if not isinstance(payload, dict):
        raise ClaimInvalidResult("claim-email-draft.json is not a JSON object.")

    keys = set(payload)
    expected = set(CLAIM_RESULT_KEYS)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise ClaimInvalidResult(
            "claim-email-draft.json must contain exactly the ten contract keys"
            + (f"; missing: {', '.join(missing)}" if missing else "")
            + (f"; unexpected: {', '.join(extra)}" if extra else "")
            + "."
        )

    if payload["case_ref"] != submission.case_id:
        raise ClaimInvalidResult(
            f"case_ref mismatch: agent returned '{payload['case_ref']}', "
            f"expected '{submission.case_id}'."
        )

    status_value = payload["status"]
    if status_value not in CLAIM_RESULT_STATUSES:
        raise ClaimInvalidResult(
            f"status '{status_value}' is not one of {', '.join(CLAIM_RESULT_STATUSES)}."
        )

    # The recipient is copied exactly; the agent must not rewrite it.
    if payload["recipient_email"] != submission.recipient_email:
        raise ClaimInvalidResult(
            "recipient_email was altered; the agent must copy it byte-for-byte."
        )

    draft_id = payload.get("gmail_draft_id")
    if status_value == "draft_ready" and not (isinstance(draft_id, str) and draft_id.strip()):
        raise ClaimInvalidResult(
            "status 'draft_ready' requires a non-empty gmail_draft_id."
        )
    if status_value in {"needs_information", "draft_failed"} and draft_id:
        raise ClaimInvalidResult(
            f"status '{status_value}' must not report a gmail_draft_id."
        )

    attachment = payload.get("attachment_filename")
    if not submission.pdf_supplied and attachment:
        raise ClaimInvalidResult(
            "attachment_filename is set but no recovery PDF was supplied."
        )
    if submission.pdf_supplied and status_value == "draft_ready" and not attachment:
        raise ClaimInvalidResult(
            "a recovery PDF was supplied but the draft reports no attachment_filename."
        )

    for key in ("missing_information", "warnings"):
        value = payload[key]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ClaimInvalidResult(f"`{key}` must be an array of strings.")

    for key in ("subject", "body_text", "gmail_message_id", "attachment_filename"):
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            raise ClaimInvalidResult(f"`{key}` must be a string or null.")

    return payload


# --------------------------------------------------------------------------- #
# Duplicate protection
# --------------------------------------------------------------------------- #
def existing_blocker(
    case_id: str, recipient_email: str
) -> tuple[str, ClaimSubmission | None]:
    """Return ("active"|"draft_exists"|"uncertain"|"none", submission)."""
    for submission in store.list_claim_submissions(case_id):
        if submission.recipient_email.lower() != recipient_email.lower():
            continue
        if submission.status in ACTIVE:
            return "active", submission
    for submission in store.list_claim_submissions(case_id):
        if submission.recipient_email.lower() != recipient_email.lower():
            continue
        if submission.result_status == "draft_ready" and submission.gmail_draft_id:
            return "draft_exists", submission
    # A failed attempt might still have created a draft (uncertain outcome).
    for submission in store.list_claim_submissions(case_id):
        if submission.recipient_email.lower() != recipient_email.lower():
            continue
        if submission.status == InvestigationStatus.failed or submission.result_status == "draft_failed":
            return "uncertain", submission
    return "none", None


# --------------------------------------------------------------------------- #
# Run lifecycle
# --------------------------------------------------------------------------- #
def build_inputs(
    *,
    submission: ClaimSubmission,
    case: Case,
    request_data: dict[str, Any],
    pdf_url: str | None,
) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "case_ref": case.id,
        "case_summary": build_case_summary(case),
        # JSON-serialized STRING, as the agent expects.
        "reviewed_record": json.dumps(build_reviewed_record(case)),
        "sender_name": request_data["sender_name"],
        "insurer_name": request_data["insurer_name"],
        "recipient_email": request_data["recipient_email"],
    }
    for optional in ("policy_number", "claim_number", "policy_type", "additional_notes"):
        value = request_data.get(optional)
        if value:
            inputs[optional] = value
    if pdf_url:
        inputs["recovery_pdf"] = pdf_url
    return inputs


async def start(
    submission: ClaimSubmission, *, inputs: dict[str, Any]
) -> ClaimSubmission:
    ensure_ready()
    async with client() as api:
        created = await api.create_run(inputs)
    agent_run_id = created.get("id")
    if not agent_run_id:
        raise ClaimError(
            f"Claim assistant create-run response had no `id`: {sorted(created.keys())}",
            code="CLAIM_BAD_RESPONSE",
        )
    updated = submission.model_copy(
        update={
            "status": InvestigationStatus.running,
            "agent_run_id": agent_run_id,
            "app_id": settings.creao_claim_app_id,
            "mode": "live",
        }
    )
    with store.lock:
        store.claim_submissions[submission.id] = updated
    logger.info("claim submission %s started on app %s", agent_run_id, settings.creao_claim_app_id)
    return updated


async def poll_and_collect(submission_id: str) -> ClaimSubmission:
    submission = store.claim_submissions[submission_id]
    async with client() as api:
        final = await api.poll_run(submission.agent_run_id or "")
        payload = await _fetch_payload(api, final)

    validate_result(payload, submission=submission)

    updated = submission.model_copy(
        update={
            "status": InvestigationStatus.complete,
            "thread_id": final.get("threadId"),
            "result_status": payload["status"],
            "subject": payload["subject"],
            "body_text": payload["body_text"],
            "attachment_filename": payload["attachment_filename"],
            "gmail_draft_id": payload["gmail_draft_id"],
            "gmail_message_id": payload["gmail_message_id"],
            "missing_information": payload["missing_information"],
            "warnings": payload["warnings"],
            "error": None,
            "finished_at": utcnow(),
        }
    )
    with store.lock:
        store.claim_submissions[submission_id] = updated
    logger.info(
        "claim submission %s complete: %s (draft=%s)",
        submission_id,
        updated.result_status,
        bool(updated.gmail_draft_id),
    )
    return updated


async def _fetch_payload(api: creao.CreaoAppClient, final: dict[str, Any]) -> Any:
    result = final.get("result") or {}
    artifacts = [a for a in (result.get("artifacts") or []) if isinstance(a, dict)]
    pool = [
        a for a in artifacts
        if "claim-email-draft" in " ".join(
            str(a.get(k) or "") for k in ("title", "name", "id", "path")
        ).lower()
    ] or [a for a in artifacts if str(a.get("type") or "").lower().startswith("application/json")]

    for artifact in pool:
        url = artifact.get("url")
        if not url:
            continue
        try:
            return json.loads((await api.download_artifact(url)).decode())
        except Exception:  # noqa: BLE001
            logger.warning("claim artifact %s unreadable", artifact.get("title"))

    text = result.get("text")
    if isinstance(text, str):
        parsed = creao._parse_json_blob(text)
        if isinstance(parsed, dict) and "case_ref" in parsed:
            return parsed

    raise ClaimInvalidResult(
        "the run completed but no readable claim-email-draft.json was found."
    )


# --------------------------------------------------------------------------- #
# Recovery PDF
# --------------------------------------------------------------------------- #
def build_recovery_pdf(case: Case) -> bytes:
    """Reuse the existing recovery-record PDF generator."""
    items = store.list_items(case.id)
    revisions = [r for item in items for r in store.list_revisions(item.id)]
    return pdf.build_case_pdf(
        case=case,
        items=items,
        uploads=store.list_uploads(case.id),
        investigations=store.list_investigations(case.id),
        revisions=revisions,
    )


def pdf_url_for(submission_id: str) -> str:
    """Signed HTTPS URL for the generated PDF (never a localhost path)."""
    if not settings.rebuild_public_base_url:
        raise ClaimError(
            "REBUILD_PUBLIC_BASE_URL is not set, so the recovery PDF has no address "
            "the agent could download. Disable include_recovery_pdf or configure the "
            "public origin.",
            code="PUBLIC_BASE_URL_NOT_CONFIGURED",
            status_code=409,
        )
    try:
        return evidence_urls.claim_pdf_url(submission_id)
    except evidence_urls.EvidenceUrlError as exc:
        raise ClaimError(exc.message, code="PUBLIC_BASE_URL_NOT_CONFIGURED", status_code=409) from exc
