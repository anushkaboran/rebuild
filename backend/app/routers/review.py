"""Review-ready inventory: the original CREAO fields, as the UI needs them.

`GET /api/cases/{case_id}/review` is read-only. It exists because the agent's
rich fields (brand, model, purchase_date, purchase_price, location_in_image,
visible_observations, user_confirmed_fields) were previously only reachable
through `agent_record`, which is deliberately excluded from API responses.

Rules enforced here:
  * agent values are returned verbatim — nothing is inferred or back-filled;
  * `purchase_price` (a string printed on a receipt) is never copied into
    `estimated_value`;
  * `currency` is `null` when the agent reported none;
  * both ids are present: `id` for PATCH /api/items/{id}, `agent_item_id` for
    traceability back to the CREAO result;
  * no storage paths, API keys, signed evidence URLs or artifact URLs.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status

from ..config import settings
from ..deps import get_case_or_404
from ..models import (
    CaseReview,
    FindingResolutionRequest,
    QuestionAnswerRequest,
    ReviewDecision,
    ReviewEvidence,
    ReviewFinding,
    ReviewItem,
    ReviewQuestion,
    ReviewStats,
)
from ..services import review_decisions, spacetime_db
from ..store import store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["review"])


def _fresh() -> str:
    """Make sure we answer from the database, not a stale cache."""
    if settings.store_backend == "spacetime":
        try:
            spacetime_db.hydrate()
            return "spacetimedb"
        except Exception:  # noqa: BLE001 - serve the cache, but say so
            logger.exception("review: could not refresh from SpacetimeDB, serving cache")
            return "memory (stale: SpacetimeDB unreachable)"
    return "memory"


@router.get(
    "/cases/{case_id}/review",
    response_model=CaseReview,
    summary="Full review-ready inventory state for a case",
)
def case_review(case_id: str, refresh: bool = Query(default=True)) -> CaseReview:
    case = get_case_or_404(case_id)
    source = _fresh() if refresh else "memory"

    uploads = store.list_uploads(case_id)
    items = store.list_items(case_id)
    investigations = store.list_investigations(case_id)

    evidence = _evidence(uploads, investigations)
    decisions = _decisions(items)

    findings = [
        ReviewFinding(**f.model_dump())
        for f in sorted(store.list_findings(case_id), key=lambda f: f.finding_id or f.id)
    ]
    questions = [
        ReviewQuestion(**q.model_dump())
        for q in sorted(store.list_questions(case_id), key=lambda q: q.question_id or q.id)
    ]

    review_items = [
        ReviewItem(
            id=item.id,
            agent_item_id=item.agent_item_id,
            investigation_id=item.investigation_id,
            name=item.name,
            room=item.room,
            category=item.category,
            brand=item.brand,
            model=item.model,
            purchase_date=item.purchase_date,
            purchase_price=item.purchase_price,
            currency=item.currency,
            location_in_image=item.location_in_image,
            visible_observations=item.visible_observations,
            condition=item.condition,
            supporting_evidence_ids=item.supporting_evidence_ids,
            user_confirmed_fields=item.user_confirmed_fields,
            review_status=item.review_status,
            damage_level=item.damage_level,
            confidence=item.confidence,
            estimated_value=item.estimated_value,
            status=item.status,
            revision=item.revision,
            updated_by=item.updated_by,
            updated_at=item.updated_at,
            reconciliation=item.reconciliation,
            possible_match_item_id=item.possible_match_item_id,
            agent_proposal=item.agent_proposal,
        )
        for item in items
    ]

    return CaseReview(
        case=case,
        evidence=evidence,
        items=review_items,
        findings=findings,
        questions=questions,
        decisions=decisions,
        investigations=investigations,
        question_decisions=[
            d for d in store.list_decisions(case_id) if d.target_type == "question"
        ],
        finding_decisions=[
            d for d in store.list_decisions(case_id) if d.target_type == "finding"
        ],
        stats=_stats(case_id, items),
        source=source,
    )


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _evidence(uploads, investigations) -> list[ReviewEvidence]:
    """Evidence metadata from persisted uploads, enriched by agent output.

    Upload rows are durable; the agent's per-file description/readability is
    only in the run payload. Nothing is fabricated: unmatched uploads simply
    carry `evidence_id: null` and no description.
    """
    from ..services import creao

    agent: dict[str, dict] = {}
    for investigation in investigations:
        payload = investigation.result_payload or {}
        for entry in creao.normalise_evidence(payload):
            if entry["filename"] and entry["filename"] not in agent:
                agent[entry["filename"]] = entry

    out: list[ReviewEvidence] = []
    for upload in uploads:
        meta = agent.get(upload.original_filename, {})
        out.append(
            ReviewEvidence(
                # Stable id assigned at upload time; payload metadata is only a
                # fallback for rows created before ids were persisted.
                evidence_id=upload.evidence_id or meta.get("evidence_id"),
                upload_id=upload.id,
                filename=upload.original_filename,
                evidence_type="photo" if upload.kind.value == "photo" else "receipt",
                content_type=upload.content_type,
                readable=meta.get("readable"),
                description=meta.get("description"),
                size_bytes=upload.size_bytes,
                # Local API route only — never a signed/public evidence URL.
                url=f"/api/uploads/{upload.id}/file",
            )
        )
    return out


def _decisions(items) -> list[ReviewDecision]:
    """Latest human edit per item, straight from the revision history."""
    out: list[ReviewDecision] = []
    for item in items:
        revisions = store.list_revisions(item.id)
        if not revisions:
            continue
        latest = max(revisions, key=lambda r: r.created_at)
        out.append(
            ReviewDecision(
                item_id=item.id,
                agent_item_id=item.agent_item_id,
                editor=latest.editor,
                note=latest.note,
                changes=latest.changes,
                created_at=latest.created_at,
            )
        )
    return out


def _stats(case_id: str, items) -> ReviewStats:
    revisions = [r for item in items for r in store.list_revisions(item.id)]
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status.value] = counts.get(item.status.value, 0) + 1
    return ReviewStats(
        case_id=case_id,
        total_items=len(items),
        ai_suggested=counts.get("ai_suggested", 0),
        human_confirmed=counts.get("human_confirmed", 0),
        human_corrected=counts.get("human_corrected", 0),
        rejected=counts.get("rejected", 0),
        total_estimated_value=round(sum(i.estimated_value or 0 for i in items), 2),
        currency=next((i.currency for i in items if i.currency), "USD"),
        contributors=sorted({r.editor for r in revisions}),
        last_activity_at=max(
            (r.created_at for r in revisions),
            default=max((i.updated_at for i in items), default=None),
        ),
    )


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #
def _raise(exc: review_decisions.DecisionError) -> None:
    detail = {"code": exc.code, "message": exc.message}
    if isinstance(exc, review_decisions.DecisionConflict):
        detail["current"] = exc.current
    raise HTTPException(status_code=exc.status_code, detail=detail)


@router.patch(
    "/cases/{case_id}/questions/{question_ref}",
    response_model=ReviewQuestion,
    summary="Save, update or clear a human answer to an investigator question",
    responses={
        400: {"description": "Invalid answer (e.g. missing reviewer)"},
        404: {"description": "Case or question not found"},
        409: {"description": "Stale revision; `detail.current` holds the saved decision"},
    },
)
def answer_question(
    case_id: str, question_ref: str, body: QuestionAnswerRequest
) -> ReviewQuestion:
    get_case_or_404(case_id)
    try:
        applied = review_decisions.answer_question(
            case_id=case_id,
            ref=question_ref,
            answer=body.answer,
            reviewer=body.reviewer,
            if_revision=body.if_revision,
        )
    except review_decisions.DecisionError as exc:
        _raise(exc)
    return ReviewQuestion(**applied.target.model_dump())


@router.patch(
    "/cases/{case_id}/findings/{finding_ref}",
    response_model=ReviewFinding,
    summary="Resolve an investigator finding",
    responses={
        400: {"description": "Invalid resolution (a rejection needs a reason)"},
        404: {"description": "Case or finding not found"},
        409: {"description": "Stale revision; `detail.current` holds the saved decision"},
    },
)
def resolve_finding(
    case_id: str, finding_ref: str, body: FindingResolutionRequest
) -> ReviewFinding:
    get_case_or_404(case_id)
    try:
        applied = review_decisions.resolve_finding(
            case_id=case_id,
            ref=finding_ref,
            resolution=body.resolution,
            reason=body.reason,
            reviewer=body.reviewer,
            if_revision=body.if_revision,
        )
    except review_decisions.DecisionError as exc:
        _raise(exc)
    return ReviewFinding(**applied.target.model_dump())
