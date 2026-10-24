"""Investigations: hand an upload to the CREAO Evidence Investigator."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from ..config import settings
from ..deps import get_case_or_404, get_upload_or_404
from ..models import (
    BBox,
    DamageLevel,
    EvidenceItem,
    Investigation,
    InvestigationRequest,
    InvestigationStatus,
    ItemStatus,
    utcnow,
)
from ..services import creao, events, evidence_urls, result_import, storage
from ..store import new_id, store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["investigations"])


@router.post(
    "/cases/{case_id}/investigations",
    response_model=Investigation,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an agent investigation for an uploaded file",
)
async def start_investigation(
    case_id: str,
    body: InvestigationRequest,
    background: BackgroundTasks,
) -> Investigation:
    """Returns immediately with a queued investigation.

    The agent call runs in the background. Poll `GET /api/investigations/{id}`
    or subscribe to the case event stream; the frontend shows a pending card
    that fills in when `investigation.completed` arrives.
    """
    get_case_or_404(case_id)
    upload = get_upload_or_404(body.upload_id)
    if upload.case_id != case_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload does not belong to this case.",
        )

    # Evidence set: the primary upload plus any explicitly requested ones,
    # de-duplicated and ordered — ev_### IDs follow this order.
    uploads = [upload]
    for extra_id in body.upload_ids or []:
        if extra_id == upload.id:
            continue
        extra = get_upload_or_404(extra_id)
        if extra.case_id != case_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Upload '{extra_id}' does not belong to this case.",
            )
        uploads.append(extra)

    # Fail before accepting work we cannot perform. In live mode a missing key,
    # app id, or an undemonstrated evidence transport is a 503 — never a quiet
    # fall back to simulated evidence, and never a paid run without evidence.
    try:
        creao.ensure_ready()
        creao.ensure_evidence_transport()
    except creao.CreaoConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": exc.message, "mode": creao.mode()},
        ) from exc

    investigation = Investigation(
        id=new_id("inv"),
        case_id=case_id,
        upload_id=upload.id,
        status=InvestigationStatus.queued,
        mode=creao.mode(),  # type: ignore[arg-type]
        evidence_transport=(
            "mock" if creao.is_mock() else settings.creao_evidence_transport
        ),
        evidence_ids=[f"ev_{i + 1:03d}" for i in range(len(uploads))],
        requested_by=body.requested_by,
    )
    with store.lock:
        store.investigations[investigation.id] = investigation

    events.publish(
        "investigation.started",
        case_id,
        {"investigation": investigation.model_dump(mode="json")},
    )
    background.add_task(
        _run, investigation.id, body.room_hint, body.instructions, [u.id for u in uploads]
    )
    return investigation


@router.get(
    "/investigations/{investigation_id}",
    response_model=Investigation,
    summary="Investigation status and result",
)
def get_investigation(investigation_id: str) -> Investigation:
    investigation = store.investigations.get(investigation_id)
    if investigation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investigation '{investigation_id}' not found.",
        )
    return investigation


@router.get(
    "/cases/{case_id}/investigations",
    response_model=list[Investigation],
    summary="All investigations for a case",
)
def list_investigations(case_id: str) -> list[Investigation]:
    get_case_or_404(case_id)
    return store.list_investigations(case_id)


# --------------------------------------------------------------------------- #
# Background worker
# --------------------------------------------------------------------------- #
async def _run(
    investigation_id: str,
    room_hint: str | None,
    instructions: str | None,
    upload_ids: list[str] | None = None,
) -> None:
    investigation = store.investigations[investigation_id]
    upload_ids = upload_ids or [investigation.upload_id]
    uploads = [store.uploads[i] for i in upload_ids if i in store.uploads]
    upload = uploads[0]
    case = store.get_case(investigation.case_id)

    _set_status(investigation, InvestigationStatus.running)

    try:
        transport = creao.ensure_evidence_transport()
        result = await creao.investigate(
            case_description=_describe_case(case, upload, instructions),
            room_name=room_hint or _room_of(case, upload),
            evidence=_build_evidence(uploads, transport, case),
            confirmed_items=_confirmed_items(investigation.case_id),
            transport=transport,
            previous_result_url=_previous_result_url(investigation),
        )
    except creao.CreaoError as exc:
        logger.error("investigation %s failed: %s (%s)", investigation_id, exc.code, exc.message)
        _fail(investigation_id, f"{exc.code}: {exc.message}")
        return
    except Exception as exc:  # noqa: BLE001 - surface any agent failure to the UI
        logger.exception("investigation %s failed unexpectedly", investigation_id)
        _fail(investigation_id, f"unexpected error: {exc}")
        return

    # Single import pipeline, shared with the recovery endpoint.
    uploads_by_evidence = {
        f"ev_{i + 1:03d}": uploads[i].id for i in range(len(uploads))
    }
    report = result_import.import_agent_result(
        investigation=investigation,
        case_id=investigation.case_id,
        result=result,
        uploads_by_evidence=uploads_by_evidence,
        default_upload_id=upload.id,
    )
    created = report.created

    with store.lock:
        investigation = investigation.model_copy(
            update={
                "status": InvestigationStatus.complete,
                "mode": result.mode,  # type: ignore[arg-type]
                "agent_run_id": result.run_id,
                "conversation_id": result.thread_id,
                "model": result.model,
                "credits_used": result.credits_used,
                "evidence_transport": result.evidence_transport,
                "evidence_ids": result.evidence_ids,
                "summary": result.summary,
                "result_payload": result.payload or None,
                "item_ids": report.item_ids,
                "finished_at": utcnow(),
            }
        )
        store.investigations[investigation_id] = investigation

    for item in created:
        events.publish(
            "item.created", investigation.case_id, {"item": item.model_dump(mode="json")}
        )
    events.publish(
        "investigation.completed",
        investigation.case_id,
        {
            "investigation": investigation.model_dump(mode="json"),
            "mode": result.mode,
            "stub": result.stub,
            "agent_summary": result.summary,
            "creao_configured": settings.creao_configured,
        },
    )


def _fail(investigation_id: str, message: str) -> None:
    investigation = store.investigations[investigation_id]
    failed = investigation.model_copy(
        update={
            "status": InvestigationStatus.failed,
            "error": message[:1000],
            "finished_at": utcnow(),
        }
    )
    with store.lock:
        store.investigations[investigation_id] = failed
    events.publish(
        "investigation.completed",
        failed.case_id,
        {"investigation": failed.model_dump(mode="json")},
    )


def _set_status(investigation: Investigation, value: InvestigationStatus) -> None:
    updated = investigation.model_copy(update={"status": value})
    with store.lock:
        store.investigations[investigation.id] = updated


def _damage(value: str) -> DamageLevel:
    try:
        return DamageLevel(value)
    except ValueError:
        return DamageLevel.unknown


def _describe_case(case, upload, instructions: str | None) -> str:
    """Compose the agent's required `case_description` from case context.

    Context only — the agent is instructed never to treat this as proof.
    """
    parts: list[str] = []
    if case is not None:
        parts.append(f"Post-flood renter claim for {case.title}.")
        if case.address:
            parts.append(f"Property: {case.address}.")
        parts.append(f"Disaster: {case.disaster_type}.")
        if case.notes:
            parts.append(f"Case notes: {case.notes}")
    else:
        parts.append("Post-flood renter claim.")
    parts.append(f"Evidence file: {upload.original_filename} ({upload.kind.value}).")
    if instructions:
        parts.append(f"Investigator instructions: {instructions}")
    return " ".join(parts)[:4000]

def _room_of(case, upload) -> str:
    """Best available room label for the agent's required `room_name`."""
    if case is not None and getattr(case, "notes", None):
        lowered = case.notes.lower()
        for room in ("living room", "kitchen", "bedroom", "bathroom", "garage", "basement"):
            if room in lowered:
                return room
    stem = upload.original_filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ")
    for room in ("living room", "kitchen", "bedroom", "bathroom", "garage", "basement"):
        if room in stem.lower():
            return room
    return "room"


def _build_evidence(uploads, transport: str, case) -> list:
    """Assign stable ev_### IDs and (for the url transport) signed public URLs.

    Classification comes from the upload's own `kind` field — photo vs receipt —
    never from the filename pattern.
    """
    default_description = case.notes if case is not None else None
    evidence = []
    for index, item in enumerate(uploads, start=1):
        url = None
        if transport == "url":
            # Raises CREAO_EVIDENCE_URLS_UNAVAILABLE if no public base is set.
            url = evidence_urls.upload_url(item.id)
        evidence.append(
            creao.EvidenceFile(
                evidence_id=f"ev_{index:03d}",
                filename=item.original_filename,
                content_type=item.content_type,
                kind="photo" if item.kind.value == "photo" else "receipt",
                path=storage.path_for(item.stored_path),
                description=default_description,
                url=url,
            )
        )
    return evidence


def _confirmed_items(case_id: str) -> list[dict]:
    """Human corrections, in the agent's confirmed_items format.

    Agent proposals and human confirmations stay separate: this only exports
    decisions a person actually made.
    """
    confirmed: list[dict] = []
    for item in store.list_items(case_id):
        for revision in store.list_revisions(item.id):
            for field, value in (revision.changes or {}).items():
                if field == "status":
                    continue
                confirmed.append(
                    {
                        "target_type": "item",
                        "target_id": item.agent_item_id or item.id,
                        "field": field,
                        "value": value,
                        "confirmed_by": revision.editor,
                    }
                )
    return confirmed


def _previous_result_url(investigation) -> str | None:
    """Signed URL of the most recent completed result for this case.

    Absent on a first investigation — the key is then omitted entirely, which is
    what the agent expects.
    """
    if creao.settings.creao_evidence_transport == "none":
        return None
    previous = [
        inv
        for inv in store.list_investigations(investigation.case_id)
        if inv.id != investigation.id
        and inv.status == InvestigationStatus.complete
        and inv.result_payload
    ]
    if not previous:
        return None
    try:
        return evidence_urls.payload_url(previous[0].id)
    except evidence_urls.EvidenceUrlError:
        return None
