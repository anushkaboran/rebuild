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
from dataclasses import dataclass, field
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


class ResearchNoArtifacts(ResearchInvalidResult):
    """The completed run carried no artifact metadata at all."""

    code = "RESEARCH_NO_ARTIFACTS"


class ResearchArtifactNotFound(ResearchInvalidResult):
    """Artifacts exist, but none contains the candidate document."""

    code = "RESEARCH_ARTIFACT_NOT_FOUND"


class ResearchArtifactAuth(ResearchInvalidResult):
    """The artifact endpoint rejected the credential (401/403)."""

    code = "RESEARCH_ARTIFACT_AUTH"


class ResearchArtifactDownload(ResearchInvalidResult):
    """Download/redirect failure (network, 4xx/5xx, empty body)."""

    code = "RESEARCH_ARTIFACT_DOWNLOAD"


class ResearchInvalidJson(ResearchInvalidResult):
    """A candidate document was found but is not valid JSON."""

    code = "RESEARCH_INVALID_JSON"


class ResearchPersistenceFailed(ResearchInvalidResult):
    """Validated results could not be written to SpacetimeDB."""

    code = "RESEARCH_PERSIST_FAILED"


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


def _as_text_list(value: Any) -> list[str]:
    """Arrays stay arrays; a bare string becomes a single-item list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(entry) for entry in value if entry is not None]
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(value)]


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
        missing_verification=_as_text_list(raw.get("missing_verification")),
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
                "queries_run": [
                    q if isinstance(q, (str, dict)) else str(q)
                    for q in payload["queries_run"]
                ],
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
        payload, source, probes = await fetch_payload(api, final)

    _, finished = import_result(
        run.model_copy(update={"thread_id": final.get("threadId")}), payload
    )
    finished = finished.model_copy(
        update={"error": f"artifact source: {source}"[:200]} if False else {}
    )
    logger.info(
        "research %s complete: %d candidate(s)", run_id, finished.candidates_valid
    )
    return finished


def _artifact_name_matches(artifact: dict[str, Any]) -> bool:
    """True for the candidate document, whatever CREAO calls the artifact.

    Matches the title *or* the id/path, because CREAO has been observed to use a
    human title while the file name only appears in `id`.
    """
    haystack = " ".join(
        str(artifact.get(key) or "") for key in ("title", "name", "id", "path", "filename")
    ).lower()
    return "research-candidates" in haystack or "research_candidates" in haystack


def _artifact_looks_json(artifact: dict[str, Any]) -> bool:
    return "json" in str(artifact.get("type") or "").lower() or _artifact_name_matches(artifact)


def _is_candidate_document(payload: Any) -> bool:
    return isinstance(payload, dict) and "item_ref" in payload and "candidates" in payload


def _result_containers(result: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Every list of artifact-like objects the run response actually contains.

    We do not assume a field name: whatever is present under the documented
    containers is inspected.
    """
    containers: list[tuple[str, list[dict[str, Any]]]] = []
    for key in ("artifacts", "outputs", "files", "attachments"):
        value = result.get(key)
        if isinstance(value, list):
            entries = [entry for entry in value if isinstance(entry, dict)]
            if entries:
                containers.append((f"result.{key}", entries))
    return containers


@dataclass
class ArtifactProbe:
    """Sanitized description of one artifact attempt (never holds a URL)."""

    container: str
    title: str | None
    type: str | None
    has_url: bool
    bytes: int | None = None
    json: bool | None = None
    keys: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "container": self.container,
            "title": self.title,
            "type": self.type,
            "has_url": self.has_url,
            "bytes": self.bytes,
            "json": self.json,
            "keys": self.keys,
            "error": self.error,
        }


async def fetch_payload(
    api: creao.CreaoAppClient, final: dict[str, Any]
) -> tuple[Any, str, list[ArtifactProbe]]:
    """Find and download the candidate document. Never guesses a location.

    Strategy, in order:
      1. every artifact that looks like the candidate document (by name/type);
      2. every other artifact, whatever its title — accepted only if its body
         parses as JSON *and* matches the contract (item_ref + candidates);
      3. inline text (`result.text` / `chatText`) as a last resort.

    Raises a coded error when nothing usable is found, with a sanitized probe
    list so the cause is diagnosable from the API.
    """
    result = final.get("result") or {}
    containers = _result_containers(result)
    probes: list[ArtifactProbe] = []

    if not containers:
        # No artifact metadata anywhere: report exactly that.
        raise ResearchNoArtifacts(
            "The completed Researcher run returned no artifact metadata "
            f"(result fields present: {', '.join(sorted(result.keys())) or 'none'})."
        )

    ordered: list[tuple[str, dict[str, Any]]] = []
    for container, entries in containers:
        ordered.extend(
            (container, entry) for entry in sorted(
                entries, key=lambda e: (not _artifact_name_matches(e), not _artifact_looks_json(e))
            )
        )

    saw_auth_error = False
    saw_download_error = False
    saw_invalid_json = False

    for container, artifact in ordered:
        probe = ArtifactProbe(
            container=container,
            title=artifact.get("title") or artifact.get("name"),
            type=artifact.get("type"),
            has_url=bool(artifact.get("url")),
        )
        url = artifact.get("url")
        if not url:
            probe.error = "no download URL on this artifact"
            probes.append(probe)
            continue

        try:
            raw = await api.download_artifact(url)  # redirect-safe, key-scoped
        except creao.CreaoError as exc:
            probe.error = f"{exc.code}: {str(exc)[:200]}"
            code = exc.code or ""
            if "AUTH" in code or "401" in str(exc) or "403" in str(exc):
                saw_auth_error = True
            else:
                saw_download_error = True
            probes.append(probe)
            continue
        except Exception as exc:  # noqa: BLE001
            probe.error = f"download failed: {str(exc)[:200]}"
            saw_download_error = True
            probes.append(probe)
            continue

        probe.bytes = len(raw)
        try:
            payload = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            probe.json = False
            probe.error = f"invalid JSON: {exc}"
            saw_invalid_json = True
            probes.append(probe)
            continue

        probe.json = True
        probe.keys = sorted(payload.keys()) if isinstance(payload, dict) else []
        probes.append(probe)

        if _is_candidate_document(payload):
            return payload, f"{container}:{probe.title or 'artifact'}", probes

        probe.error = "JSON, but not a research-candidates document"

    # 3. inline fallback
    for key in ("text", "chatText"):
        blob = result.get(key)
        if isinstance(blob, str):
            parsed = creao._parse_json_blob(blob)
            if _is_candidate_document(parsed):
                return parsed, f"result.{key}", probes

    if saw_auth_error:
        raise ResearchArtifactAuth(
            "The artifact endpoint rejected the Researcher credential while downloading "
            "research-candidates.json."
        )
    if saw_download_error:
        raise ResearchArtifactDownload(
            "research-candidates.json could not be downloaded (network or redirect failure)."
        )
    if saw_invalid_json:
        raise ResearchInvalidJson(
            "An artifact was found but it is not valid JSON."
        )
    raise ResearchArtifactNotFound(
        "No artifact in the completed run contains a research-candidates document."
    )


# kept for backwards compatibility with the previous private name
_fetch_payload = fetch_payload


def diagnose(final: dict[str, Any], probes: list[ArtifactProbe] | None = None) -> dict[str, Any]:
    """Sanitized structure report — no URLs, no credentials, no headers."""
    result = final.get("result") or {}
    return {
        "run_status": final.get("status"),
        "error_code": final.get("errorCode"),
        "result_fields": sorted(result.keys()) if isinstance(result, dict) else [],
        "artifacts_seen": len(probes or []),
        "probes": [probe.as_dict() for probe in (probes or [])],
    }


async def recover_run(research_id: str, creao_run_id: str) -> tuple[ResearchRun, str]:
    """Re-import an already-completed CREAO run into an existing research record.

    Never creates a CREAO run. Idempotent: candidates are keyed deterministically
    per run, and an already-complete run is returned untouched.
    """
    ensure_ready()
    run = store.research_runs.get(research_id)
    if run is None:
        raise ResearchError(
            f"Research run '{research_id}' is not known to this backend.",
            code="RESEARCH_NOT_FOUND",
            status_code=404,
        )

    completed_marker = (
        run.status == InvestigationStatus.complete
        and run.agent_run_id == creao_run_id
        and run.candidate_ids
    )

    async with client() as api:
        try:
            final = await api.get_run(creao_run_id)
        except creao.CreaoError as exc:
            raise ResearchError(
                f"Could not read CREAO run {creao_run_id}: {exc.code}",
                code="RESEARCH_RUN_UNREADABLE",
                status_code=502,
            ) from exc

        status = str(final.get("status") or "").lower()
        if status not in {"completed", "succeeded", "success"}:
            raise ResearchError(
                f"CREAO run {creao_run_id} is '{status or 'unknown'}' — only a completed "
                "run can be recovered.",
                code="RESEARCH_RUN_NOT_COMPLETED",
                status_code=409,
            )

        payload, source, probes = await fetch_payload(api, final)

    # Validate before touching anything.
    validate_result(payload, expected_item_ref=run.item_id)

    if completed_marker:
        return run, f"already recovered from {source}"

    with store.lock:
        store.research_runs[research_id] = run.model_copy(
            update={"agent_run_id": creao_run_id, "status": InvestigationStatus.running}
        )

    _, finished = import_result(run.model_copy(update={"agent_run_id": creao_run_id}), payload)

    # Persistence must succeed before the run is reported complete.
    if settings.store_backend == "spacetime":
        from . import spacetime_db

        try:
            spacetime_db.sync()
        except Exception as exc:  # noqa: BLE001
            failed = finished.model_copy(
                update={
                    "status": InvestigationStatus.failed,
                    "error": f"RESEARCH_PERSIST_FAILED: {str(exc)[:300]}",
                    "finished_at": utcnow(),
                }
            )
            with store.lock:
                store.research_runs[research_id] = failed
            raise ResearchPersistenceFailed(
                "research-candidates.json validated but could not be persisted."
            ) from exc

    logger.info(
        "recovered research %s from CREAO run %s (%s, %d candidate(s))",
        research_id,
        creao_run_id,
        source,
        finished.candidates_valid,
    )
    return finished, source


async def diagnose_run(creao_run_id: str) -> dict[str, Any]:
    """Read a run and report its (sanitized) output structure. No downloads kept."""
    ensure_ready()
    async with client() as api:
        final = await api.get_run(creao_run_id)
        probes: list[ArtifactProbe] = []
        try:
            _, source, probes = await fetch_payload(api, final)
            outcome = {"resolved": True, "source": source}
        except ResearchError as exc:
            outcome = {"resolved": False, "code": exc.code, "message": exc.message}
    report = diagnose(final, probes)
    report.update(outcome)
    return report


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
