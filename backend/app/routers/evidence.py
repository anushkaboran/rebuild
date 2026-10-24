"""Evidence items: the belongings reviewers read, correct and confirm."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from ..deps import get_case_or_404, get_item_or_404
from ..models import (
    EvidenceItem,
    EvidenceItemCreate,
    EvidenceItemPatch,
    ItemStatus,
    Revision,
    utcnow,
)
from ..services import events
from ..store import new_id, store

router = APIRouter(prefix="/api", tags=["evidence"])

# Fields a reviewer may change. `status` included so confirm/reject is one call.
_PATCHABLE = {
    "name",
    "category",
    "room",
    "description",
    "condition",
    "damage_level",
    "estimated_value",
    "currency",
    "status",
}


@router.get(
    "/cases/{case_id}/items",
    response_model=list[EvidenceItem],
    summary="List belongings in a case",
)
def list_items(
    case_id: str,
    item_status: Optional[ItemStatus] = Query(default=None, alias="status"),
    room: Optional[str] = None,
) -> list[EvidenceItem]:
    get_case_or_404(case_id)
    items = store.list_items(case_id)
    if item_status is not None:
        items = [i for i in items if i.status == item_status]
    if room is not None:
        items = [i for i in items if (i.room or "").lower() == room.lower()]
    return items


@router.post(
    "/cases/{case_id}/items",
    response_model=EvidenceItem,
    status_code=status.HTTP_201_CREATED,
    summary="Add a belonging the agent missed",
)
def create_item(case_id: str, body: EvidenceItemCreate) -> EvidenceItem:
    case = get_case_or_404(case_id)
    item = EvidenceItem(
        id=new_id("itm"),
        case_id=case_id,
        name=body.name,
        room=body.room,
        category=body.category,
        description=body.description,
        condition=body.condition,
        damage_level=body.damage_level,
        estimated_value=body.estimated_value,
        currency=body.currency.upper(),
        status=ItemStatus.human_confirmed,
        updated_by=body.editor,
    )
    with store.lock:
        store.items[item.id] = item
        store.cases[case_id] = case.model_copy(
            update={"item_count": len(store.list_items(case_id)), "updated_at": utcnow()}
        )
    events.publish("item.created", case_id, {"item": item.model_dump(mode="json")})
    return item


@router.get("/items/{item_id}", response_model=EvidenceItem, summary="One belonging")
def get_item(item_id: str) -> EvidenceItem:
    return get_item_or_404(item_id)


@router.patch(
    "/items/{item_id}",
    response_model=EvidenceItem,
    summary="Correct, confirm or reject a belonging",
    responses={
        409: {"description": "Another collaborator saved first — refetch and retry"},
    },
)
def patch_item(item_id: str, body: EvidenceItemPatch) -> EvidenceItem:
    item = get_item_or_404(item_id)
    payload = body.model_dump(exclude_none=True)

    # Optimistic concurrency: without this, two reviewers editing the same row
    # silently overwrite each other.
    expected = payload.pop("if_revision", None)
    if expected is not None and expected != item.revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    f"Item was updated by {item.updated_by or 'another reviewer'} "
                    f"(revision {item.revision}). Refetch and reapply your change."
                ),
                "current": item.model_dump(mode="json"),
            },
        )

    editor = payload.pop("editor", "anonymous")
    note = payload.pop("note", None)
    changes = {k: v for k, v in payload.items() if k in _PATCHABLE}
    if not changes:
        return item

    # A reviewer touching an AI suggestion implicitly advances its status.
    if "status" not in changes and item.status == ItemStatus.ai_suggested:
        changes["status"] = ItemStatus.human_corrected

    updated = item.model_copy(
        update={
            **changes,
            "revision": item.revision + 1,
            "updated_at": utcnow(),
            "updated_by": editor,
        }
    )

    serialised = {
        key: (value.value if hasattr(value, "value") else value)
        for key, value in changes.items()
    }

    revision = Revision(
        id=new_id("rev"),
        item_id=item.id,
        editor=editor,
        note=note,
        changes=serialised,
    )

    with store.lock:
        store.items[item.id] = updated
        store.revisions[revision.id] = revision

    events.publish(
        "item.updated",
        item.case_id,
        {"item": updated.model_dump(mode="json"), "revision": revision.model_dump(mode="json")},
    )
    return updated


@router.post(
    "/items/{item_id}/confirm",
    response_model=EvidenceItem,
    summary="Confirm an item as-is",
)
def confirm_item(item_id: str, editor: str = Query(default="anonymous")) -> EvidenceItem:
    return patch_item(
        item_id,
        EvidenceItemPatch(
            status=ItemStatus.human_confirmed,
            editor=editor,
            note="Confirmed without changes",
        ),
    )


@router.post(
    "/items/{item_id}/reject",
    response_model=EvidenceItem,
    summary="Reject an item (not a real belonging / false positive)",
)
def reject_item(
    item_id: str,
    editor: str = Query(default="anonymous"),
    note: Optional[str] = Query(default=None),
) -> EvidenceItem:
    return patch_item(
        item_id,
        EvidenceItemPatch(
            status=ItemStatus.rejected, editor=editor, note=note or "Rejected by reviewer"
        ),
    )


@router.get(
    "/items/{item_id}/revisions",
    response_model=list[Revision],
    summary="Audit trail for one belonging",
)
def list_item_revisions(item_id: str) -> list[Revision]:
    get_item_or_404(item_id)
    return store.list_revisions(item_id)
