"""Recover an already-completed CREAO run into the local record.

Why this exists: a run costs credits the moment it executes, so a failure in
*our* download/import step must not force the team to pay for the same
investigation twice. This endpoint takes a `run_id`, re-reads the completed run
and its `rebuild-result.json`, validates it, and imports it through the same
pipeline the live path uses.

It never creates a CREAO run. The only CREAO calls are read-only:
`GET /apps/{appId}/runs/{run_id}` plus the artifact download.

Safety properties:
  * validation must pass before anything is written;
  * the investigation is marked `complete` only after a successful import;
  * re-running the recovery is idempotent — no duplicate items;
  * existing items (and therefore human corrections) are never overwritten;
  * if the original case/evidence metadata is gone, the request fails with a
    precise error instead of inventing a case. A caller may explicitly supply
    `case` + `evidence` to restore state that exists on disk.
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, HTTPException, status

from ..config import settings
from ..deps import get_case_or_404
from ..models import (
    Case,
    CaseStatus,
    Investigation,
    InvestigationStatus,
    ItemStatus,
    RecoverRequest,
    RecoverResponse,
    Upload,
    utcnow,
)
from ..services import creao, events, result_import, storage
from ..store import new_id, store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["recovery"])

SUCCESS_STATUSES = {"completed", "succeeded", "success"}


def _error(code: str, message: str, *, http: int = status.HTTP_409_CONFLICT) -> HTTPException:
    return HTTPException(status_code=http, detail={"code": code, "message": message})


@router.post(
    "/creao/recover",
    response_model=RecoverResponse,
    summary="Import an existing completed CREAO run (starts no new run)",
    responses={
        400: {"description": "No case context supplied"},
        404: {"description": "Case not found and not restorable"},
        409: {"description": "Run not completed, or evidence metadata unavailable"},
    },
)
async def recover_run(body: RecoverRequest) -> RecoverResponse:
    if creao.is_mock():
        raise _error(
            "CREAO_MODE_MOCK",
            "Recovery reads a real completed run, so it needs CREAO_MODE=live. "
            "Mock mode never talks to CREAO.",
        )
    try:
        creao.ensure_ready()
    except creao.CreaoConfigError as exc:
        raise _error(exc.code, exc.message, http=status.HTTP_503_SERVICE_UNAVAILABLE) from exc

    # ---- 1. what do we already know about this case? ---------------------- #
    investigation = (
        store.investigations.get(body.investigation_id) if body.investigation_id else None
    )
    case_id = (body.case.id if body.case else None) or (
        investigation.case_id if investigation else None
    )
    if not case_id:
        raise _error(
            "RECOVERY_CONTEXT_MISSING",
            "Cannot tell which case this run belongs to. Supply `investigation_id` "
            "(if the local investigation still exists) or `case.id`.",
            http=status.HTTP_400_BAD_REQUEST,
        )

    restored_case = False
    restored_uploads: list[str] = []
    case = store.get_case(case_id)

    if case is None:
        case, restored_uploads = _restore_case(body, case_id)
        restored_case = True
        logger.warning(
            "recovery restored case %s from supplied metadata (%d upload(s))",
            case_id,
            len(restored_uploads),
        )

    # Idempotency guard: this run may already be imported. Checked before any
    # CREAO call, and independent of whether the caller passed investigation_id
    # — otherwise a retry would create a second investigation and duplicate the
    # whole inventory.
    already = next(
        (
            inv
            for inv in store.list_investigations(case_id)
            if inv.agent_run_id == body.run_id
            and inv.status == InvestigationStatus.complete
        ),
        None,
    )
    if already is not None and body.investigation_id in (None, already.id):
        logger.info("CREAO run %s was already recovered as %s", body.run_id, already.id)
        return RecoverResponse(
            recovered=True,
            already_recovered=True,
            run_id=body.run_id,
            run_status="completed",
            case_id=case_id,
            investigation=already,
            items_created=0,
            items_already_present=len(already.item_ids),
            evidence_ids=already.evidence_ids,
            restored_case=restored_case,
            restored_uploads=restored_uploads,
        )

    # ---- 2. read the completed run (read-only) ---------------------------- #
    async with creao.CreaoAppClient() as client:
        try:
            run = await client.get_run(body.run_id)
        except creao.CreaoError as exc:
            raise _error(exc.code, exc.message, http=status.HTTP_502_BAD_GATEWAY) from exc

        run_status = str(run.get("status") or "").lower()
        if run_status not in SUCCESS_STATUSES:
            raise _error(
                "RUN_NOT_COMPLETED",
                f"CREAO run {body.run_id} is '{run_status or 'unknown'}'. Only a "
                "completed run can be recovered; poll it instead.",
            )

        try:
            payload, source = await creao.fetch_result_payload(client, run)
        except creao.CreaoError as exc:
            raise _error(exc.code, exc.message, http=status.HTTP_502_BAD_GATEWAY) from exc

    # ---- 3. bind the agent's evidence IDs to real uploads ----------------- #
    evidence_ids, uploads_by_evidence = _resolve_evidence(body, case_id, payload)

    try:
        creao.validate_evidence_ids(payload, evidence_ids)
    except creao.CreaoInvalidResult as exc:
        raise _error(exc.code, exc.message, http=status.HTTP_409_CONFLICT) from exc

    # ---- 4. import through the shared pipeline ---------------------------- #
    result = creao.normalise_result(payload)
    result.run_id = body.run_id
    result.thread_id = run.get("threadId")
    result.status = run_status
    result.mode = "live"
    result.stub = False
    result.evidence_transport = "recovered"
    result.evidence_ids = evidence_ids
    result.payload = payload
    result.raw = {"source": source, "recovered_from_run": body.run_id}

    if investigation is None:
        investigation = Investigation(
            id=body.investigation_id or new_id("inv"),
            case_id=case_id,
            upload_id=next(iter(uploads_by_evidence.values()), ""),
            status=InvestigationStatus.failed,
            error="pending recovery",
            mode="live",
            evidence_ids=evidence_ids,
            requested_by="recovery",
        )
    elif investigation.status == InvestigationStatus.complete and investigation.agent_run_id == body.run_id:
        # Already imported this exact run — report without touching anything.
        return RecoverResponse(
            recovered=True,
            already_recovered=True,
            run_id=body.run_id,
            run_status=run_status,
            case_id=case_id,
            investigation=investigation,
            items_created=0,
            items_already_present=len(investigation.item_ids),
            evidence_ids=evidence_ids,
            restored_case=restored_case,
            restored_uploads=restored_uploads,
        )

    with store.lock:
        store.investigations[investigation.id] = investigation

    report = result_import.import_agent_result(
        investigation=investigation,
        case_id=case_id,
        result=result,
        uploads_by_evidence=uploads_by_evidence,
        default_upload_id=next(iter(uploads_by_evidence.values()), None),
    )

    # ---- 5. mark complete only now that validation + import succeeded ----- #
    finished = investigation.model_copy(
        update={
            "status": InvestigationStatus.complete,
            "mode": "live",
            "agent_run_id": body.run_id,
            "conversation_id": run.get("threadId"),
            "evidence_transport": "recovered",
            "evidence_ids": evidence_ids,
            "summary": result.summary,
            "result_payload": payload,
            "recovered": True,
            "error": None,
            "item_ids": report.item_ids,
            "finished_at": utcnow(),
        }
    )
    with store.lock:
        store.investigations[finished.id] = finished
        store.cases[case_id] = case.model_copy(
            update={
                "item_count": len(store.list_items(case_id)),
                "status": CaseStatus.review,
                "updated_at": utcnow(),
            }
        )

    events.publish(
        "investigation.completed",
        case_id,
        {
            "investigation": finished.model_dump(mode="json"),
            "recovered": True,
            "run_id": body.run_id,
            "items_created": len(report.created),
        },
    )

    logger.info(
        "recovered CREAO run %s into case %s: %d new item(s), %d already present",
        body.run_id,
        case_id,
        len(report.created),
        len(report.matched),
    )

    return RecoverResponse(
        recovered=True,
        run_id=body.run_id,
        run_status=run_status,
        case_id=case_id,
        investigation=finished,
        items_created=len(report.created),
        items_already_present=len(report.matched),
        evidence_ids=evidence_ids,
        restored_case=restored_case,
        restored_uploads=restored_uploads,
    )


# --------------------------------------------------------------------------- #
# Evidence binding
# --------------------------------------------------------------------------- #
def _resolve_evidence(
    body: RecoverRequest, case_id: str, payload: dict
) -> tuple[list[str], dict[str, str]]:
    """Map the agent's evidence IDs onto uploads of this case."""
    reported = [
        str(e.get("evidence_id"))
        for e in payload.get("evidence") or []
        if isinstance(e, dict) and e.get("evidence_id")
    ]
    if not reported:
        raise _error(
            "EVIDENCE_METADATA_MISSING",
            "The recovered result lists no evidence records, so its items cannot "
            "be linked to photographs.",
        )

    if body.evidence:
        ids = [e.evidence_id for e in body.evidence]
        mapping = {e.evidence_id: e.upload_id for e in body.evidence}
        missing_uploads = [u for u in mapping.values() if u not in store.uploads]
        if missing_uploads:
            raise _error(
                "EVIDENCE_METADATA_MISSING",
                "These upload ids are not known to the backend: "
                + ", ".join(missing_uploads)
                + ". Restore the uploads (see `case`/`evidence`) or drop the mapping "
                "so filenames can be matched against the case's existing uploads.",
            )
        unknown = [i for i in reported if i not in mapping]
        if unknown:
            raise _error(
                "EVIDENCE_METADATA_MISSING",
                "The run references evidence ids missing from the supplied mapping: "
                + ", ".join(unknown),
            )
        return reported, {i: mapping[i] for i in reported}

    # No explicit mapping: match by filename against this case's uploads.
    uploads = store.list_uploads(case_id)
    by_name = {u.original_filename: u.id for u in uploads}
    mapping: dict[str, str] = {}
    unmatched: list[str] = []
    for entry in payload.get("evidence") or []:
        filename = str(entry.get("filename") or "")
        upload_id = by_name.get(filename)
        if upload_id:
            mapping[str(entry["evidence_id"])] = upload_id
        else:
            unmatched.append(filename or str(entry.get("evidence_id")))

    if unmatched:
        raise _error(
            "EVIDENCE_METADATA_MISSING",
            "Cannot link these evidence files to uploads in case "
            f"{case_id}: {', '.join(unmatched)}. The case's upload metadata is "
            "unavailable (the backend keeps it in memory). Supply `case` and "
            "`evidence` to restore it, or re-upload the files.",
            http=status.HTTP_404_NOT_FOUND,
        )
    return reported, mapping


# --------------------------------------------------------------------------- #
# State restoration
# --------------------------------------------------------------------------- #
def _restore_case(body: RecoverRequest, case_id: str) -> tuple[Case, list[str]]:
    """Rebuild a case + upload records from supplied metadata.

    Only possible when the caller can state what the case was *and* the uploaded
    bytes are still on disk. Nothing is invented: every field comes from the
    request or from the stored file.
    """
    if body.case is None or not body.evidence:
        raise _error(
            "CASE_NOT_RESTORABLE",
            f"Case {case_id} is not in the backend's store (case state is kept in "
            "memory and does not survive a restart). To recover this run, resend "
            "`case` (id/title/…) and `evidence` (evidence_id → upload_id, "
            "filename) so the record can be rebuilt from the uploads still on "
            "disk. Without that, only the run's JSON can be read, not attributed "
            "to a case.",
            http=status.HTTP_404_NOT_FOUND,
        )

    restored: list[str] = []
    uploads: dict[str, Upload] = {}
    missing_files: list[str] = []

    for entry in body.evidence:
        existing = store.uploads.get(entry.upload_id)
        if existing is not None:
            uploads[entry.upload_id] = existing
            continue

        path, stored_path = _find_stored_file(case_id, entry.upload_id)
        if path is None:
            missing_files.append(f"{entry.upload_id} ({entry.filename})")
            continue

        uploads[entry.upload_id] = Upload(
            id=entry.upload_id,
            case_id=case_id,
            kind=entry.kind,
            original_filename=entry.filename,
            content_type=entry.content_type,
            size_bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            uploaded_by="recovery",
            url=f"/api/uploads/{entry.upload_id}/file",
            stored_path=stored_path,
        )
        restored.append(entry.upload_id)

    if missing_files:
        raise _error(
            "UPLOAD_BYTES_MISSING",
            "These upload files are no longer on disk, so the case cannot be "
            "restored: " + ", ".join(missing_files),
            http=status.HTTP_404_NOT_FOUND,
        )

    case = Case(
        id=body.case.id,
        title=body.case.title,
        address=body.case.address,
        disaster_type=body.case.disaster_type,
        notes=body.case.notes,
        created_by=body.case.created_by,
        status=CaseStatus.review,
        upload_count=len(uploads),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    with store.lock:
        store.cases[case.id] = case
        for upload in uploads.values():
            store.uploads[upload.id] = upload
    return case, restored


def _find_stored_file(case_id: str, upload_id: str):
    """Locate `<upload_id>.<ext>` inside the case's upload directory."""
    directory = storage.settings.upload_dir / case_id
    if not directory.is_dir():
        return None, None
    for candidate in sorted(directory.iterdir()):
        if candidate.stem == upload_id:
            return candidate, f"{case_id}/{candidate.name}"
    return None, None
