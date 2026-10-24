"""SpacetimeDB mirror.

The frontend will subscribe to SpacetimeDB directly for live collaboration.
The backend's job is to publish authoritative writes (cases, uploads, items,
investigations) as reducer calls so the database stays the shared source of
truth.

This module is intentionally thin and feature-flagged by `SPACETIMEDB_ENABLED`:
when the flag is off (the default) every call is a logged no-op, so the backend
runs standalone on day one of the hackathon.

--------------------------------------------------------------------------
Confirm with the SpacetimeDB module in /spacetimedb
--------------------------------------------------------------------------
Assumed reducer name: `publish_event(payload_json: String)` on module
`REBUILD_MODULE`, invoked over the SpacetimeDB HTTP API:

    POST {SPACETIMEDB_HOST}/v1/database/{db}/{module}/call/publish_event
    { "args": ["<json string>"] }

If the reducer is named differently, change `_REDUCER` only.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_REDUCER = "publish_event"


def enabled() -> bool:
    return settings.spacetime_enabled


def publish_event(event: Any) -> None:
    """Best-effort mirror of one domain event. Never raises."""
    if not enabled():
        return

    url = (
        f"{settings.spacetime_host.rstrip('/')}/v1/database/"
        f"{settings.spacetime_db}/{settings.spacetime_module}/call/{_REDUCER}"
    )
    payload = event.model_dump(mode="json") if hasattr(event, "model_dump") else event

    try:
        response = httpx.post(url, json={"args": [payload]}, timeout=5.0)
        if response.status_code >= 400:
            logger.warning(
                "SpacetimeDB reject %s: %s", response.status_code, response.text[:300]
            )
    except Exception:  # noqa: BLE001 - collaboration mirror must not break writes
        logger.warning("SpacetimeDB unreachable at %s", url, exc_info=False)


def health() -> dict[str, Any]:
    """Reported by GET /api/health so the demo can show wiring status."""
    return {
        "enabled": enabled(),
        "host": settings.spacetime_host,
        "database": settings.spacetime_db,
        "module": settings.spacetime_module,
    }
