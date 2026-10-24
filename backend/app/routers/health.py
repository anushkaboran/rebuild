"""Health and capability discovery."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from .. import __version__
from ..config import settings
from ..services import creao, spacetime, spacetime_db

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", summary="Liveness + wiring status")
def health() -> dict:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": __version__,
        "environment": settings.environment,
        "integrations": {
            # Secret-free: mode, whether it is usable, and which key prefix is
            # in use. The key itself is never returned.
            "creao": {
                **creao.configuration_status(),
                "public_base_url_configured": bool(settings.rebuild_public_base_url),
                "evidence_url_ttl_seconds": settings.rebuild_evidence_url_ttl_seconds,
            },
            "spacetimedb": spacetime.health(),
            "spacetimedb_db": spacetime_db.health(),
        },
    }


@router.get("/creao/status", tags=["creao"], summary="CREAO integration status (no secrets)")
def creao_status() -> dict:
    return creao.configuration_status()


@router.post(
    "/creao/validate",
    tags=["creao"],
    summary="Verify CREAO credentials and agent without starting a paid run",
    responses={503: {"description": "Live mode is not configured"}},
)
async def creao_validate() -> dict:
    """Calls the documented read-only endpoints only.

    `GET /v1/agents` confirms the key works and the agent is a personal agent
    owned by the same account; `GET /v1/account/usage` reports wallet balance.
    No credits are consumed.
    """
    if creao.is_mock():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CREAO_MODE_MOCK",
                "message": (
                    "CREAO_MODE=mock — there are no live credentials to validate. "
                    "Set CREAO_MODE=live with CREAO_API_KEY and CREAO_AGENT_ID, "
                    "then call this again. In mock mode investigations return "
                    "simulated data only."
                ),
            },
        )

    try:
        return await creao.validate_live_credentials()
    except creao.CreaoConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except creao.CreaoError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
