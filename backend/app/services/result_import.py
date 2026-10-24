"""Shared result-import pipeline with follow-up reconciliation.

Both the live investigation path and the recovery path go through
`import_agent_result`, so a recovered or repeated run behaves exactly like a
fresh one.

Reconciliation rules (follow-up investigations):

  * an agent item whose `agent_item_id` already exists in the case reuses that
    backend item — the backend id, revision and human decisions are preserved;
  * if that item has any human decision (status change, revision > 1, or
    confirmed fields) the incoming AI values are **not** written to it; they are
    stored in `agent_proposal` for a human to look at;
  * if the agent changed an item id but the evidence suggests the same object
    (same normalised name AND at least one shared evidence id) a new row is
    created flagged `identity_conflict` with `possible_match_item_id` — never a
    silent merge;
  * a genuinely unmatched proposal becomes a new item (`reconciliation="new"`);
  * findings and questions are matched on `(case_id, agent id)`, so a follow-up
    run updates their agent text while every human answer/resolution, reviewer,
    timestamp and revision survives untouched.

Re-importing the same result twice is idempotent: matching happens before any
insert, so no duplicates are created.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..models import (
    BBox,
    DamageLevel,
    EvidenceItem,
    Finding,
    Investigation,
    ItemStatus,
    Question,
    utcnow,
)
from ..store import new_id, store
from .creao import AgentItem, AgentResult

logger = logging.getLogger(__name__)


@dataclass
class ImportReport:
    created: list[EvidenceItem] = field(default_factory=list)
    matched: list[EvidenceItem] = field(default_factory=list)
    flagged: list[EvidenceItem] = field(default_factory=list)
    item_ids: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.item_ids)


def _damage(value: str) -> DamageLevel:
    try:
        return DamageLevel(value)
    except ValueError:
        return DamageLevel.unknown


def _upload_for(item: AgentItem, uploads_by_evidence: dict[str, str], default: str | None) -> str | None:
    for evidence_id in item.supporting_evidence_ids:
        if evidence_id in uploads_by_evidence:
            return uploads_by_evidence[evidence_id]
    return default


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _human_touched(item: EvidenceItem) -> bool:
    """True when a person has decided anything about this row."""
    return (
        item.status != ItemStatus.ai_suggested
        or item.revision > 1
        or bool(item.user_confirmed_fields)
        or bool(item.updated_by)
    )


def _proposal(agent_item: AgentItem) -> dict:
    """Snapshot of what the latest run proposed (kept out of the human columns)."""
    return {
        "name": agent_item.name,
        "room": agent_item.room,
        "brand": agent_item.brand,
        "model": agent_item.model,
        "purchase_date": agent_item.purchase_date,
        "purchase_price": agent_item.purchase_price,
        "currency": agent_item.currency,
        "location_in_image": agent_item.location_in_image,
        "visible_observations": agent_item.visible_observations,
        "supporting_evidence_ids": agent_item.supporting_evidence_ids,
        "agent_item_id": agent_item.agent_item_id,
    }


def _new_item(
    *,
    case_id: str,
    investigation: Investigation,
    agent_item: AgentItem,
    upload_id: str | None,
    reconciliation: str,
    possible_match_item_id: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        id=new_id("itm"),
        case_id=case_id,
        investigation_id=investigation.id,
        upload_id=upload_id,
        room=agent_item.room,
        name=agent_item.name,
        category=agent_item.category,
        description=agent_item.description,
        condition=agent_item.condition,
        damage_level=_damage(agent_item.damage_level),
        confidence=agent_item.confidence,
        estimated_value=agent_item.estimated_value,
        currency=agent_item.currency,
        bbox=BBox(**agent_item.bbox) if agent_item.bbox else None,
        agent_reasoning=agent_item.reasoning,
        agent_item_id=agent_item.agent_item_id,
        supporting_evidence_ids=agent_item.supporting_evidence_ids,
        review_status=agent_item.review_status,
        brand=agent_item.brand,
        model=agent_item.model,
        purchase_date=agent_item.purchase_date,
        purchase_price=agent_item.purchase_price,
        location_in_image=agent_item.location_in_image,
        visible_observations=agent_item.visible_observations,
        user_confirmed_fields=agent_item.user_confirmed_fields,
        agent_record=agent_item.raw or None,
        reconciliation=reconciliation,
        possible_match_item_id=possible_match_item_id,
        status=(
            ItemStatus.human_confirmed
            if agent_item.review_status == "confirmed"
            else ItemStatus.ai_suggested
        ),
    )


def import_agent_result(
    *,
    investigation: Investigation,
    case_id: str,
    result: AgentResult,
    uploads_by_evidence: dict[str, str],
    default_upload_id: str | None = None,
) -> ImportReport:
    """Reconcile an agent result into the case. Safe to call repeatedly."""
    report = ImportReport()

    with store.lock:
        case_items = store.list_items(case_id)
        by_agent_id: dict[str, EvidenceItem] = {
            i.agent_item_id: i for i in case_items if i.agent_item_id
        }

        for agent_item in result.items:
            key = agent_item.agent_item_id
            upload_id = _upload_for(agent_item, uploads_by_evidence, default_upload_id)
            existing = by_agent_id.get(key) if key else None

            if existing is not None:
                # Same belonging seen before: never duplicate, never demote.
                merged = existing.model_copy(
                    update={
                        "investigation_id": existing.investigation_id or investigation.id,
                        "supporting_evidence_ids": sorted(
                            set(existing.supporting_evidence_ids)
                            | set(agent_item.supporting_evidence_ids)
                        ),
                        "reconciliation": "matched",
                    }
                )
                if _human_touched(existing):
                    # Keep every human value; park the AI proposal alongside.
                    merged = merged.model_copy(
                        update={"agent_proposal": _proposal(agent_item)}
                    )
                else:
                    merged = merged.model_copy(
                        update={
                            "name": agent_item.name,
                            "room": agent_item.room or existing.room,
                            "brand": agent_item.brand,
                            "model": agent_item.model,
                            "purchase_date": agent_item.purchase_date,
                            "purchase_price": agent_item.purchase_price,
                            "currency": agent_item.currency,
                            "location_in_image": agent_item.location_in_image,
                            "visible_observations": agent_item.visible_observations,
                            "description": agent_item.description,
                            "condition": agent_item.condition,
                            "agent_record": agent_item.raw or existing.agent_record,
                            "updated_at": utcnow(),
                        }
                    )
                store.items[existing.id] = merged
                by_agent_id[key] = merged
                report.matched.append(merged)
                continue

            # No id match. Only reconcile when identity evidence is trustworthy.
            possible = _possible_same_object(agent_item, case_items)
            if possible is not None:
                flagged = _new_item(
                    case_id=case_id,
                    investigation=investigation,
                    agent_item=agent_item,
                    upload_id=upload_id,
                    reconciliation="identity_conflict",
                    possible_match_item_id=possible.id,
                )
                store.items[flagged.id] = flagged
                report.flagged.append(flagged)
                report.created.append(flagged)
                continue

            created = _new_item(
                case_id=case_id,
                investigation=investigation,
                agent_item=agent_item,
                upload_id=upload_id,
                reconciliation="new",
            )
            store.items[created.id] = created
            report.created.append(created)
            if key:
                by_agent_id[key] = created

        report.item_ids = (
            [i.id for i in report.matched]
            + [i.id for i in report.flagged]
            + [i.id for i in report.created if i not in report.flagged]
        )

        _reconcile_findings(case_id, investigation, result)
        _reconcile_questions(case_id, investigation, result)

    case = store.get_case(case_id)
    if case is not None:
        with store.lock:
            store.cases[case_id] = case.model_copy(
                update={
                    "item_count": len(store.list_items(case_id)),
                    "updated_at": utcnow(),
                }
            )

    logger.info(
        "import for %s: %d new, %d matched, %d flagged",
        investigation.id,
        len(report.created) - len(report.flagged),
        len(report.matched),
        len(report.flagged),
    )
    return report


def _possible_same_object(agent_item: AgentItem, case_items: list[EvidenceItem]) -> EvidenceItem | None:
    """Find a likely match across a changed agent id — flag, never merge.

    Requires *both* a normalised-name match and a shared evidence reference;
    a generic name or category alone is never enough.
    """
    name = _norm(agent_item.name)
    if not name:
        return None
    evidence = set(agent_item.supporting_evidence_ids)
    for candidate in case_items:
        if not candidate.agent_item_id or candidate.agent_item_id == agent_item.agent_item_id:
            continue
        if _norm(candidate.name) != name:
            continue
        if evidence & set(candidate.supporting_evidence_ids):
            return candidate
    return None


# --------------------------------------------------------------------------- #
# Findings / questions: agent text updates, human decisions never move
# --------------------------------------------------------------------------- #
def _reconcile_findings(case_id: str, investigation: Investigation, result: AgentResult) -> None:
    from . import creao

    existing = {
        f.finding_id: f for f in store.findings.values() if f.case_id == case_id and f.finding_id
    }
    for raw in creao.normalise_findings(result.payload or {}):
        fid = raw.get("finding_id")
        if not fid:
            continue
        current = existing.get(fid)
        if current is not None:
            # Agent text may be refreshed; the human decision stays.
            store.findings[current.id] = current.model_copy(
                update={
                    "finding_type": raw["finding_type"],
                    "item_ids": raw["item_ids"],
                    "evidence_ids": raw["evidence_ids"],
                    "explanation": raw["explanation"],
                }
            )
            continue
        new_id_ = f"{case_id}:{fid}"
        store.findings[new_id_] = Finding(
            id=new_id_, case_id=case_id, investigation_id=investigation.id, **raw
        )


def _reconcile_questions(case_id: str, investigation: Investigation, result: AgentResult) -> None:
    from . import creao

    existing = {
        q.question_id: q for q in store.questions.values() if q.case_id == case_id and q.question_id
    }
    for raw in creao.normalise_questions(result.payload or {}):
        qid = raw.get("question_id")
        if not qid:
            continue
        current = existing.get(qid)
        if current is not None:
            store.questions[current.id] = current.model_copy(
                update={
                    "item_id": raw["item_id"],
                    "question": raw["question"],
                    "reason": raw["reason"],
                }
            )
            continue
        new_id_ = f"{case_id}:{qid}"
        store.questions[new_id_] = Question(
            id=new_id_, case_id=case_id, investigation_id=investigation.id, **raw
        )
