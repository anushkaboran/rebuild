"""Question answers and finding resolutions.

Both write paths follow the same rules:

  * the agent's original text (question wording, finding explanation, evidence
    references) is never touched;
  * every accepted change bumps `revision` and appends a `DecisionEvent`, so the
    history is auditable;
  * `if_revision` must match the stored revision, otherwise `ConflictError`
    carries the current state for a 409 response;
  * a repeated submission of the same revision is treated as a conflict with the
    current state (no silent double-apply);
  * nothing here changes inventory items, `user_confirmed_fields` or receipt
    prices — a decision about a question is not a confirmation of an item.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import DecisionEvent, Finding, Question, utcnow
from ..store import new_id, store


class DecisionError(RuntimeError):
    code = "DECISION_INVALID"
    status_code = 400

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class DecisionNotFound(DecisionError):
    code = "DECISION_TARGET_NOT_FOUND"
    status_code = 404


class DecisionConflict(DecisionError):
    code = "REVISION_CONFLICT"
    status_code = 409

    def __init__(self, message: str, current: dict[str, Any]):
        super().__init__(message)
        self.current = current


@dataclass
class AppliedDecision:
    target: Question | Finding
    event: DecisionEvent


def find_question(case_id: str, ref: str) -> Question:
    """Accept either the backend id (`inv_x:q_001`) or the agent id (`q_001`)."""
    question = store.questions.get(ref)
    if question is not None and question.case_id == case_id:
        return question
    for candidate in store.list_questions(case_id):
        if candidate.question_id == ref:
            return candidate
    raise DecisionNotFound(
        f"Question '{ref}' was not found in case '{case_id}'.",
        code="QUESTION_NOT_FOUND",
    )


def find_finding(case_id: str, ref: str) -> Finding:
    finding = store.findings.get(ref)
    if finding is not None and finding.case_id == case_id:
        return finding
    for candidate in store.list_findings(case_id):
        if candidate.finding_id == ref:
            return candidate
    raise DecisionNotFound(
        f"Finding '{ref}' was not found in case '{case_id}'.",
        code="FINDING_NOT_FOUND",
    )


def _record(event: DecisionEvent) -> None:
    with store.lock:
        store.decision_events[event.id] = event


def answer_question(
    *,
    case_id: str,
    ref: str,
    answer: str | None,
    reviewer: str,
    if_revision: int,
) -> AppliedDecision:
    question = find_question(case_id, ref)
    if if_revision != question.revision:
        raise DecisionConflict(
            f"Question '{question.question_id or question.id}' is at revision "
            f"{question.revision}, not {if_revision}.",
            current=question.model_dump(mode="json"),
        )

    cleaned = (answer or "").strip()
    if cleaned and not reviewer:
        raise DecisionError(
            "A reviewer is required when saving an answer.", code="REVIEWER_REQUIRED"
        )

    clearing = not cleaned
    now = utcnow()
    updated = question.model_copy(
        update={
            "answer": None if clearing else cleaned,
            "answered_by": None if clearing else reviewer,
            "answered_at": None if clearing else now,
            # An answer only counts once it is saved.
            "status": "open" if clearing else "answered",
            "revision": question.revision + 1,
        }
    )
    event = DecisionEvent(
        id=new_id("dec"),
        case_id=case_id,
        target_type="question",
        target_id=question.id,
        agent_ref=question.question_id,
        revision=updated.revision,
        value=None if clearing else cleaned,
        reviewer=reviewer,
        note="cleared" if clearing else None,
        created_at=now,
    )
    with store.lock:
        store.questions[question.id] = updated
    _record(event)
    return AppliedDecision(target=updated, event=event)


def resolve_finding(
    *,
    case_id: str,
    ref: str,
    resolution: str,
    reason: str | None,
    reviewer: str,
    if_revision: int,
) -> AppliedDecision:
    finding = find_finding(case_id, ref)
    if if_revision != finding.revision:
        raise DecisionConflict(
            f"Finding '{finding.finding_id or finding.id}' is at revision "
            f"{finding.revision}, not {if_revision}.",
            current=finding.model_dump(mode="json"),
        )

    cleaned_reason = (reason or "").strip() or None
    if resolution == "rejected" and not cleaned_reason:
        raise DecisionError(
            "A reason is required when rejecting a finding.",
            code="REASON_REQUIRED",
        )
    if not reviewer:
        raise DecisionError(
            "A reviewer is required when resolving a finding.", code="REVIEWER_REQUIRED"
        )

    now = utcnow()
    updated = finding.model_copy(
        update={
            "resolution": resolution,
            "resolution_reason": cleaned_reason,
            "resolved_by": reviewer,
            "resolved_at": now,
            "revision": finding.revision + 1,
        }
    )
    event = DecisionEvent(
        id=new_id("dec"),
        case_id=case_id,
        target_type="finding",
        target_id=finding.id,
        agent_ref=finding.finding_id,
        revision=updated.revision,
        value=resolution,
        reviewer=reviewer,
        note=cleaned_reason,
        created_at=now,
    )
    with store.lock:
        store.findings[finding.id] = updated
    _record(event)
    return AppliedDecision(target=updated, event=event)


def decision_history(case_id: str, target_id: str) -> list[DecisionEvent]:
    return [d for d in store.list_decisions(case_id) if d.target_id == target_id]
