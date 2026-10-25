"""Photo and receipt uploads."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from ..deps import get_case_or_404, get_upload_or_404
from ..models import Upload, UploadKind, utcnow
from ..services import events, storage
from ..store import new_id, store

router = APIRouter(prefix="/api", tags=["uploads"])


@router.post(
    "/cases/{case_id}/uploads",
    response_model=Upload,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a room photo or receipt",
)
async def upload_file(
    case_id: str,
    file: UploadFile = File(..., description="Room photo or receipt (image/* or PDF)"),
    kind: UploadKind = Form(UploadKind.photo),
    uploaded_by: str = Form("anonymous"),
) -> Upload:
    case = get_case_or_404(case_id)
    upload_id = new_id("upl")

    stored = await storage.save_upload(case_id, upload_id, file)

    upload = Upload(
        id=upload_id,
        case_id=case_id,
        kind=kind,
        original_filename=file.filename or "upload",
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        uploaded_by=uploaded_by,
        url=f"/api/uploads/{upload_id}/file",
        stored_path=stored.rel_path,  # type: ignore[call-arg]
    )
    with store.lock:
        store.uploads[upload.id] = upload
        store.cases[case_id] = case.model_copy(
            update={"upload_count": case.upload_count + 1, "updated_at": utcnow()}
        )
    events.publish("upload.created", case_id, {"upload": upload.model_dump(mode="json")})
    return upload


@router.get("/uploads/{upload_id}", response_model=Upload, summary="Upload metadata")
def get_upload(upload_id: str) -> Upload:
    return get_upload_or_404(upload_id)


@router.get("/uploads/{upload_id}/file", summary="Serve the uploaded file")
def get_upload_file(upload_id: str) -> FileResponse:
    upload = get_upload_or_404(upload_id)
    path = storage.resolve_upload_path(upload)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "EVIDENCE_FILE_MISSING",
                "message": "The stored file for this upload is not present on disk.",
            },
        )
    return FileResponse(
        path,
        media_type=upload.content_type or storage.guessed_content_type(path),
        headers={
            "Content-Disposition": storage.content_disposition(
                upload.original_filename, disposition="attachment"
            )
        },
    )
