"""PDF export of the recovery record."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from ..deps import get_case_or_404
from ..models import utcnow
from ..services import events, pdf
from ..store import store

router = APIRouter(prefix="/api", tags=["export"])


@router.get(
    "/cases/{case_id}/export.pdf",
    summary="Download the evidence-linked recovery record as PDF",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def export_case_pdf(case_id: str) -> Response:
    case = get_case_or_404(case_id)
    items = store.list_items(case_id)
    uploads = store.list_uploads(case_id)
    investigations = store.list_investigations(case_id)
    revisions = [r for item in items for r in store.list_revisions(item.id)]

    blob = pdf.build_case_pdf(
        case=case,
        items=items,
        uploads=uploads,
        investigations=investigations,
        revisions=revisions,
    )

    # Mark the case as exported for the dashboard status chip.
    with store.lock:
        store.cases[case_id] = case.model_copy(
            update={"status": case.status, "updated_at": utcnow()}
        )
    events.publish("case.updated", case_id, {"exported": True, "format": "pdf"})

    slug = "".join(c if c.isalnum() else "-" for c in case.title.lower()).strip("-")[:40]
    filename = f"rebuild-{slug or case_id}.pdf"
    return Response(
        content=blob,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Rebuild-Item-Count": str(len(items)),
        },
    )
