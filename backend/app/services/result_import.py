"""Shared result-import pipeline.

Both the live investigation path (`routers/investigations._run`) and the
recovery path (`routers/recovery`) go through `import_agent_result`, so a
recovered run produces exactly the same rows as a fresh one — there is no second
copy of the mapping logic to drift.

Idempotency is keyed on `(investigation_id, agent_item_id)`: re-importing the
same agent output creates nothing new and *never* touches an item that already
exists. That is what protects human corrections on a retry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..models import (
    BBox,
    DamageLevel,
    EvidenceItem,
    Investigation,
    ItemStatus,
    utcnow,
)
from ..store import new_id, store
from .creao import AgentItem, AgentResult

logger = logging.getLogger(__name__)


@dataclass
class ImportReport:
    created: list[EvidenceItem] = field(default_factory=list)
    skipped: list[EvidenceItem] = field(default_factory=list)
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
    """Link an item to the upload behind its first supporting evidence ID."""
    for evidence_id in item.supporting_evidence_ids:
        if evidence_id in uploads_by_evidence:
            return uploads_by_evidence[evidence_id]
    return default


def import_agent_result(
    *,
    investigation: Investigation,
    case_id: str,
    result: AgentResult,
    uploads_by_evidence: dict[str, str],
    default_upload_id: str | None = None,
) -> ImportReport:
    """Create any missing items for this investigation. Safe to call twice."""
    report = ImportReport()

    with store.lock:
        existing = [
            i
            for i in store.list_items(case_id)
            if i.investigation_id == investigation.id
        ]
        by_agent_id = {
            i.agent_item_id: i for i in existing if i.agent_item_id
        }
        report.skipped = list(existing)

        for agent_item in result.items:
            key = agent_item.agent_item_id
            if key and key in by_agent_id:
                # Already imported — leave it alone so human edits survive.
                continue

            item = EvidenceItem(
                id=new_id("itm"),
                case_id=case_id,
                investigation_id=investigation.id,
                upload_id=_upload_for(agent_item, uploads_by_evidence, default_upload_id),
                room=agent_item.room,
                name=agent_item.name,
                category=agent_item.category,
                description=agent_item.description,
                condition=agent_item.condition,
                damage_level=_damage(agent_item.damage_level),
                confidence=agent_item.confidence,
                estimated_value=agent_item.estimated_value,
                currency=agent_item.currency or "USD",
                bbox=BBox(**agent_item.bbox) if agent_item.bbox else None,
                agent_reasoning=agent_item.reasoning,
                agent_item_id=agent_item.agent_item_id,
                supporting_evidence_ids=agent_item.supporting_evidence_ids,
                review_status=agent_item.review_status,
                agent_record=agent_item.raw or None,
                status=(
                    ItemStatus.human_confirmed
                    if agent_item.review_status == "confirmed"
                    else ItemStatus.ai_suggested
                ),
            )
            store.items[item.id] = item
            report.created.append(item)
            report.item_ids.append(item.id)

    report.item_ids = [i.id for i in report.skipped] + [i.id for i in report.created]

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
        "imported %d new item(s) for investigation %s (%d already present)",
        len(report.created),
        investigation.id,
        len(report.skipped),
    )
    return report
