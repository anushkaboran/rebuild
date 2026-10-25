"""Rebuild API — FastAPI application entry point.

Run locally:
    cd backend
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app.main:app --reload --port 8000

Interactive docs: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import settings
from .services import spacetime_db
from .routers import (
    admin,
    cases,
    claims,
    demo,
    evidence,
    evidence_public,
    exports,
    health,
    investigations,
    recovery,
    research,
    review,
    uploads,
)

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.store_backend == "spacetime":
        # Durable state is authoritative: pull it into the cache before serving.
        try:
            spacetime_db.hydrate()
            logger.info("SpacetimeDB backend active (%s/%s)", settings.spacetimedb_url, settings.spacetimedb_db)
        except Exception as exc:  # noqa: BLE001 - fail loudly, never silently degrade
            logger.error("SpacetimeDB hydrate failed: %s", exc)
            raise
    logger.info("Rebuild API starting (env=%s)", settings.environment)
    if settings.creao_mode == "mock":
        logger.warning(
            "CREAO_MODE=mock — investigations return SIMULATED stub data and touch "
            "no network. Set CREAO_MODE=live with CREAO_APP_API_KEY for real "
            "investigations (see .env.example)."
        )
    elif settings.creao_missing_settings:
        logger.error(
            "CREAO_MODE=live but missing: %s — investigations will fail with 503 "
            "rather than fall back to simulated data.",
            ", ".join(settings.creao_missing_settings),
        )
    else:
        logger.info(
            "CREAO live mode: app-scoped API Trigger at %s (app %s, evidence transport %s)",
            settings.creao_app_base_url,
            settings.creao_app_id,
            settings.creao_evidence_transport,
        )
    yield
    logger.info("Rebuild API shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Collaborative post-flood recovery workspace. Upload room photos and "
            "receipts, let the CREAO Evidence Investigator propose belongings and "
            "condition, then review, correct and export an evidence-linked record."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "X-Rebuild-Item-Count"],
    )

    # Every route lives under /api except the docs/openapi UI.
    for module in (
        health, admin, cases, claims, uploads, investigations, evidence, evidence_public, recovery, research, review, exports, demo
    ):
        app.include_router(module.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "path": request.url.path},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": "Validation failed", "errors": exc.errors()},
        )

    @app.middleware("http")
    async def persist_after_write(request: Request, call_next):
        """Write-behind persistence: push changed rows after mutating requests."""
        response = await call_next(request)
        if (
            settings.store_backend == "spacetime"
            and request.method in {"POST", "PATCH", "PUT", "DELETE"}
            and response.status_code < 400
        ):
            try:
                pushed = spacetime_db.sync()
                if pushed:
                    logger.info("SpacetimeDB sync: %s", pushed)
            except Exception:  # noqa: BLE001 - the request already succeeded
                logger.exception("SpacetimeDB sync failed after %s %s", request.method, request.url.path)
        return response

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "service": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "health": "/api/health",
        }

    return app


app = create_app()
