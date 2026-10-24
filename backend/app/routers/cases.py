"""Cases: the recovery workspace shared by a household and its reviewers."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ..deps import get_case_or_404, get_item_or_404
from ..models import (
    Case,
    CaseCreate,
    CaseDetail,
    CaseStatus,
    CaseUpdate,
    Comment,
    CommentCreate,
    Event,
    Member,
    ReviewStats,
    Upload,
    utcnow,
)
from ..services import events
from ..store import new_id, store

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("", response_model=list[Case], summary="List cases")
def list_cases() -> list[Case]:
    return store.list_cases()


@router.post(
    "",
    response_model=Case,
    status_code=status.HTTP_201_CREATED,
    summary="Create a case",
)
def create_case(body: CaseCreate) -> Case:
    case = Case(
        id=new_id("case"),
        title=body.title,
        address=body.address,
        disaster_type=body.disaster_type,
        notes=body.notes,
        created_by=body.created_by,
        members=[Member(id=new_id("usr"), display_name=body.created_by, role="owner")],
    )
    with store.lock:
        store.cases[case.id] = case
    events.publish("case.created", case.id, {"case": case.model_dump(mode="json")})
    return case


@router.get("/{case_id}", response_model=CaseDetail, summary="Case with uploads and items")
def get_case(case_id: str) -> CaseDetail:
    case = get_case_or_404(case_id)
    return CaseDetail(
        **case.model_dump(),
        uploads=store.list_uploads(case_id),
        items=store.list_items(case_id),
    )


@router.patch("/{case_id}", response_model=Case, summary="Update case metadata")
def update_case(case_id: str, body: CaseUpdate) -> Case:
    case = get_case_or_404(case_id)
    changes = body.model_dump(exclude_none=True)
    if not changes:
        return case

    updated = case.model_copy(update={**changes, "updated_at": utcnow()})
    with store.lock:
        store.cases[case_id] = updated
    events.publish("case.updated", case_id, {"changes": _jsonable(changes)})
    return updated


@router.get("/{case_id}/stats", response_model=ReviewStats, summary="Review progress counters")
def get_stats(case_id: str) -> ReviewStats:
    get_case_or_404(case_id)
    items = store.list_items(case_id)
    revisions = [r for i in items for r in store.list_revisions(i.id)]

    counts: dict[str, int] = {}
    for item in items:
        counts[item.status.value] = counts.get(item.status.value, 0) + 1

    contributors = sorted({r.editor for r in revisions})
    last_activity = max(
        (r.created_at for r in revisions),
        default=max((i.updated_at for i in items), default=None),
    )

    return ReviewStats(
        case_id=case_id,
        total_items=len(items),
        ai_suggested=counts.get("ai_suggested", 0),
        human_confirmed=counts.get("human_confirmed", 0),
        human_corrected=counts.get("human_corrected", 0),
        rejected=counts.get("rejected", 0),
        total_estimated_value=round(sum(i.estimated_value or 0 for i in items), 2),
        currency=next((i.currency for i in items if i.currency), "USD"),
        contributors=contributors,
        last_activity_at=last_activity,
    )


# --------------------------------------------------------------------------- #
# Comments
# --------------------------------------------------------------------------- #
@router.get("/{case_id}/comments", response_model=list[Comment], summary="List comments")
def list_comments(case_id: str) -> list[Comment]:
    get_case_or_404(case_id)
    return store.list_comments(case_id)


@router.post(
    "/{case_id}/comments",
    response_model=Comment,
    status_code=status.HTTP_201_CREATED,
    summary="Add a review comment",
)
def add_comment(case_id: str, body: CommentCreate) -> Comment:
    get_case_or_404(case_id)
    if body.item_id:
        get_item_or_404(body.item_id)

    comment = Comment(
        id=new_id("cmt"),
        case_id=case_id,
        item_id=body.item_id,
        author=body.author,
        body=body.body,
    )
    with store.lock:
        store.comments[comment.id] = comment
    events.publish("comment.created", case_id, {"comment": comment.model_dump(mode="json")})
    return comment


# --------------------------------------------------------------------------- #
# Real-time fallback channel
# --------------------------------------------------------------------------- #
@router.get(
    "/{case_id}/events",
    response_model=list[Event],
    summary="Recent events for a case (poll fallback)",
)
def list_events(case_id: str, limit: int = Query(default=50, ge=1, le=200)) -> list[Event]:
    get_case_or_404(case_id)
    return [e for e in store.events if e.case_id == case_id][-limit:]


@router.get(
    "/{case_id}/events/stream",
    summary="Server-sent events for a case (live fallback until SpacetimeDB is wired)",
)
async def stream_events(case_id: str, request: Request) -> StreamingResponse:
    get_case_or_404(case_id)
    queue = events.subscribe(case_id)

    async def generator():
        try:
            # Tell the client we're connected so it can stop polling.
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # defeats idle proxies
                    continue
                yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
        finally:
            events.unsubscribe(case_id, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
@router.get("/{case_id}/uploads", response_model=list[Upload], summary="List case uploads")
def list_uploads(case_id: str) -> list[Upload]:
    get_case_or_404(case_id)
    return store.list_uploads(case_id)


def _jsonable(data: dict) -> dict:
    return json.loads(json.dumps(data, default=str))
