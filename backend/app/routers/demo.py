"""Demo seed — one call fills a case with reviewable data for the pitch.

Handy when the wifi dies mid-demo or a teammate needs a populated screen.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from ..models import (
    Case,
    CaseStatus,
    DamageLevel,
    EvidenceItem,
    ItemStatus,
    Member,
    utcnow,
)
from ..services import events
from ..store import new_id, store

router = APIRouter(prefix="/api/demo", tags=["demo"])

_SEED_ITEMS = [
    ("Sofa", "living room", "furniture", "severe", 1200.0, ItemStatus.human_confirmed, "Priya"),
    ("Coffee table", "living room", "furniture", "moderate", 250.0, ItemStatus.human_corrected, "Marcus"),
    ("Area rug", "living room", "textile", "destroyed", 300.0, ItemStatus.ai_suggested, None),
    ("Refrigerator", "kitchen", "appliance", "severe", 1800.0, ItemStatus.human_confirmed, "Priya"),
    ("Kitchen cabinet set", "kitchen", "millwork", "severe", 2400.0, ItemStatus.ai_suggested, None),
    ("Washer", "basement", "appliance", "moderate", 850.0, ItemStatus.ai_suggested, None),
]


@router.post(
    "/seed",
    status_code=status.HTTP_201_CREATED,
    summary="Create a populated demo case",
)
def seed_demo() -> dict:
    case = Case(
        id=new_id("case"),
        title="Demo — 42 Willow Creek Rd",
        address="42 Willow Creek Rd, Asheville, NC",
        disaster_type="flood",
        status=CaseStatus.review,
        created_by="demo",
        notes="Seeded demo case. Safe to delete.",
        members=[
            Member(id=new_id("usr"), display_name="Priya", role="owner"),
            Member(id=new_id("usr"), display_name="Marcus", role="reviewer"),
        ],
    )

    items: list[EvidenceItem] = []
    for name, room, category, damage, value, item_status, editor in _SEED_ITEMS:
        items.append(
            EvidenceItem(
                id=new_id("itm"),
                case_id=case.id,
                room=room,
                name=name,
                category=category,
                condition="Water line visible; material swollen.",
                damage_level=DamageLevel(damage),
                confidence=0.81,
                estimated_value=value,
                status=item_status,
                updated_by=editor,
                updated_at=utcnow(),
            )
        )

    with store.lock:
        store.cases[case.id] = case.model_copy(
            update={"item_count": len(items), "updated_at": utcnow()}
        )
        for item in items:
            store.items[item.id] = item

    events.publish("case.created", case.id, {"case": case.model_dump(mode="json")})
    return {"case_id": case.id, "items": len(items), "next": f"/api/cases/{case.id}"}
