"""SpacetimeDB persistence for the Rebuild backend.

Architecture (deliberate, documented in docs/spacetimedb.md):

    SpacetimeDB = durable, shared source of truth
    `store`     = write-through read cache used by the existing routers

Routers were written against the in-memory `store` and 45 tests depend on that
shape, so instead of rewriting every endpoint this module keeps SpacetimeDB
authoritative and mirrors between the two:

  * `hydrate()`  on startup — load every table into `store`
  * `sync()`     after each mutating request — push changed rows via reducers
  * `sql()`      used by tests/diagnostics

Writes go through reducers, never raw SQL, so the module's invariants hold
(idempotent upserts, duplicate-import guard, revision conflict detection).

`STORE_BACKEND=memory` (default, and what the test suite uses) keeps the pure
in-memory behaviour. `STORE_BACKEND=spacetime` is explicit — there is no silent
fallback: if the database is unreachable, health reports it and mutating
requests fail loudly.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Iterable

import httpx

from ..config import settings
from ..models import (
    Case,
    CaseStatus,
    Comment,
    DecisionEvent,
    Finding,
    Question,
    ResearchCandidate,
    ResearchRun,
    DamageLevel,
    EvidenceItem,
    Investigation,
    InvestigationStatus,
    ItemStatus,
    Member,
    Revision,
    Upload,
    UploadKind,
    utcnow,
)
from ..store import store

logger = logging.getLogger(__name__)

# id -> hash of the last payload successfully written
_synced: dict[str, str] = {}
_last_error: str | None = None


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def enabled() -> bool:
    return settings.store_backend == "spacetime"


def _url(path: str) -> str:
    return f"{settings.spacetimedb_url.rstrip('/')}/v1/database/{settings.spacetimedb_db}{path}"


def sql(query: str, timeout: float = 20.0) -> list[dict[str, Any]]:
    """Run read-only SQL and return rows as dicts."""
    response = httpx.post(
        _url("/sql"),
        content=query.encode(),
        headers={"Content-Type": "text/plain"},
        timeout=timeout,
    )
    response.raise_for_status()
    statements = response.json()
    rows: list[dict[str, Any]] = []
    for statement in statements:
        names = [
            element.get("name", {}).get("some")
            for element in statement.get("schema", {}).get("elements", [])
        ]
        for raw in statement.get("rows", []):
            rows.append({name: value for name, value in zip(names, raw)})
    return rows


def call(reducer: str, args: list[Any], timeout: float = 20.0) -> dict[str, Any]:
    """Invoke a reducer. Positional args, matching the module signature."""
    response = httpx.post(
        _url(f"/call/{reducer}"),
        json=args,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise SpacetimeError(
            f"{reducer} failed ({response.status_code}): {response.text[:300]}"
        )
    try:
        return response.json()
    except json.JSONDecodeError:
        return {}


class SpacetimeError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Serialisation (model -> reducer payload)
# --------------------------------------------------------------------------- #
def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _case_payload(case: Case) -> dict[str, Any]:
    return {
        "id": case.id,
        "title": case.title,
        "address": case.address,
        "disaster_type": case.disaster_type,
        "status": case.status.value,
        "notes": case.notes,
        "created_by": case.created_by,
        "upload_count": case.upload_count,
        "item_count": case.item_count,
        "members": [m.model_dump() for m in case.members],
        "created_at": _iso(case.created_at),
        "updated_at": _iso(case.updated_at),
    }


def _upload_payload(upload: Upload) -> dict[str, Any]:
    return {
        "id": upload.id,
        "case_id": upload.case_id,
        "kind": upload.kind.value,
        "original_filename": upload.original_filename,
        "content_type": upload.content_type,
        "size_bytes": upload.size_bytes,
        "sha256": upload.sha256,
        "uploaded_by": upload.uploaded_by,
        "evidence_id": upload.evidence_id,
        "url": upload.url,
        "stored_path": upload.stored_path,
        "created_at": _iso(upload.created_at),
    }


def _investigation_payload(inv: Investigation) -> dict[str, Any]:
    return {
        "id": inv.id,
        "case_id": inv.case_id,
        "upload_id": inv.upload_id,
        "status": inv.status.value,
        "mode": inv.mode,
        "agent_run_id": inv.agent_run_id,
        "conversation_id": inv.conversation_id,
        "model": inv.model,
        "credits_used": inv.credits_used,
        "evidence_transport": inv.evidence_transport,
        "evidence_ids": inv.evidence_ids,
        "summary": inv.summary,
        "error": inv.error,
        "recovered": inv.recovered,
        "follow_up": inv.follow_up,
        "review_answers_sent": inv.review_answers_sent,
        "items_created": inv.items_created,
        "items_reconciled": inv.items_reconciled,
        "items_flagged": inv.items_flagged,
        "item_ids": inv.item_ids,
        "requested_by": inv.requested_by,
        "created_at": _iso(inv.created_at),
        "finished_at": _iso(inv.finished_at),
    }


def _item_payload(item: EvidenceItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "case_id": item.case_id,
        "investigation_id": item.investigation_id,
        "upload_id": item.upload_id,
        "agent_item_id": item.agent_item_id,
        "room": item.room,
        "name": item.name,
        "category": item.category,
        "description": item.description,
        "condition": item.condition,
        "damage_level": item.damage_level.value,
        "confidence": item.confidence,
        "estimated_value": item.estimated_value,
        "currency": item.currency,
        "bbox": item.bbox.model_dump() if item.bbox else None,
        "agent_reasoning": item.agent_reasoning,
        "brand": item.brand,
        "model": item.model,
        "purchase_date": item.purchase_date,
        "purchase_price": item.purchase_price,
        "location_in_image": item.location_in_image,
        "visible_observations": item.visible_observations,
        "user_confirmed_fields": item.user_confirmed_fields,
        "reconciliation": item.reconciliation,
        "possible_match_item_id": item.possible_match_item_id,
        "agent_proposal": item.agent_proposal,
        "review_status": item.review_status,
        "status": item.status.value,
        "supporting_evidence_ids": item.supporting_evidence_ids,
        "agent_record": item.agent_record,
        "revision": item.revision,
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at),
        "updated_by": item.updated_by,
    }


def _revision_payload(rev: Revision) -> dict[str, Any]:
    return {
        "id": rev.id,
        "case_id": "",
        "item_id": rev.item_id,
        "editor": rev.editor,
        "note": rev.note,
        "changes": rev.changes,
        "created_at": _iso(rev.created_at),
    }


def _finding_payload(finding: Finding) -> dict[str, Any]:
    return {
        "id": finding.id,
        "case_id": finding.case_id,
        "investigation_id": finding.investigation_id,
        "finding_id": finding.finding_id,
        "finding_type": finding.finding_type,
        "item_ids": finding.item_ids,
        "evidence_ids": finding.evidence_ids,
        "explanation": finding.explanation,
        "status": finding.status,
        "resolution": finding.resolution,
        "resolution_reason": finding.resolution_reason,
        "resolved_by": finding.resolved_by,
        "resolved_at": _iso(finding.resolved_at),
        "revision": finding.revision,
    }


def _question_payload(question: Question) -> dict[str, Any]:
    return {
        "id": question.id,
        "case_id": question.case_id,
        "investigation_id": question.investigation_id,
        "question_id": question.question_id,
        "item_id": question.item_id,
        "question": question.question,
        "reason": question.reason,
        "evidence_ids": question.evidence_ids,
        "status": question.status,
        "resolution_reason": question.resolution_reason,
        "answer": question.answer,
        "answered_by": question.answered_by,
        "answered_at": _iso(question.answered_at),
        "revision": question.revision,
    }


def _decision_payload(event: DecisionEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "case_id": event.case_id,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "agent_ref": event.agent_ref,
        "revision": event.revision,
        "value": event.value,
        "reviewer": event.reviewer,
        "note": event.note,
        "created_at": _iso(event.created_at),
    }


def _research_run_payload(run: ResearchRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "case_id": run.case_id,
        "item_id": run.item_id,
        "status": run.status.value,
        "mode": run.mode,
        "app_id": run.app_id,
        "agent_run_id": run.agent_run_id,
        "thread_id": run.thread_id,
        "error": run.error,
        "region_currency": run.region_currency,
        "input_summary": run.input_summary,
        "queries_run": run.queries_run,
        "limitations": run.limitations,
        "candidate_ids": run.candidate_ids,
        "candidates_valid": run.candidates_valid,
        "requested_by": run.requested_by,
        "created_at": _iso(run.created_at),
        "finished_at": _iso(run.finished_at),
    }


def _research_candidate_payload(c: ResearchCandidate) -> dict[str, Any]:
    return {
        "id": c.id,
        "candidate_id": c.candidate_id,
        "run_id": c.run_id,
        "case_id": c.case_id,
        "item_id": c.item_id,
        "brand": c.brand,
        "model": c.model,
        "product_name": c.product_name,
        "product_url": c.product_url,
        "matching_features": c.matching_features,
        "differing_features": c.differing_features,
        # Stored as JSON inside the existing text column (no migration needed).
        "missing_verification": json.dumps(c.missing_verification),
        "price_as_displayed": c.price_as_displayed,
        "currency": c.currency,
        "source_accessed_at": c.source_accessed_at,
        "status": c.status,
        "created_at": _iso(c.created_at),
    }


def _comment_payload(comment: Comment) -> dict[str, Any]:
    return {
        "id": comment.id,
        "case_id": comment.case_id,
        "item_id": comment.item_id,
        "author": comment.author,
        "body": comment.body,
        "created_at": _iso(comment.created_at),
    }


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Push
# --------------------------------------------------------------------------- #
_PUSHERS = {
    "app_case": ("upsert_case", _case_payload),
    "upload": ("upsert_upload", _upload_payload),
    "investigation": ("upsert_investigation", _investigation_payload),
    "item": ("upsert_item", _item_payload),
    "revision": ("upsert_revision", _revision_payload),
    "comment": ("upsert_comment", _comment_payload),
    "finding": ("upsert_finding", _finding_payload),
    "question": ("upsert_question", _question_payload),
    "decision_event": ("upsert_decision_event", _decision_payload),
    "research_run": ("upsert_research_run", _research_run_payload),
    "research_candidate": ("upsert_research_candidate", _research_candidate_payload),
}


def sync() -> dict[str, int]:
    """Push rows that changed since the last sync. Idempotent by hash."""
    if not enabled():
        return {}
    global _last_error
    pushed: dict[str, int] = {}

    collections: list[tuple[str, Iterable[Any]]] = [
        ("app_case", store.cases.values()),
        ("upload", store.uploads.values()),
        ("investigation", store.investigations.values()),
        ("item", store.items.values()),
        ("revision", store.revisions.values()),
        ("comment", store.comments.values()),
        ("finding", store.findings.values()),
        ("question", store.questions.values()),
        ("decision_event", store.decision_events.values()),
        ("research_run", store.research_runs.values()),
        ("research_candidate", store.research_candidates.values()),
    ]

    try:
        for table, objects in collections:
            reducer, serialise = _PUSHERS[table]
            count = 0
            for obj in list(objects):
                payload = serialise(obj)
                digest = _hash(payload)
                if _synced.get(obj.id) == digest:
                    continue
                call(reducer, [json.dumps(payload, default=str)])
                _synced[obj.id] = digest
                count += 1
            if count:
                pushed[table] = count
    except (httpx.HTTPError, SpacetimeError) as exc:
        _last_error = str(exc)
        logger.error("SpacetimeDB sync failed: %s", exc)
        raise
    _last_error = None
    return pushed


# --------------------------------------------------------------------------- #
# Hydrate (pull)
# --------------------------------------------------------------------------- #
def _rows(table: str) -> list[dict[str, Any]]:
    return sql(f"SELECT * FROM {table}")


def _dt(value: Any) -> datetime:
    if not value:
        return utcnow()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return utcnow()


def _text_list(value: Any) -> list:
    """Read a list that may have been stored as JSON, plain text, or empty."""
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return [str(value)]
    if isinstance(parsed, list):
        return [str(entry) for entry in parsed]
    return [str(parsed)]


def _json(value: Any, default: Any) -> Any:
    if value in (None, "", "null"):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def hydrate() -> dict[str, int]:
    """Load the database into the in-memory cache. Called on startup."""
    if not enabled():
        return {}

    counts: dict[str, int] = {}

    cases: dict[str, Case] = {}
    for row in _rows("app_case"):
        members = [Member(**m) for m in _json(row.get("members_json"), [])]
        cases[row["id"]] = Case(
            id=row["id"],
            title=row["title"],
            address=row.get("address") or None,
            disaster_type=row.get("disaster_type") or "flood",
            status=CaseStatus(row.get("status") or "open"),
            notes=row.get("notes") or None,
            created_by=row.get("created_by") or "anonymous",
            members=members,
            upload_count=int(row.get("upload_count") or 0),
            item_count=int(row.get("item_count") or 0),
            created_at=_dt(row.get("created_at")),
            updated_at=_dt(row.get("updated_at")),
        )
    counts["cases"] = len(cases)

    uploads: dict[str, Upload] = {}
    for row in _rows("upload"):
        uploads[row["id"]] = Upload(
            id=row["id"],
            case_id=row["case_id"],
            kind=UploadKind(row.get("kind") or "photo"),
            original_filename=row.get("original_filename") or "",
            content_type=row.get("content_type") or "",
            size_bytes=int(row.get("size_bytes") or 0),
            sha256=row.get("sha256") or "",
            uploaded_by=row.get("uploaded_by") or "anonymous",
            evidence_id=row.get("evidence_id") or None,
            url=row.get("url") or "",
            stored_path=row.get("stored_path") or "",
            created_at=_dt(row.get("created_at")),
        )
    counts["uploads"] = len(uploads)

    investigations: dict[str, Investigation] = {}
    for row in _rows("investigation"):
        investigations[row["id"]] = Investigation(
            id=row["id"],
            case_id=row["case_id"],
            upload_id=row.get("upload_id") or "",
            status=InvestigationStatus(row.get("status") or "queued"),
            mode=row.get("mode") or "live",
            agent_run_id=row.get("agent_run_id") or None,
            conversation_id=row.get("conversation_id") or None,
            model=row.get("model") or None,
            credits_used=row.get("credits_used") or None,
            evidence_transport=row.get("evidence_transport") or None,
            evidence_ids=_json(row.get("evidence_ids_json"), []),
            summary=row.get("summary") or None,
            error=row.get("error") or None,
            recovered=bool(row.get("recovered")),
            follow_up=bool(row.get("follow_up")),
            review_answers_sent=int(row.get("review_answers_sent") or 0),
            items_created=int(row.get("items_created") or 0),
            items_reconciled=int(row.get("items_reconciled") or 0),
            items_flagged=int(row.get("items_flagged") or 0),
            item_ids=_json(row.get("item_ids_json"), []),
            requested_by=row.get("requested_by") or "anonymous",
            created_at=_dt(row.get("created_at")),
            finished_at=_dt(row.get("finished_at")) if row.get("finished_at") else None,
        )
    counts["investigations"] = len(investigations)

    items: dict[str, EvidenceItem] = {}
    for row in _rows("item"):
        items[row["id"]] = EvidenceItem(
            id=row["id"],
            case_id=row["case_id"],
            investigation_id=row.get("investigation_id") or None,
            upload_id=row.get("upload_id") or None,
            agent_item_id=row.get("agent_item_id") or None,
            room=row.get("room") or None,
            name=row["name"],
            category=row.get("category") or "other",
            description=row.get("description") or None,
            condition=row.get("condition") or None,
            damage_level=DamageLevel(row.get("damage_level") or "unknown"),
            confidence=float(row.get("confidence") or 0),
            estimated_value=row.get("estimated_value") or None,
            currency=row.get("currency") or None,
            bbox=_json(row.get("bbox_json"), None),
            agent_reasoning=row.get("agent_reasoning") or None,
            brand=row.get("brand") or None,
            model=row.get("model") or None,
            purchase_date=row.get("purchase_date") or None,
            purchase_price=row.get("purchase_price") or None,
            location_in_image=row.get("location_in_image") or None,
            visible_observations=row.get("visible_observations") or None,
            user_confirmed_fields=_json(row.get("user_confirmed_fields_json"), []),
            reconciliation=row.get("reconciliation") or "new",
            possible_match_item_id=row.get("possible_match_item_id") or None,
            agent_proposal=_json(row.get("agent_proposal_json"), None),
            review_status=row.get("review_status") or None,
            status=ItemStatus(row.get("status") or "ai_suggested"),
            supporting_evidence_ids=_json(row.get("supporting_evidence_ids_json"), []),
            agent_record=_json(row.get("agent_record_json"), None),
            revision=int(row.get("revision") or 1),
            created_at=_dt(row.get("created_at")),
            updated_at=_dt(row.get("updated_at")),
            updated_by=row.get("updated_by") or None,
        )
    counts["items"] = len(items)

    revisions: dict[str, Revision] = {}
    for row in _rows("revision"):
        revisions[row["id"]] = Revision(
            id=row["id"],
            item_id=row["item_id"],
            editor=row.get("editor") or "",
            note=row.get("note") or None,
            changes=_json(row.get("changes_json"), {}),
            created_at=_dt(row.get("created_at")),
        )
    counts["revisions"] = len(revisions)

    findings: dict[str, Finding] = {}
    for row in _rows("finding"):
        findings[row["id"]] = Finding(
            id=row["id"],
            case_id=row["case_id"],
            investigation_id=row.get("investigation_id") or None,
            finding_id=row.get("finding_id") or None,
            finding_type=row.get("finding_type") or None,
            item_ids=_json(row.get("item_ids_json"), []),
            evidence_ids=_json(row.get("evidence_ids_json"), []),
            explanation=row.get("explanation") or None,
            status=row.get("status") or "needs_review",
            resolution=row.get("resolution") or None,
            resolution_reason=row.get("resolution_reason") or None,
            resolved_by=row.get("resolved_by") or None,
            resolved_at=_dt(row.get("resolved_at")) if row.get("resolved_at") else None,
            revision=int(row.get("revision") or 0),
        )
    counts["findings"] = len(findings)

    questions: dict[str, Question] = {}
    for row in _rows("question"):
        questions[row["id"]] = Question(
            id=row["id"],
            case_id=row["case_id"],
            investigation_id=row.get("investigation_id") or None,
            question_id=row.get("question_id") or None,
            item_id=row.get("item_id") or None,
            question=row.get("question") or "",
            reason=row.get("reason") or None,
            evidence_ids=_json(row.get("evidence_ids_json"), []),
            status=row.get("status") or "open",
            resolution_reason=row.get("resolution_reason") or None,
            answer=row.get("answer") or None,
            answered_by=row.get("answered_by") or None,
            answered_at=_dt(row.get("answered_at")) if row.get("answered_at") else None,
            revision=int(row.get("revision") or 0),
        )
    counts["questions"] = len(questions)

    decisions: dict[str, DecisionEvent] = {}
    for row in _rows("decision_event"):
        decisions[row["id"]] = DecisionEvent(
            id=row["id"],
            case_id=row["case_id"],
            target_type=row.get("target_type") or "question",
            target_id=row.get("target_id") or "",
            agent_ref=row.get("agent_ref") or None,
            revision=int(row.get("revision") or 0),
            value=row.get("value") or None,
            reviewer=row.get("reviewer") or "anonymous",
            note=row.get("note") or None,
            created_at=_dt(row.get("created_at")),
        )
    counts["decision_events"] = len(decisions)

    research_runs: dict[str, ResearchRun] = {}
    for row in _rows("research_run"):
        research_runs[row["id"]] = ResearchRun(
            id=row["id"],
            case_id=row["case_id"],
            item_id=row["item_id"],
            status=InvestigationStatus(row.get("status") or "queued"),
            mode=row.get("mode") or "live",
            app_id=row.get("app_id") or None,
            agent_run_id=row.get("agent_run_id") or None,
            thread_id=row.get("thread_id") or None,
            error=row.get("error") or None,
            region_currency=row.get("region_currency") or None,
            input_summary=_json(row.get("input_summary_json"), {}),
            queries_run=_json(row.get("queries_run_json"), []),
            limitations=_json(row.get("limitations_json"), []),
            candidate_ids=_json(row.get("candidate_ids_json"), []),
            candidates_valid=int(row.get("candidates_valid") or 0),
            requested_by=row.get("requested_by") or "anonymous",
            created_at=_dt(row.get("created_at")),
            finished_at=_dt(row.get("finished_at")) if row.get("finished_at") else None,
        )
    counts["research_runs"] = len(research_runs)

    research_candidates: dict[str, ResearchCandidate] = {}
    for row in _rows("research_candidate"):
        research_candidates[row["id"]] = ResearchCandidate(
            id=row["id"],
            run_id=row["run_id"],
            case_id=row["case_id"],
            item_id=row["item_id"],
            candidate_id=row.get("candidate_id") or None,
            brand=row.get("brand") or None,
            model=row.get("model") or None,
            product_name=row.get("product_name") or None,
            product_url=row.get("product_url") or None,
            matching_features=_json(row.get("matching_features_json"), []),
            differing_features=_json(row.get("differing_features_json"), []),
            missing_verification=_text_list(row.get("missing_verification")),
            price_as_displayed=row.get("price_as_displayed") or None,
            currency=row.get("currency") or None,
            source_accessed_at=row.get("source_accessed_at") or None,
            status=row.get("status") or "unverified_possible_match",
            unverified=True,
            created_at=_dt(row.get("created_at")),
        )
    counts["research_candidates"] = len(research_candidates)

    comments: dict[str, Comment] = {}
    for row in _rows("comment"):
        comments[row["id"]] = Comment(
            id=row["id"],
            case_id=row["case_id"],
            item_id=row.get("item_id") or None,
            author=row.get("author") or "",
            body=row.get("body") or "",
            created_at=_dt(row.get("created_at")),
        )
    counts["comments"] = len(comments)

    with store.lock:
        store.cases.update(cases)
        store.uploads.update(uploads)
        store.investigations.update(investigations)
        store.items.update(items)
        store.revisions.update(revisions)
        store.comments.update(comments)
        store.findings.update(findings)
        store.questions.update(questions)
        store.decision_events.update(decisions)
        store.research_runs.update(research_runs)
        store.research_candidates.update(research_candidates)

    for obj in (
        list(cases.values())
        + list(uploads.values())
        + list(investigations.values())
        + list(items.values())
        + list(revisions.values())
        + list(comments.values())
        + list(findings.values())
        + list(questions.values())
        + list(decisions.values())
        + list(research_runs.values())
        + list(research_candidates.values())
    ):
        payload = _PUSHERS[_table_for(obj)][1](obj)
        _synced[obj.id] = _hash(payload)

    logger.info(
        "hydrated from SpacetimeDB: %s",
        ", ".join(f"{k}={v}" for k, v in counts.items()),
    )
    return counts


def _table_for(obj: Any) -> str:
    if isinstance(obj, Case):
        return "app_case"
    if isinstance(obj, Upload):
        return "upload"
    if isinstance(obj, Investigation):
        return "investigation"
    if isinstance(obj, EvidenceItem):
        return "item"
    if isinstance(obj, Revision):
        return "revision"
    if isinstance(obj, Comment):
        return "comment"
    if isinstance(obj, Finding):
        return "finding"
    if isinstance(obj, Question):
        return "question"
    if isinstance(obj, DecisionEvent):
        return "decision_event"
    if isinstance(obj, ResearchRun):
        return "research_run"
    if isinstance(obj, ResearchCandidate):
        return "research_candidate"
    raise TypeError(type(obj))


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #
def health() -> dict[str, Any]:
    info: dict[str, Any] = {
        "backend": settings.store_backend,
        "enabled": enabled(),
        "url": settings.spacetimedb_url,
        "database": settings.spacetimedb_db,
        "last_error": _last_error,
    }
    if not enabled():
        return info
    try:
        rows = sql("SELECT * FROM app_case")
        info["reachable"] = True
        info["cases_in_db"] = len(rows)
    except Exception as exc:  # noqa: BLE001 - health must never throw
        info["reachable"] = False
        info["error"] = str(exc)[:300]
    return info


def reset_sync_state() -> None:
    """Test helper: forget what has been pushed."""
    _synced.clear()
