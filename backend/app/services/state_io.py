"""Snapshot export / import for in-memory state.

Why this exists: `STORE_BACKEND=memory` keeps everything in the process, so a
restart destroys it. This module defines one portable snapshot document and a
strict, ID-preserving importer.

Design rules:
  * IDs are preserved exactly — nothing is regenerated, no `item_ref` rewritten.
  * Referential integrity is validated *before* anything is written.
  * Conflicts are surfaced, never silently resolved. A human-decided item
    (revision > 1, non-`ai_suggested` status, or confirmed fields) is never
    overwritten by an import.
  * Import is idempotent: importing the same snapshot twice changes nothing.
  * No CREAO calls of any kind happen here.

What a snapshot can never contain (the API deliberately excludes it):
  * `Upload.stored_path`      → only the file on disk in `backend/uploads/`
  * `EvidenceItem.agent_record`
  * `Investigation.result_payload`
  * `ResearchRun.input_snapshot`
See docs/state-export.md for the consequences.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models import (
    Case,
    Comment,
    DecisionEvent,
    EvidenceItem,
    Finding,
    Investigation,
    Question,
    ResearchCandidate,
    ResearchRun,
    Revision,
    Upload,
    utcnow,
)
from ..store import store

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1

# Collections in the snapshot document (name → model)
MODELS = {
    "cases": Case,
    "uploads": Upload,
    "investigations": Investigation,
    "items": EvidenceItem,
    "revisions": Revision,
    "comments": Comment,
    "findings": Finding,
    "questions": Question,
    "research_runs": ResearchRun,
    "research_candidates": ResearchCandidate,
    # decision_events are not a pydantic model in the store parity set
}

STORE_ATTRS = {
    "cases": "cases",
    "uploads": "uploads",
    "investigations": "investigations",
    "items": "items",
    "revisions": "revisions",
    "comments": "comments",
    "findings": "findings",
    "questions": "questions",
    "research_runs": "research_runs",
    "research_candidates": "research_candidates",
}


class SnapshotError(RuntimeError):
    code = "SNAPSHOT_INVALID"
    status_code = 400

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None,
                 conflicts: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.conflicts = conflicts or []


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def build_snapshot() -> dict[str, Any]:
    """Everything the API is allowed to expose, plus decision history."""
    with store.lock:
        snapshot: dict[str, Any] = {
            "snapshot_version": SNAPSHOT_VERSION,
            "exported_at": utcnow().isoformat(),
            "counts": {},
            "cases": [c.model_dump(mode="json") for c in store.cases.values()],
            "uploads": [u.model_dump(mode="json") for u in store.uploads.values()],
            "investigations": [i.model_dump(mode="json") for i in store.investigations.values()],
            "items": [i.model_dump(mode="json") for i in store.items.values()],
            "revisions": [r.model_dump(mode="json") for r in store.revisions.values()],
            "comments": [c.model_dump(mode="json") for c in store.comments.values()],
            "findings": [f.model_dump(mode="json") for f in store.findings.values()],
            "questions": [q.model_dump(mode="json") for q in store.questions.values()],
            "decision_events": [d.model_dump(mode="json") for d in store.decision_events.values()],
            "research_runs": [r.model_dump(mode="json") for r in store.research_runs.values()],
            "research_candidates": [
                c.model_dump(mode="json") for c in store.research_candidates.values()
            ],
            "warnings": [],
        }
    snapshot["counts"] = {
        key: len(snapshot[key]) for key in list(STORE_ATTRS) + ["decision_events"]
    }
    return snapshot


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _parse(payload: dict[str, Any]) -> dict[str, list]:
    parsed: dict[str, list] = {}
    errors: list[str] = []

    for key, model in MODELS.items():
        raw = payload.get(key, [])
        if not isinstance(raw, list):
            errors.append(f"`{key}` must be an array")
            continue
        rows = []
        for index, entry in enumerate(raw):
            try:
                rows.append(model.model_validate(entry))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{key}[{index}]: {exc}")
        parsed[key] = rows

    events: list[DecisionEvent] = []
    for index, entry in enumerate(payload.get("decision_events", []) or []):
        try:
            events.append(DecisionEvent.model_validate(entry))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"decision_events[{index}]: {exc}")
    parsed["decision_events"] = events

    if errors:
        raise SnapshotError(
            "Snapshot failed validation and was NOT imported:\n" + "\n".join(errors[:20]),
            code="SNAPSHOT_INVALID",
        )
    return parsed


def _check_integrity(parsed: dict[str, list]) -> None:
    """Every reference must resolve inside the snapshot. Reject, never repair."""
    problems: list[str] = []

    case_ids = {c.id for c in parsed["cases"]}
    upload_ids = {u.id for u in parsed["uploads"]}
    item_ids = {i.id for i in parsed["items"]}
    investigation_ids = {i.id for i in parsed["investigations"]}
    research_ids = {r.id for r in parsed["research_runs"]}

    for upload in parsed["uploads"]:
        if upload.case_id not in case_ids:
            problems.append(f"upload {upload.id} references unknown case {upload.case_id}")

    for item in parsed["items"]:
        if item.case_id not in case_ids:
            problems.append(f"item {item.id} references unknown case {item.case_id}")
        if item.upload_id and item.upload_id not in upload_ids:
            problems.append(f"item {item.id} references unknown upload {item.upload_id}")
        if item.investigation_id and item.investigation_id not in investigation_ids:
            problems.append(
                f"item {item.id} references unknown investigation {item.investigation_id}"
            )

    for investigation in parsed["investigations"]:
        if investigation.case_id not in case_ids:
            problems.append(
                f"investigation {investigation.id} references unknown case {investigation.case_id}"
            )
        if investigation.upload_id and investigation.upload_id not in upload_ids:
            problems.append(
                f"investigation {investigation.id} references unknown upload "
                f"{investigation.upload_id}"
            )

    for revision in parsed["revisions"]:
        if revision.item_id not in item_ids:
            problems.append(f"revision {revision.id} references unknown item {revision.item_id}")

    for comment in parsed["comments"]:
        if comment.case_id not in case_ids:
            problems.append(f"comment {comment.id} references unknown case {comment.case_id}")

    for finding in parsed["findings"]:
        if finding.case_id not in case_ids:
            problems.append(f"finding {finding.id} references unknown case {finding.case_id}")

    for question in parsed["questions"]:
        if question.case_id not in case_ids:
            problems.append(f"question {question.id} references unknown case {question.case_id}")

    for event in parsed["decision_events"]:
        if event.case_id not in case_ids:
            problems.append(
                f"decision {event.id} references unknown case {event.case_id}"
            )

    for run in parsed["research_runs"]:
        if run.case_id not in case_ids:
            problems.append(f"research {run.id} references unknown case {run.case_id}")
        if run.item_id not in item_ids:
            problems.append(
                f"research {run.id} references unknown item {run.item_id} — refusing to "
                "rewrite item_ref"
            )

    for candidate in parsed["research_candidates"]:
        if candidate.run_id not in research_ids:
            problems.append(
                f"candidate {candidate.id} references unknown research run {candidate.run_id}"
            )
        if candidate.item_id not in item_ids:
            problems.append(
                f"candidate {candidate.id} references unknown item {candidate.item_id}"
            )

    if problems:
        raise SnapshotError(
            "Snapshot is inconsistent and was NOT imported:\n" + "\n".join(problems[:20]),
            code="SNAPSHOT_INCONSISTENT",
        )


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #
def _human_decided(item: EvidenceItem) -> bool:
    return item.revision > 1 or item.status.value != "ai_suggested" or bool(item.user_confirmed_fields)


def import_snapshot(payload: dict[str, Any], *, on_conflict: str = "error") -> dict[str, Any]:
    """Restore a snapshot with original IDs.

    on_conflict:
      "error" — abort without writing anything if any record differs (default)
      "skip"  — keep the existing record, report the conflict, import the rest
    """
    if on_conflict not in {"error", "skip"}:
        raise SnapshotError("on_conflict must be 'error' or 'skip'")

    parsed = _parse(payload)
    _check_integrity(parsed)

    # ---- conflict detection (before writing) ----------------------------- #
    conflicts: list[dict[str, Any]] = []
    for key, attr in STORE_ATTRS.items():
        existing = getattr(store, attr)
        for record in parsed[key]:
            current = existing.get(record.id)
            if current is None:
                continue
            incoming = record.model_dump(mode="json")
            stored = current.model_dump(mode="json")
            if incoming == stored:
                continue

            # A human decision always wins over an import.
            if key == "items" and _human_decided(current):
                conflicts.append(
                    {
                        "collection": key,
                        "id": record.id,
                        "reason": "existing item has human decisions; refused to overwrite",
                        "kept_revision": current.revision,
                        "incoming_revision": record.revision,
                    }
                )
                continue
            if key == "research_runs" and current.status.value == "complete" and current.candidate_ids:
                conflicts.append(
                    {
                        "collection": key,
                        "id": record.id,
                        "reason": "existing research run already has imported candidates",
                    }
                )
                continue
            conflicts.append(
                {"collection": key, "id": record.id, "reason": "record already exists with different content"}
            )

    if conflicts and on_conflict == "error":
        raise SnapshotError(
            f"{len(conflicts)} conflicting record(s); nothing was imported.",
            code="SNAPSHOT_CONFLICT",
            status_code=409,
            conflicts=conflicts[:50],
        )

    blocked = {(c["collection"], c["id"]) for c in conflicts}
    report: dict[str, Any] = {"imported": {}, "skipped": {}, "conflicts": conflicts}

    with store.lock:
        for key, attr in STORE_ATTRS.items():
            target = getattr(store, attr)
            imported = skipped = 0
            for record in parsed[key]:
                if (key, record.id) in blocked:
                    skipped += 1
                    continue
                if record.id in target and on_conflict == "skip":
                    skipped += 1
                    continue
                target[record.id] = record
                imported += 1
            report["imported"][key] = imported
            report["skipped"][key] = skipped

        imported_events = 0
        for event in parsed["decision_events"]:
            if event.id not in store.decision_events:
                store.decision_events[event.id] = event
                imported_events += 1
        report["imported"]["decision_events"] = imported_events

        # Keep case counters consistent with what was restored.
        for case_id, case in list(store.cases.items()):
            items = [i for i in store.items.values() if i.case_id == case_id]
            uploads = [u for u in store.uploads.values() if u.case_id == case_id]
            store.cases[case_id] = case.model_copy(
                update={
                    "item_count": len(items),
                    "upload_count": len(uploads),
                    "updated_at": case.updated_at,
                }
            )

    logger.info("snapshot import: %s", report["imported"])
    return report


def verify_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Dry-run validation used by the CLI and the verify endpoint."""
    parsed = _parse(payload)
    _check_integrity(parsed)
    counts = {key: len(rows) for key, rows in parsed.items()}
    return {
        "valid": True,
        "counts": counts,
        "case_ids": sorted(c.id for c in parsed["cases"]),
        "item_ids": sorted(i.id for i in parsed["items"]),
        "research_run_ids": sorted(r.id for r in parsed["research_runs"]),
        "agent_run_ids": {
            r.id: r.agent_run_id for r in parsed["research_runs"] if r.agent_run_id
        },
    }
