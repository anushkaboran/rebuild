"""CREAO Evidence Investigator — app-scoped API Trigger client.

===========================================================================
VERIFIED AGAINST PERSON 2'S TESTED INTEGRATION
===========================================================================
Shapes below come from the captured examples on the `agent` branch
(`agent/api-create-response.json`, `api-poll-response.json`,
`api-run-result.json`) and `agent/SKILL.md` / `docs/contract.md` — not from
guesses:

  POST {base}/apps/{APP_ID}/runs          body: {"inputs": {...}}
    → {"id","status","appId","appName"}                        (pending)

  GET  {base}/apps/{APP_ID}/runs/{id}
    → {"id","status","threadId","result":{
         "text": "<summary JSON as a string>",
         "chatText": "<transcript>",
         "artifacts":[{"id","url","type","title"}]},
       "errorCode","startedAt","completedAt","createdAt"}

  GET  <artifact.url>   (authenticated — same agent.creao.ai host)

The run's authoritative output is the `rebuild-result.json` artifact. The
agent's top-level schema (agent/docs/contract.md) is:
    case_summary, evidence[], items[], findings[], questions[],
    limitations[], receipt_extracts[]

Note the deliberate absence of `agent_id` in this request body: the app-scoped
route identifies the agent by APP_ID in the URL, and CREAO documents that
sending an agent id here is wrong (`/v1` uses agent_id + `input`; this route
uses appId + `inputs`).

===========================================================================
EVIDENCE TRANSPORT — NOT ASSUMED
===========================================================================
The agent declares `room_photos (files, required)` and `receipt_files (files)`
form fields, but CREAO marks workspace/agent file **upload** as "Not supported"
on the app-scoped path. Nothing in Person 2's captured examples shows file
bytes being transmitted, and the filenames in their run (`01_room_armchair.jpg`
etc.) match files committed to the agent branch — i.e. the demo run most likely
read **pre-attached** reference files.

So the transport is an explicit setting and is refused when unset:
    CREAO_EVIDENCE_TRANSPORT=unset        → 503, no run started (default)
    CREAO_EVIDENCE_TRANSPORT=pre_attached → manifest only; files live in the agent
    CREAO_EVIDENCE_TRANSPORT=data_url     → inline base64 (UNVERIFIED hypothesis)
    CREAO_EVIDENCE_TRANSPORT=none         → text only, declared as no evidence

`evidence_transport` is recorded on every investigation so the UI, the PDF and
the team can always tell what was actually sent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

DAMAGE_LEVELS = {"none", "light", "moderate", "severe", "destroyed", "unknown"}

# Required top-level keys of rebuild-result.json (agent contract).
REQUIRED_RESULT_KEYS = (
    "case_summary",
    "evidence",
    "items",
    "findings",
    "questions",
    "limitations",
    "receipt_extracts",
)

TERMINAL_OK = {"completed", "succeeded", "success"}
TERMINAL_BAD = {"failed", "cancelled", "error"}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class CreaoError(RuntimeError):
    code = "CREAO_ERROR"
    status_code = 502

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class CreaoConfigError(CreaoError):
    code = "CREAO_NOT_CONFIGURED"
    status_code = 503


class CreaoEvidenceTransportUnverified(CreaoConfigError):
    code = "CREAO_EVIDENCE_TRANSPORT_UNVERIFIED"


class CreaoAuthError(CreaoError):
    code = "CREAO_AUTHENTICATION_REQUIRED"
    status_code = 502


class CreaoRunFailed(CreaoError):
    code = "CREAO_RUN_FAILED"
    status_code = 502


class CreaoTimeout(CreaoError):
    code = "CREAO_RUN_TIMEOUT"
    status_code = 504


class CreaoEvidenceUnreachable(CreaoConfigError):
    """Evidence URLs would not resolve for CREAO — refuse before spending."""

    code = "CREAO_EVIDENCE_URLS_UNAVAILABLE"


class CreaoInvalidResult(CreaoError):
    code = "CREAO_INVALID_RESULT"
    status_code = 502


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #
@dataclass
class EvidenceFile:
    """One piece of evidence to declare to the agent."""

    evidence_id: str          # ev_001 — assigned by the backend, per the contract
    filename: str
    content_type: str
    kind: str                 # "photo" | "receipt"
    path: Path | None = None
    description: str | None = None
    # Public HTTPS URL the agent will fetch. Built by the caller (signed URL) or
    # derived from the staged demo base when CREAO_EVIDENCE_TRANSPORT=demo_url.
    url: str | None = None


@dataclass
class AgentItem:
    """One belonging, mapped from the agent's items[] record."""

    name: str
    # Fields the app-scoped agent actually emits.
    agent_item_id: str | None = None
    location_in_image: str | None = None
    visible_observations: str | None = None
    supporting_evidence_ids: list[str] = field(default_factory=list)
    review_status: str | None = None
    brand: str | None = None
    model: str | None = None
    purchase_price: str | None = None
    currency: str | None = None
    # Fields kept for the existing Rebuild model. The agent does not invent a
    # damage grade or a confidence score, so these stay neutral.
    category: str = "other"
    room: str | None = None
    description: str | None = None
    condition: str | None = None
    damage_level: str = "unknown"
    confidence: float = 0.0
    estimated_value: float | None = None
    bbox: dict[str, float] | None = None
    reasoning: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    items: list[AgentItem] = field(default_factory=list)
    summary: str | None = None
    run_id: str | None = None
    thread_id: str | None = None
    status: str | None = None
    # App-scoped runs do not report a model id or a credit figure today; kept so
    # the Investigation contract is stable if CREAO exposes them later.
    model: str | None = None
    credits_used: float | None = None
    mode: str = "live"                 # "live" | "mock"
    stub: bool = False                 # True ONLY in mock mode
    evidence_transport: str = "unset"
    evidence_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)   # full rebuild-result.json
    raw: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Mode + readiness
# --------------------------------------------------------------------------- #
def mode() -> str:
    return settings.creao_mode


def is_mock() -> bool:
    return settings.creao_mode == "mock"


def _key_prefix(key: str) -> str | None:
    if not key:
        return None
    for prefix in ("capi_", "cr_sk_"):
        if key.startswith(prefix):
            return prefix
    return "unrecognized"


def configuration_status() -> dict[str, Any]:
    """Secret-free description of the integration state."""
    return {
        "mode": settings.creao_mode,
        "configured": is_mock() or settings.creao_configured,
        "transport": "app-trigger" if not is_mock() else "mock",
        "base_url": settings.creao_app_base_url if not is_mock() else None,
        "app_id": settings.creao_app_id or None,
        "api_key_present": bool(settings.creao_app_api_key),
        "api_key_prefix": _key_prefix(settings.creao_app_api_key),
        "evidence_transport": settings.creao_evidence_transport,
        "evidence_ready": is_mock() or settings.creao_evidence_ready,
        "missing": settings.creao_missing_settings,
    }


def ensure_ready() -> None:
    """Fail fast in live mode. Never degrades to the stub."""
    if is_mock():
        return

    missing = settings.creao_missing_settings
    if missing:
        raise CreaoConfigError(
            "CREAO live mode is on but configuration is incomplete: "
            + ", ".join(missing)
            + ". Add them to .env (see .env.example) or set CREAO_MODE=mock. "
            "The backend will not substitute simulated evidence."
        )

    if _key_prefix(settings.creao_app_api_key) == "cr_sk_":
        raise CreaoConfigError(
            "CREAO_APP_API_KEY looks like a /v1 Account key (cr_sk_…). The "
            "app-scoped trigger needs the capi_ key created from the agent "
            "detail page in agent.creao.ai. A /v1 key on this route is 401."
        )


def ensure_evidence_transport() -> str:
    """Refuse to spend a credit on a run that cannot carry the evidence."""
    if is_mock():
        return "mock"
    transport = settings.creao_evidence_transport
    if transport == "unset":
        raise CreaoEvidenceTransportUnverified(
            "Evidence transport is not configured. The confirmed mechanism is "
            "public HTTPS URLs, so set CREAO_EVIDENCE_TRANSPORT=url together "
            "with REBUILD_PUBLIC_BASE_URL (an origin CREAO can reach), or "
            "CREAO_EVIDENCE_TRANSPORT=demo_url for the staged demo fixture. "
            "The backend will not start a run whose photos cannot be fetched."
        )
    if transport == "url" and not settings.rebuild_public_base_url:
        raise CreaoEvidenceUnreachable(
            "CREAO_EVIDENCE_TRANSPORT=url needs REBUILD_PUBLIC_BASE_URL pointing "
            "at an externally reachable origin; without it the evidence links "
            "would only resolve on this machine."
        )
    return transport


async def validate_live_credentials() -> dict[str, Any]:
    """Read-only probe: proves the capi_ key and APP_ID work, spends no credits.

    Uses the documented app-scoped list route `GET /apps/{appId}/runs`, which
    is the only read-only way to confirm access on this path.
    """
    ensure_ready()
    async with CreaoAppClient() as client:
        listing = await client.list_runs(limit=1)

    runs = listing.get("runs") if isinstance(listing, dict) else None
    return {
        "ok": True,
        "mode": settings.creao_mode,
        "transport": "app-trigger",
        "app_id": settings.creao_app_id,
        "base_url": settings.creao_app_base_url,
        "api_key_prefix": _key_prefix(settings.creao_app_api_key),
        "recent_runs_visible": len(runs) if isinstance(runs, list) else None,
        "evidence_transport": settings.creao_evidence_transport,
        "evidence_ready": settings.creao_evidence_ready,
    }


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
async def investigate(
    *,
    case_description: str,
    room_name: str,
    evidence: list[EvidenceFile],
    confirmed_items: list[dict[str, Any]] | None = None,
    transport: str | None = None,
    previous_result_url: str | None = None,
) -> AgentResult:
    """Run one investigation through the app-scoped API Trigger."""
    if is_mock():
        logger.info("CREAO mock mode — returning stub investigation (no network)")
        first = evidence[0] if evidence else None
        return stub_investigation(
            Path(first.filename) if first else Path("upload"),
            first.content_type if first else "image/jpeg",
            room_name,
        )

    ensure_ready()
    transport = transport or ensure_evidence_transport()
    if transport == "unset":
        ensure_evidence_transport()

    if len(evidence) > settings.creao_max_evidence_files:
        raise CreaoConfigError(
            f"Evidence count {len(evidence)} exceeds the agent's documented cap of "
            f"{settings.creao_max_evidence_files} attached files combined "
            "(FILE_LIMIT_EXCEEDED)."
        )

    # Resolve the public URL for every piece of evidence. Nothing is sent until
    # each one is known to be fetchable from outside this machine.
    for item in evidence:
        item.url = _resolve_evidence_url(item, transport)

    urls = [e.url for e in evidence if e.url]
    if settings.creao_verify_evidence_urls and urls:
        await verify_evidence_urls(urls)

    inputs = build_inputs(
        case_description=case_description,
        room_name=room_name,
        evidence=evidence,
        confirmed_items=confirmed_items or [],
        transport=transport,
        previous_result_url=previous_result_url,
    )

    async with CreaoAppClient() as client:
        created = await client.create_run(inputs)
        run_id = created.get("id")
        if not run_id:
            raise CreaoInvalidResult(
                "CREAO create-run response had no `id` field: "
                f"{sorted(created.keys())}"
            )
        logger.info(
            "CREAO app run %s created (status=%s, app=%s)",
            run_id,
            created.get("status"),
            created.get("appId"),
        )

        final = await client.poll_run(run_id)
        return await build_result(client, final, transport=transport, evidence=evidence)


def _resolve_evidence_url(item: EvidenceFile, transport: str) -> str:
    if transport == "demo_url":
        base = settings.creao_demo_evidence_base_url.rstrip("/")
        return f"{base}/{item.filename.lstrip('/')}"
    if transport == "url":
        if not item.url:
            raise CreaoEvidenceUnreachable(
                f"Evidence {item.evidence_id} ({item.filename}) has no public URL. "
                "Upload it through /api/cases/{id}/uploads so a signed URL can be issued."
            )
        return item.url
    raise CreaoEvidenceUnreachable(f"Evidence is not deliverable under transport '{transport}'.")


async def verify_evidence_urls(urls: list[str]) -> None:
    """Confirm CREAO could fetch every URL before a run is created."""
    from . import evidence_urls  # local import keeps the module import graph flat

    checks = await evidence_urls.check_urls(urls)
    failures = [c for c in checks if not c.ok]
    if failures:
        raise CreaoEvidenceUnreachable(
            "Evidence URLs are not fetchable from outside this machine, so no "
            "run was started: "
            + evidence_urls.summarise_failures(failures)
            + ". CREAO fetches these itself — fix REBUILD_PUBLIC_BASE_URL / the "
            "tunnel, or set CREAO_EVIDENCE_TRANSPORT=demo_url for the fixture."
        )


def build_inputs(
    *,
    case_description: str,
    room_name: str,
    evidence: list[EvidenceFile],
    confirmed_items: list[dict[str, Any]],
    transport: str,
    previous_result_url: str | None = None,
) -> dict[str, Any]:
    """Build the exact `inputs` object Person 2 confirmed.

    Key shapes that are load-bearing:
      * room_photos / receipt_files are **arrays of HTTPS URL strings**
      * evidence_manifest is a **JSON-serialized string**, not an array
      * confirmed_items is a **JSON-serialized string**, not an array
      * previous_result is omitted entirely on a first investigation
    """
    manifest = [
        {
            "evidence_id": item.evidence_id,
            "filename": item.filename,
            "description": item.description or "",
        }
        for item in evidence
    ]

    photos = [e for e in evidence if e.kind == "photo"]
    receipts = [e for e in evidence if e.kind != "photo"]

    inputs: dict[str, Any] = {
        "case_description": case_description,
        "room_name": room_name,
        "room_photos": [e.url for e in photos if e.url],
        "receipt_files": [e.url for e in receipts if e.url],
        "evidence_manifest": json.dumps(manifest),
        "confirmed_items": json.dumps(confirmed_items),
    }

    if transport == "none":
        inputs["room_photos"] = []
        inputs["receipt_files"] = []
        inputs["evidence_manifest"] = json.dumps([])

    # First run: the key is absent, not empty. Follow-ups carry the prior result.
    if previous_result_url:
        inputs["previous_result"] = previous_result_url

    return inputs


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
class CreaoAppClient:
    """App-scoped API Trigger client (agent.creao.ai/api/v1)."""

    def __init__(self) -> None:
        self._base = settings.creao_app_base_url.rstrip("/")
        self._app = settings.creao_app_id
        self._api_host = urlparse(self._base).netloc
        self._client = httpx.AsyncClient(
            timeout=settings.creao_http_timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.creao_app_api_key}",
                "Accept": "application/json",
                "User-Agent": "rebuild-backend/0.3",
            },
        )

    async def __aenter__(self) -> "CreaoAppClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    # -- runs --------------------------------------------------------------- #
    async def create_run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """POST /apps/{appId}/runs — body is exactly {"inputs": {...}}.

        No `agent_id`: the app-scoped route identifies the agent by URL.
        """
        return await self._json(
            "POST", f"/apps/{self._app}/runs", json={"inputs": inputs}
        )

    async def get_run(self, run_id: str) -> dict[str, Any]:
        return await self._json("GET", f"/apps/{self._app}/runs/{run_id}")

    async def list_runs(self, limit: int = 1) -> dict[str, Any]:
        return await self._json(
            "GET", f"/apps/{self._app}/runs", params={"limit": max(1, min(limit, 100))}
        )

    async def poll_run(self, run_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + settings.creao_run_timeout_seconds
        interval = settings.creao_poll_interval_seconds
        last_status = "unknown"

        while True:
            try:
                run = await self.get_run(run_id)
            except CreaoError as exc:
                if exc.code != "RATE_LIMITED":
                    raise
                await asyncio.sleep(interval)
                continue

            status = str(run.get("status") or "").lower()
            if status != last_status:
                logger.info("CREAO app run %s → %s", run_id, status)
                last_status = status

            if status in TERMINAL_OK:
                return run
            if status in TERMINAL_BAD:
                raise CreaoRunFailed(
                    f"CREAO run {run_id} ended '{status}'"
                    + (f" with errorCode {run.get('errorCode')}" if run.get("errorCode") else ""),
                    code=str(run.get("errorCode") or "CREAO_RUN_FAILED"),
                )

            if time.monotonic() >= deadline:
                raise CreaoTimeout(
                    f"CREAO run {run_id} still '{status or 'pending'}' after "
                    f"{settings.creao_run_timeout_seconds:.0f}s. Raise "
                    "CREAO_RUN_TIMEOUT_SECONDS or check the run in agent.creao.ai."
                )
            await asyncio.sleep(interval)

    # -- artifacts ---------------------------------------------------------- #
    async def download_artifact(self, url: str) -> bytes:
        """Download an artifact, following the API's redirect safely.

        Confirmed against a real run: the artifact URL is a **302** from the
        agent.creao.ai API to a presigned S3 object. The API call needs the
        capi_ key; the S3 hop must NOT receive it.

        So redirects are followed manually, one hop, with a clean client — this
        is both correct (httpx does not follow by default) and leak-proof.
        """
        same_api_host = urlparse(url).netloc == self._api_host

        if same_api_host:
            response = await self._client.get(url)
        else:
            async with httpx.AsyncClient(
                timeout=settings.creao_http_timeout_seconds
            ) as plain:
                response = await plain.get(url)

        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise CreaoError(
                    "Artifact download redirected without a Location header.",
                    code="CREAO_ARTIFACT_DOWNLOAD_FAILED",
                )
            target = urljoin(url, location)
            # Never carry the API key to the storage host.
            async with httpx.AsyncClient(
                timeout=settings.creao_http_timeout_seconds, follow_redirects=True
            ) as anonymous:
                response = await anonymous.get(target)

        if response.status_code >= 400:
            raise CreaoError(
                f"Artifact download failed ({response.status_code}) at "
                f"{urlparse(url).path}: {response.text[:200]}",
                code="CREAO_ARTIFACT_DOWNLOAD_FAILED",
            )
        if not response.content:
            raise CreaoError(
                f"Artifact download returned an empty body for {urlparse(url).path}.",
                code="CREAO_ARTIFACT_DOWNLOAD_FAILED",
            )
        return response.content

    # -- transport ---------------------------------------------------------- #
    async def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self._base}{path}"
        response = await self._client.request(method, url, **kwargs)
        if response.status_code < 400:
            try:
                return response.json()
            except json.JSONDecodeError:
                raise CreaoInvalidResult(f"CREAO returned non-JSON at {path}") from None

        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {}

        code = str(body.get("error") or body.get("errorCode") or f"HTTP_{response.status_code}")
        detail = body.get("message") or body.get("detail") or response.text[:300]

        if response.status_code == 401:
            raise CreaoAuthError(
                "CREAO rejected the app-scoped key (401). Use the capi_ key from "
                "the agent detail page in agent.creao.ai — a /v1 cr_sk_ key is "
                "not accepted on this route."
            )
        if response.status_code == 404:
            raise CreaoError(
                f"CREAO 404 ({code}): {detail}. Check CREAO_APP_ID "
                f"({settings.creao_app_id}) matches the tested app.",
                code=code,
                status_code=502,
            )
        raise CreaoError(f"CREAO {code}: {detail}", code=code, status_code=502)


# --------------------------------------------------------------------------- #
# Result retrieval + validation
# --------------------------------------------------------------------------- #
async def build_result(
    client: CreaoAppClient,
    run: dict[str, Any],
    *,
    transport: str,
    evidence: list[EvidenceFile],
) -> AgentResult:
    result = run.get("result") or {}
    payload, source = await _extract_result_payload(client, result)
    validate_result_payload(payload)
    validate_evidence_ids(payload, [e.evidence_id for e in evidence])

    normalised = normalise_result(payload)
    normalised.run_id = run.get("id")
    normalised.thread_id = run.get("threadId")
    normalised.status = run.get("status")
    normalised.mode = "live"
    normalised.stub = False
    normalised.evidence_transport = transport
    normalised.evidence_ids = [e.evidence_id for e in evidence]
    normalised.payload = payload
    normalised.raw = {"source": source, "run": {
        "id": run.get("id"),
        "status": run.get("status"),
        "errorCode": run.get("errorCode"),
        "startedAt": run.get("startedAt"),
        "completedAt": run.get("completedAt"),
    }}
    if not normalised.summary:
        normalised.summary = _summary_from_text(result.get("text"))
    return normalised


async def _extract_result_payload(
    client: CreaoAppClient, result: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Fetch rebuild-result.json from result.artifacts, as Person 2's run shows."""

    artifacts = [a for a in (result.get("artifacts") or []) if isinstance(a, dict)]
    preferred = [
        a for a in artifacts
        if str(a.get("title") or "").strip().lower() == "rebuild-result.json"
    ]
    candidates = preferred or [
        a for a in artifacts
        if str(a.get("type") or "").startswith("application/json")
        or "rebuild-result" in str(a.get("title") or "").lower()
    ]

    errors: list[str] = []
    for artifact in candidates:
        url = artifact.get("url")
        if not url:
            continue
        try:
            raw = await client.download_artifact(url)
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict):
                logger.info("rebuild-result.json read from artifact '%s'", artifact.get("title"))
                return payload, f"artifact:{artifact.get('title')}"
            errors.append("artifact JSON was not an object")
        except Exception as exc:  # noqa: BLE001 - report, never invent
            errors.append(f"{artifact.get('title')}: {exc}")

    # Last resort: some runs inline the payload in result.text.
    for key in ("text", "chatText"):
        blob = result.get(key)
        if isinstance(blob, str):
            parsed = _parse_json_blob(blob)
            if isinstance(parsed, dict) and any(k in parsed for k in REQUIRED_RESULT_KEYS):
                return parsed, f"result.{key}"

    raise CreaoInvalidResult(
        "CREAO run completed but no rebuild-result.json artifact could be read"
        + (f" ({'; '.join(errors)})" if errors else "")
        + ". Refusing to synthesise an inventory."
    )


async def fetch_result_payload(
    client: "CreaoAppClient", run: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Public entry point: read and validate rebuild-result.json from a run.

    Used by recovery for an already-completed run; performs no run creation.
    """
    result = run.get("result") or {}
    payload, source = await _extract_result_payload(client, result)
    validate_result_payload(payload)
    return payload, source


def validate_result_payload(payload: Any) -> None:
    """Validate against the agent contract before anything is stored."""
    if not isinstance(payload, dict):
        raise CreaoInvalidResult("rebuild-result.json is not a JSON object.")
    missing = [k for k in REQUIRED_RESULT_KEYS if k not in payload]
    if missing:
        raise CreaoInvalidResult(
            "rebuild-result.json is missing required top-level fields: "
            + ", ".join(missing)
        )
    for key in ("evidence", "items", "findings", "questions", "limitations", "receipt_extracts"):
        if not isinstance(payload.get(key), list):
            raise CreaoInvalidResult(f"rebuild-result.json `{key}` must be an array.")
    if not isinstance(payload.get("case_summary"), str):
        raise CreaoInvalidResult("rebuild-result.json `case_summary` must be a string.")


def validate_evidence_ids(payload: dict[str, Any], supplied_ids: list[str]) -> None:
    """Every evidence reference must be one of the IDs this backend assigned.

    The agent contract says IDs are carried through unchanged; a mismatch means
    we cannot trust the linkage between a claim and its photograph, so the run
    fails rather than storing unverifiable references.
    """
    allowed = set(supplied_ids)
    if not allowed:
        return

    reported = []
    for entry in payload.get("evidence") or []:
        if isinstance(entry, dict) and entry.get("evidence_id") is not None:
            reported.append(str(entry["evidence_id"]))

    unknown = sorted({eid for eid in reported if eid not in allowed})
    if unknown:
        raise CreaoInvalidResult(
            "rebuild-result.json references evidence IDs that were never sent: "
            + ", ".join(unknown)
            + f" (sent: {', '.join(sorted(allowed))}). Refusing to store "
            "findings whose evidence cannot be traced."
        )

    if not reported:
        raise CreaoInvalidResult(
            "rebuild-result.json has no evidence[] entries although "
            f"{len(allowed)} evidence file(s) were sent."
        )

    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        for ref in item.get("supporting_evidence_ids") or []:
            if str(ref) not in allowed:
                raise CreaoInvalidResult(
                    f"item {item.get('item_id')} cites unknown evidence id '{ref}'."
                )


_ITEMS_KEYS = ("items",)


def _iter_raw_items(payload: Any):
    if isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict):
                yield entry
        return
    if not isinstance(payload, dict):
        return
    for key in _ITEMS_KEYS + ("belongings",):
        container = payload.get(key)
        if isinstance(container, list):
            for entry in container:
                if isinstance(entry, dict):
                    yield entry
            return


def normalise_result(payload: Any) -> AgentResult:
    """Map agent items[] onto Rebuild's AgentItem without inventing values.

    The agent never emits a damage grade, a confidence score or a replacement
    value — it only records what is visible. Those Rebuild fields therefore stay
    neutral; the agent's own words are preserved in the dedicated fields.
    """
    items: list[AgentItem] = []
    for raw in _iter_raw_items(payload):
        name = str(raw.get("name") or "").strip()
        if not name:
            continue  # never invent an item

        damage = "unknown"
        for key in ("damage_level", "damage"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip().lower() in DAMAGE_LEVELS:
                damage = value.strip().lower()
                break

        supporting = raw.get("supporting_evidence_ids")
        items.append(
            AgentItem(
                name=name[:200],
                agent_item_id=_str(raw.get("item_id")),
                location_in_image=_str(raw.get("location_in_image")),
                visible_observations=_str(raw.get("visible_observations")),
                supporting_evidence_ids=[str(s) for s in supporting] if isinstance(supporting, list) else [],
                review_status=_str(raw.get("review_status")),
                brand=_str(raw.get("brand")),
                model=_str(raw.get("model")),
                purchase_price=_str(raw.get("purchase_price")),
                currency=_str(raw.get("currency")),
                room=_str(raw.get("room")),
                description=_str(raw.get("location_in_image")),
                condition=_str(raw.get("visible_observations")),
                damage_level=damage,
                confidence=0.0,
                estimated_value=None,
                bbox=None,
                raw=raw,
            )
        )

    summary = None
    if isinstance(payload, dict):
        summary = _str(payload.get("case_summary"))
    return AgentResult(items=items, summary=summary, mode="live", stub=False)


def _str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value)[:4000]
    return None


def _summary_from_text(text: Any) -> str | None:
    """result.text is a JSON string: {"status":"success","summary":"..."}."""
    if not isinstance(text, str):
        return None
    parsed = _parse_json_blob(text)
    if isinstance(parsed, dict):
        for key in ("summary", "message", "error"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:2000]
    return text.strip()[:2000] or None


_FENCE_MARKER = "```"


def _parse_json_blob(text: str) -> Any | None:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    if _FENCE_MARKER in text:
        for chunk in text.split(_FENCE_MARKER):
            candidate = chunk.strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = stripped.find(opener), stripped.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


# --------------------------------------------------------------------------- #
# Deterministic stub (mock mode only)
# --------------------------------------------------------------------------- #
_ROOM_LIBRARY: dict[str, list[tuple[str, str, str, float]]] = {
    "living room": [
        ("Sofa", "furniture", "severe", 1200),
        ("Coffee table", "furniture", "moderate", 250),
        ("Area rug", "textile", "destroyed", 300),
        ("Floor lamp", "lighting", "moderate", 90),
    ],
    "kitchen": [
        ("Refrigerator", "appliance", "severe", 1800),
        ("Dishwasher", "appliance", "moderate", 900),
        ("Kitchen cabinet set", "millwork", "severe", 2400),
        ("Microwave", "appliance", "light", 180),
    ],
    "bedroom": [
        ("Queen mattress", "furniture", "destroyed", 1100),
        ("Dresser", "furniture", "moderate", 400),
        ("Bed frame", "furniture", "light", 550),
        ("Nightstand", "furniture", "moderate", 150),
    ],
    "garage": [
        ("Lawn mower", "equipment", "severe", 450),
        ("Tool chest", "equipment", "moderate", 600),
        ("Water heater", "appliance", "severe", 1400),
    ],
    "basement": [
        ("Furnace", "appliance", "severe", 3200),
        ("Cardboard storage boxes", "storage", "destroyed", 120),
        ("Washer", "appliance", "moderate", 850),
    ],
    "bathroom": [
        ("Vanity cabinet", "millwork", "severe", 700),
        ("Mirror", "fixture", "light", 140),
        ("Towel set", "textile", "destroyed", 60),
    ],
}

_DEFAULT_ROOM = "living room"

_CONDITION_BY_DAMAGE = {
    "none": "Dry and undamaged",
    "light": "Minor water contact, likely salvageable",
    "moderate": "Saturated, may be repairable",
    "severe": "Substantial water damage, replacement likely",
    "destroyed": "Non-salvageable",
    "unknown": "Condition could not be determined",
}


def stub_investigation(
    file_path: Path, content_type: str, room_hint: str | None
) -> AgentResult:
    """Stable fake result keyed off the file name — mock mode only."""
    room = (room_hint or _DEFAULT_ROOM).lower().strip()
    if room not in _ROOM_LIBRARY:
        room = _guess_room_from_name(file_path.name) or _DEFAULT_ROOM

    seed = int(hashlib.sha256(file_path.name.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    library = _ROOM_LIBRARY[room]
    chosen = rng.sample(library, k=min(len(library), rng.randint(2, 4)))

    items: list[AgentItem] = []
    for index, (name, category, damage, value) in enumerate(chosen):
        items.append(
            AgentItem(
                name=name,
                category=category,
                room=room,
                description=f"{name} photographed in the {room} after flooding.",
                condition=_CONDITION_BY_DAMAGE[damage],
                damage_level=damage,
                confidence=round(rng.uniform(0.62, 0.94), 2),
                estimated_value=float(value),
                bbox={"x": 0.1, "y": min(0.1 + index * 0.2, 0.8), "w": 0.35, "h": 0.18},
                review_status="needs_review",
                agent_item_id=f"itm_{index + 1:03d}",
            )
        )

    return AgentResult(
        items=items,
        summary=(
            f"[SIMULATED] Stub investigation for the {room}: {len(items)} belongings "
            f"identified, estimated replacement cost "
            f"${sum(i.estimated_value or 0 for i in items):,.0f}."
        ),
        run_id=f"mock_{seed:08x}",
        status="completed",
        mode="mock",
        stub=True,
        evidence_transport="mock",
        raw={"note": "CREAO_MODE=mock — deterministic stub, no network call"},
    )


def _guess_room_from_name(filename: str) -> str | None:
    lowered = filename.lower().replace("-", " ").replace("_", " ")
    for room in _ROOM_LIBRARY:
        if room in lowered:
            return room
    return None
