"""Researcher endpoints (Rebuild Product Researcher, confirmed contract).

Canonical routes:
    POST /api/items/{item_id}/research
    GET  /api/research/{research_id}
    GET  /api/items/{item_id}/research

Case-scoped aliases are kept for backward compatibility:
    POST /api/cases/{case_id}/items/{item_id}/research
    GET  /api/cases/{case_id}/items/{item_id}/research/{run_id}
    GET  /api/cases/{case_id}/items/{item_id}/research
    GET  /api/cases/{case_id}/research/{run_id}/candidates

Research is advisory. Candidates are stored apart from inventory items, are
always `unverified_possible_match`, and never write to brand/model/
purchase_price/currency/estimated_value.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status

from ..deps import get_case_or_404, get_item_or_404
from ..models import (
    InvestigationStatus,
    ResearchRecoverRequest,
    ResearchRecoverResponse,
    ResearchRun,
    ResearchRunResponse,
    ResearchStartRequest,
    utcnow,
)
from ..services import creao, events, researcher
from ..store import new_id, store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["research"])


def _error(exc: researcher.ResearchError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
    )


# --------------------------------------------------------------------------- #
# Canonical routes
# --------------------------------------------------------------------------- #
@router.post(
    "/items/{item_id}/research",
    response_model=ResearchRun,
    summary="Start (or rejoin) product research for one belonging",
    responses={
        200: {"description": "An active research run already exists for this item"},
        202: {"description": "A new research run was queued"},
        404: {"description": "Unknown item"},
        409: {"description": "Mock mode"},
        503: {"description": "Researcher not configured"},
    },
)
async def start_research(
    item_id: str,
    body: ResearchStartRequest,
    background: BackgroundTasks,
    response: Response,
) -> ResearchRun:
    item = get_item_or_404(item_id)
    return _start(body, item, background, response)


@router.get(
    "/research/{research_id}",
    response_model=ResearchRunResponse,
    summary="Research status, candidates, queries and limitations",
)
def get_research(research_id: str) -> ResearchRunResponse:
    run = store.research_runs.get(research_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "RESEARCH_NOT_FOUND",
                "message": f"Research run '{research_id}' was not found.",
            },
        )
    return ResearchRunResponse(run=run, candidates=researcher.candidates_for(run.id))


@router.get(
    "/items/{item_id}/research",
    response_model=list[ResearchRun],
    summary="Saved research history for one belonging (newest first)",
)
def list_item_research(item_id: str) -> list[ResearchRun]:
    get_item_or_404(item_id)
    return store.list_research_runs_by_item(item_id)


@router.post(
    "/research/{research_id}/recover",
    response_model=ResearchRecoverResponse,
    summary="Re-import an already-completed CREAO run (no new run is started)",
    responses={
        404: {"description": "Unknown backend research record"},
        409: {"description": "The CREAO run has not completed"},
        502: {"description": "Artifact or run could not be read"},
    },
)
async def recover_research(
    research_id: str, body: ResearchRecoverRequest
) -> ResearchRecoverResponse:
    before = store.research_runs.get(research_id)
    already = bool(
        before is not None
        and before.status == InvestigationStatus.complete
        and before.agent_run_id == body.creao_run_id
        and before.candidate_ids
    )

    try:
        run, source = await researcher.recover_run(research_id, body.creao_run_id)
    except researcher.ResearchError as exc:
        raise _error(exc) from exc
    body_out = ResearchRecoverResponse(
        run=run, candidates=researcher.candidates_for(run.id), source=source,
        already_recovered=already,
    )
    events.publish("research.completed", run.case_id, {"research_run": run.model_dump(mode="json")})
    return body_out


@router.get(
    "/research/{research_id}/diagnostics",
    summary="Sanitized view of the CREAO run's output structure (no URLs, no secrets)",
    include_in_schema=False,
)
async def research_diagnostics(research_id: str) -> dict:
    run = store.research_runs.get(research_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESEARCH_NOT_FOUND", "message": f"Research run '{research_id}' was not found."},
        )
    if not run.agent_run_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "RESEARCH_NO_AGENT_RUN",
                "message": "This research record has no CREAO run id yet; supply one via /recover.",
            },
        )
    try:
        return await researcher.diagnose_run(run.agent_run_id)
    except researcher.ResearchError as exc:
        raise _error(exc) from exc


# --------------------------------------------------------------------------- #
# Backward-compatible case-scoped aliases
# --------------------------------------------------------------------------- #
@router.post(
    "/cases/{case_id}/items/{item_id}/research",
    response_model=ResearchRun,
    include_in_schema=False,
)
async def start_research_for_case(
    case_id: str,
    item_id: str,
    body: ResearchStartRequest,
    background: BackgroundTasks,
    response: Response,
) -> ResearchRun:
    get_case_or_404(case_id)
    item = get_item_or_404(item_id)
    if item.case_id != case_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ITEM_NOT_IN_CASE",
                "message": f"Item '{item_id}' is not in case '{case_id}'.",
            },
        )
    return _start(body, item, background, response)


@router.get(
    "/cases/{case_id}/items/{item_id}/research/{run_id}",
    response_model=ResearchRunResponse,
    include_in_schema=False,
)
def get_research_for_case(case_id: str, item_id: str, run_id: str) -> ResearchRunResponse:
    get_case_or_404(case_id)
    run = store.research_runs.get(run_id)
    if run is None or run.case_id != case_id or run.item_id != item_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESEARCH_NOT_FOUND", "message": f"Research run '{run_id}' was not found for this item."},
        )
    return ResearchRunResponse(run=run, candidates=researcher.candidates_for(run.id))


@router.get(
    "/cases/{case_id}/items/{item_id}/research",
    response_model=list[ResearchRun],
    include_in_schema=False,
)
def list_research_for_case(case_id: str, item_id: str) -> list[ResearchRun]:
    get_case_or_404(case_id)
    get_item_or_404(item_id)
    return [
        r for r in store.list_research_runs(case_id, item_id)
    ]


@router.get(
    "/cases/{case_id}/research/{run_id}/candidates",
    include_in_schema=False,
)
def get_candidates_for_case(case_id: str, run_id: str) -> dict:
    get_case_or_404(case_id)
    run = store.research_runs.get(run_id)
    if run is None or run.case_id != case_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESEARCH_NOT_FOUND", "message": f"Research run '{run_id}' was not found."},
        )
    return {
        "run": run.model_dump(mode="json"),
        "candidates": [c.model_dump(mode="json") for c in researcher.candidates_for(run_id)],
    }


# --------------------------------------------------------------------------- #
# Shared start logic
# --------------------------------------------------------------------------- #
def _start(
    body: ResearchStartRequest,
    item,
    background: BackgroundTasks,
    response: Response,
) -> ResearchRun:
    # Paid-run dedupe: never start a second run for an item that is in flight.
    existing = researcher.active_run_for(item.id)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return existing

    try:
        researcher.ensure_ready()
    except researcher.ResearchError as exc:
        raise _error(exc) from exc

    case = store.get_case(item.case_id)
    run = ResearchRun(
        id=new_id("rsch"),
        case_id=item.case_id,
        item_id=item.id,
        status=InvestigationStatus.queued,
        mode="live",
        app_id=researcher.settings.creao_research_app_id,
        region_currency=body.region_currency,
        requested_by=body.requested_by,
    )
    with store.lock:
        store.research_runs[run.id] = run
    events.publish("research.started", item.case_id, {"research_run": run.model_dump(mode="json")})
    response.status_code = status.HTTP_202_ACCEPTED

    description = f"Post-flood renter claim for {case.title}." if case else "Post-flood renter claim."
    background.add_task(_execute, run.id, description)
    return run


async def _execute(run_id: str, case_description: str) -> None:
    run = store.research_runs[run_id]
    item = store.items.get(run.item_id)
    if item is None:
        _fail(run_id, "item disappeared before the research run started")
        return
    try:
        started = await researcher.start(run, item=item, case_description=case_description)
        await researcher.poll_and_collect(started.id)
    except researcher.ResearchError as exc:
        logger.error("research %s failed: %s (%s)", run_id, exc.code, exc.message)
        _fail(run_id, f"{exc.code}: {exc.message}")
    except creao.CreaoError as exc:
        # Transport-level failures (auth, run failed, artifact, timeout).
        logger.error("research %s failed: %s (%s)", run_id, exc.code, exc.message)
        _fail(run_id, f"{exc.code}: {exc.message}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("research %s failed unexpectedly", run_id)
        _fail(run_id, f"unexpected error: {exc}")


def _fail(run_id: str, message: str) -> None:
    run = store.research_runs[run_id]
    failed = run.model_copy(
        update={
            "status": InvestigationStatus.failed,
            "error": message[:1000],
            "finished_at": utcnow(),
        }
    )
    with store.lock:
        store.research_runs[run_id] = failed
    events.publish("research.completed", failed.case_id, {"research_run": failed.model_dump(mode="json")})
