"""Rebuild Product Researcher — confirmed contract (Person 1, authoritative).

Same trigger mechanism as the Investigator (`POST /apps/{appId}/runs`, poll
`/runs/{id}`, authenticated redirect-safe artifact download) with its own App ID
and credential, so the two agents can never be confused.

Inputs (exact keys the agent expects):
    item_ref, visible_observations, distinguishing_features,
    evidence_context (JSON-serialized STRING), room_photos, region_currency

Output (`research-candidates.json`):
    { item_ref, candidates[], queries_run[], limitations[] }
    candidate = { candidate_id, brand, model, product_name, product_url,
                  matching_features, differing_features, missing_verification,
                  price_as_displayed, currency, source_accessed_at, status }

Validation is strict: a wrong `item_ref`, more than three candidates, a bad
candidate `status`, an unusable `product_url` or a malformed document fails the
run. Nothing is fabricated to fill a gap, and an empty candidate list is a valid
"no matches" result.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ..config import settings
from ..models import (
    CANDIDATE_STATUS,
    EvidenceItem,
    InvestigationStatus,
    ResearchCandidate,
    ResearchRun,
    utcnow,
)
from ..store import store
from . import creao, evidence_urls

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 3
_REQUIRED_KEYS = ("item_ref", "candidates", "queries_run", "limitations")


class ResearchError(RuntimeError):
    code = "RESEARCH_ERROR"
    status_code = 502

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class ResearchNotConfigured(ResearchError):
    code = "RESEARCH_NOT_CONFIGURED"
    status_code = 503


class ResearchInvalidResult(ResearchError):
    code = "RESEARCH_INVALID_RESULT"
    status_code = 502


def ensure_ready() -> None:
    if creao.is_mock():
        raise ResearchError(
            "Research runs are disabled in mock mode (CREAO_MODE=mock).",
            code="CREAO_MODE_MOCK",
            status_code=409,
        )
    missing = settings.research_missing_settings
    if missing:
        raise ResearchNotConfigured(
            "Researcher is not configured: " + ", ".join(missing) + "."
        )


def client() -> creao.CreaoAppClient:
    """Client bound to the Researcher app and its own credential."""
    return creao.CreaoAppClient(
        app_id=settings.creao_research_app_id,
        api_key=settings.creao_research_api_key,
    )


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def build_inputs(
    *,
    item: EvidenceItem,
    region_currency: str | None,
    case_description: str,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Return (agent inputs, public snapshot summary, signed photo URLs).

    Signed URLs are deliberately kept out of the summary: the public API must
    never expose them.
    """
    photos, photo_upload_ids = _room_photos(item)
    evidence_context = _evidence_context(item)

    inputs: dict[str, Any] = {
        "item_ref": item.id,
        "visible_observations": item.visible_observations or item.condition,
        "distinguishing_features": _distinguishing_features(item),
        # JSON-serialized STRING, as the agent expects — not an array.
        "evidence_context": json.dumps(evidence_context),
        "room_photos": photos,
    }
    if region_currency:
        inputs["region_currency"] = region_currency

    summary = {
        "item_ref": item.id,
        "visible_observations": inputs["visible_observations"],
        "distinguishing_features": inputs["distinguishing_features"],
        "evidence_context": evidence_context,
        "room_photo_upload_ids": photo_upload_ids,   # ids, never URLs
        "region_currency": region_currency,
        "case_description": case_description,
    }
    return inputs, summary, photos


def _distinguishing_features(item: EvidenceItem) -> list[str]:
    """Documented features only — nothing is inferred or invented.

    Taken from the Investigator's own record when present, otherwise from fields
    the reviewer can already see on the item.
    """
    record = item.agent_record or {}
    documented = record.get("distinguishing_features")
    if isinstance(documented, list) and documented:
        return [str(value) for value in documented if value]

    features: list[str] = []
    if item.brand:
        features.append(f"brand: {item.brand}")
    if item.model:
        features.append(f"model: {item.model}")
    if item.location_in_image:
        features.append(f"location in photo: {item.location_in_image}")
    return features


def _evidence_context(item: EvidenceItem) -> list[dict[str, Any]]:
    """Supporting evidence IDs, descriptions and attributed human corrections."""
    context: list[dict[str, Any]] = []
    for evidence_id in item.supporting_evidence_ids:
        upload = _upload_for_evidence(item, evidence_id)
        context.append(
            {
                "evidence_id": evidence_id,
                "description": upload.original_filename if upload else None,
                "human_corrections": _corrections_for(item, evidence_id),
            }
        )
    return context


def _upload_for_evidence(item: EvidenceItem, evidence_id: str):
    for upload in store.list_uploads(item.case_id):
        if upload.evidence_id == evidence_id:
            return upload
    if item.upload_id:
        return store.uploads.get(item.upload_id)
    return None


def _corrections_for(item: EvidenceItem, evidence_id: str) -> list[dict[str, Any]]:
    """Human edits, explicitly attributed. Empty when nobody has corrected it."""
    corrections: list[dict[str, Any]] = []
    for revision in store.list_revisions(item.id):
        for field, value in (revision.changes or {}).items():
            if field == "status":
                continue
            corrections.append(
                {
                    "field": field,
                    "value": value,
                    "confirmed_by": revision.editor,
                    "evidence_id": evidence_id,
                }
            )
    return corrections


def _room_photos(item: EvidenceItem) -> tuple[list[str], list[str]]:
    """HTTPS URLs for the item's supporting photographs (existing transport)."""
    if not settings.rebuild_public_base_url:
        return [], []
    urls: list[str] = []
    upload_ids: list[str] = []
    seen: set[str] = set()
    for evidence_id in item.supporting_evidence_ids:
        upload = _upload_for_evidence(item, evidence_id)
        if upload is None or upload.id in seen or upload.kind.value != "photo":
            continue
        seen.add(upload.id)
        try:
            urls.append(evidence_urls.upload_url(upload.id))
            upload_ids.append(upload.id)
        except evidence_urls.EvidenceUrlError:
            return [], []
    return urls, upload_ids


# --------------------------------------------------------------------------- #
# Output validation
# --------------------------------------------------------------------------- #
def validate_result(payload: Any, *, expected_item_ref: str) -> dict[str, Any]:
    """Strictly validate `research-candidates.json`. Raises instead of guessing."""
    if not isinstance(payload, dict):
        raise ResearchInvalidResult("research-candidates.json is not a JSON object.")

    missing = [key for key in _REQUIRED_KEYS if key not in payload]
    if missing:
        raise ResearchInvalidResult(
            "research-candidates.json is missing required fields: " + ", ".join(missing)
        )

    if payload["item_ref"] != expected_item_ref:
        raise ResearchInvalidResult(
            f"item_ref mismatch: agent returned '{payload['item_ref']}', "
            f"expected '{expected_item_ref}'."
        )

    candidates = payload["candidates"]
    if not isinstance(candidates, list):
        raise ResearchInvalidResult("`candidates` must be an array.")
    if len(candidates) > MAX_CANDIDATES:
        raise ResearchInvalidResult(
            f"agent returned {len(candidates)} candidates; the contract allows at most "
            f"{MAX_CANDIDATES}."
        )

    for key in ("queries_run", "limitations"):
        if not isinstance(payload[key], list):
            raise ResearchInvalidResult(f"`{key}` must be an array.")

    for index, candidate in enumerate(candidates):
        _validate_candidate(candidate, index)

    return payload


def _validate_candidate(candidate: Any, index: int) -> None:
    if not isinstance(candidate, dict):
        raise ResearchInvalidResult(f"candidate {index} is not an object.")

    status = candidate.get("status")
    if status != CANDIDATE_STATUS:
        raise ResearchInvalidResult(
            f"candidate {index} has status '{status}'; expected '{CANDIDATE_STATUS}'."
        )

    url = candidate.get("product_url")
    if url is not None and not (isinstance(url, str) and url.startswith(("http://", "https://"))):
        raise ResearchInvalidResult(
            f"candidate {index} has an unusable product_url: {url!r}"
        )

    for key in ("matching_features", "differing_features"):
        value = candidate.get(key)
        if value is not None and not isinstance(value, list):
            raise ResearchInvalidResult(f"candidate {index} `{key}` must be an array or null.")


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    nl = "\n"
    return nl.join(_as_list(value)) or None


def normalise_candidate(
    raw: dict[str, Any], *, run: ResearchRun, index: int
) -> ResearchCandidate:
    """Map the agent's candidate verbatim. Missing values stay null."""
    agent_id = raw.get("candidate_id")
    digest = hashlib.sha256(
        f"{run.id}|{agent_id or index}|{raw.get('product_url') or ''}".encode()
    ).hexdigest()[:16]
    return ResearchCandidate(
        id=f"rc_{digest}",
        candidate_id=str(agent_id) if agent_id is not None else None,
        run_id=run.id,
        case_id=run.case_id,
        item_id=run.item_id,
        brand=_text(raw.get("brand")),
        model=_text(raw.get("model")),
        product_name=_text(raw.get("product_name")),
        product_url=raw.get("product_url"),
        matching_features=_as_list(raw.get("matching_features")),
        differing_features=_as_list(raw.get("differing_features")),
        missing_verification=_text(raw.get("missing_verification")),
        price_as_displayed=_text(raw.get("price_as_displayed")),
        currency=_text(raw.get("currency")),
        source_accessed_at=_text(raw.get("source_accessed_at")),
        status=CANDIDATE_STATUS,
        unverified=True,
    )


def import_result(run: ResearchRun, payload: dict[str, Any]) -> tuple[list[ResearchCandidate], ResearchRun]:
    """Validate + persist candidates. Idempotent for the same run."""
    validate_result(payload, expected_item_ref=run.item_id)
    raw_candidates = payload["candidates"]
    candidates = [normalise_candidate(raw, run=run, index=i) for i, raw in enumerate(raw_candidates)]

    with store.lock:
        for candidate in candidates:
            store.research_candidates[candidate.id] = candidate
        finished = run.model_copy(
            update={
                "status": InvestigationStatus.complete,
                "error": None,
                "candidate_ids": [c.id for c in candidates],
                "candidates_valid": len(candidates),
                "queries_run": [str(q) for q in payload["queries_run"]],
                "limitations": [str(item) for item in payload["limitations"]],
                "finished_at": utcnow(),
            }
        )
        store.research_runs[run.id] = finished
    return candidates, finished


# --------------------------------------------------------------------------- #
# Run lifecycle
# --------------------------------------------------------------------------- #
async def start(
    run: ResearchRun, *, item: EvidenceItem, case_description: str
) -> ResearchRun:
    ensure_ready()
    inputs, summary, _ = build_inputs(
        item=item,
        region_currency=run.region_currency,
        case_description=case_description,
    )
    async with client() as api:
        created = await api.create_run(inputs)
    agent_run_id = created.get("id")
    if not agent_run_id:
        raise ResearchError(
            f"Researcher create-run response had no `id`: {sorted(created.keys())}",
            code="RESEARCH_BAD_RESPONSE",
        )
    updated = run.model_copy(
        update={
            "status": InvestigationStatus.running,
            "agent_run_id": agent_run_id,
            "app_id": settings.creao_research_app_id,
            "mode": "live",
            "input_snapshot": inputs,      # internal only (contains signed URLs)
            "input_summary": summary,      # public, redacted
        }
    )
    with store.lock:
        store.research_runs[run.id] = updated
    logger.info("research %s started on app %s", agent_run_id, settings.creao_research_app_id)
    return updated


async def poll_and_collect(run_id: str) -> ResearchRun:
    run = store.research_runs[run_id]
    async with client() as api:
        final = await api.poll_run(run.agent_run_id or "")
        payload = await _fetch_payload(api, final)

    _, finished = import_result(
        run.model_copy(update={"thread_id": final.get("threadId")}), payload
    )
    logger.info(
        "research %s complete: %d candidate(s)", run_id, finished.candidates_valid
    )
    return finished


async def _fetch_payload(api: creao.CreaoAppClient, final: dict[str, Any]) -> Any:
    result = final.get("result") or {}
    artifacts = [a for a in (result.get("artifacts") or []) if isinstance(a, dict)]
    preferred = [
        a for a in artifacts
        if str(a.get("title") or "").strip().lower() == "research-candidates.json"
    ]
    pool = preferred or [
        a for a in artifacts
        if "research-candidates" in str(a.get("title") or "").lower()
    ]

    errors: list[str] = []
    for artifact in pool:
        url = artifact.get("url")
        if not url:
            continue
        try:
            raw = await api.download_artifact(url)   # redirect-safe, key-scoped
            return json.loads(raw.decode())
        except json.JSONDecodeError as exc:
            errors.append(f"{artifact.get('title')}: invalid JSON ({exc})")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{artifact.get('title')}: {exc}")

    raise ResearchInvalidResult(
        "Researcher run completed but no readable research-candidates.json was found"
        + (f" ({'; '.join(errors)})" if errors else ""),
    )


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def candidates_for(run_id: str) -> list[ResearchCandidate]:
    return sorted(
        (c for c in store.research_candidates.values() if c.run_id == run_id),
        key=lambda c: c.created_at,
    )


def active_run_for(item_id: str) -> ResearchRun | None:
    """A queued/running run for this item, if one exists (paid-run dedupe)."""
    for run in store.list_research_runs_by_item(item_id):
        if run.status in (InvestigationStatus.queued, InvestigationStatus.running):
            return run
    return None
