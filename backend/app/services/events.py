"""Event fan-out for real-time collaboration.

Two consumers:
  * SSE subscribers (`GET /api/cases/{id}/events/stream`) — lets the frontend
    get live updates before SpacetimeDB subscriptions are wired up.
  * `services.spacetime.publish_event` — mirrors the event into SpacetimeDB so
    the shared state stays authoritative there.

Publishing never raises: a dead subscriber or an unreachable SpacetimeDB must
not break a write request.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from ..models import Event
from ..store import new_id, store

logger = logging.getLogger(__name__)

_subscribers: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)


def publish(
    event_type: str,
    case_id: str,
    payload: dict[str, Any] | None = None,
) -> Event:
    """Record an event locally and push it to everyone watching the case."""
    event = Event(
        id=new_id("evt"),
        type=event_type,  # type: ignore[arg-type]
        case_id=case_id,
        payload=payload or {},
    )

    store.events.append(event)
    if len(store.events) > 500:  # bounded ring buffer
        del store.events[:100]

    for queue in list(_subscribers.get(case_id, ())):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:  # slow client — drop, don't block writers
            logger.warning("dropping event for slow SSE subscriber on %s", case_id)

    # Mirror to SpacetimeDB (imported lazily to avoid a cycle).
    try:
        from . import spacetime

        spacetime.publish_event(event)
    except Exception:  # noqa: BLE001 - mirroring is best-effort
        logger.exception("failed to mirror event %s to SpacetimeDB", event.id)

    return event


def subscribe(case_id: str) -> asyncio.Queue[Event]:
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=100)
    _subscribers[case_id].add(queue)
    return queue


def unsubscribe(case_id: str, queue: asyncio.Queue[Event]) -> None:
    _subscribers[case_id].discard(queue)
    if not _subscribers[case_id]:
        _subscribers.pop(case_id, None)
