"""Short-lived signed URLs for evidence the CREAO agent must fetch itself.

The app-scoped API Trigger takes **public HTTPS URLs** in `room_photos` /
`receipt_files` (mechanism confirmed by Person 2). CREAO downloads those files
from its own cloud, so:

  * the origin must be externally reachable (`REBUILD_PUBLIC_BASE_URL`), and
  * the URL must be temporary — private receipts must never live at a permanent
    public address.

Each URL therefore carries an expiry and an HMAC signature over the path, the
object id and the expiry. There is no listing route and no way to guess a link
for another case.

    /api/evidence/{upload_id}/public?exp=1730000000&sig=…
    /api/evidence/payloads/{investigation_id}/public?exp=…&sig=…

Set `REBUILD_EVIDENCE_SIGNING_SECRET` in production. Without it a random
per-process key is used, which is fine for a dev session but breaks links when
the server restarts.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

UPLOAD_PATH = "/api/evidence/{upload_id}/public"
PAYLOAD_PATH = "/api/evidence/payloads/{investigation_id}/public"
CLAIM_PDF_PATH = "/api/evidence/claims/{submission_id}/pdf/public"

# Content types CREAO's investigator can read.
_ALLOWED_TYPE_PREFIXES = ("image/",)
_ALLOWED_TYPES = {"application/pdf"}


class EvidenceUrlError(RuntimeError):
    """Evidence URLs cannot be built or are not externally reachable."""

    code = "CREAO_EVIDENCE_URLS_UNAVAILABLE"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


# --------------------------------------------------------------------------- #
# Signing
# --------------------------------------------------------------------------- #
def format_path(template: str, object_id: str) -> str:
    """Resolve a path template for a concrete id (used by sign *and* verify)."""
    return template.format(
        upload_id=object_id, investigation_id=object_id, submission_id=object_id
    )


def _sign(path: str, object_id: str, exp: int) -> str:
    message = f"{path}|{object_id}|{exp}".encode()
    return hmac.new(
        settings.evidence_signing_secret.encode(), message, hashlib.sha256
    ).hexdigest()


def verify_signature(template: str, object_id: str, exp: int, sig: str) -> tuple[bool, str]:
    if exp < int(time.time()):
        return False, "expired"
    if not hmac.compare_digest(_sign(format_path(template, object_id), object_id, exp), sig or ""):
        return False, "bad-signature"
    return True, "ok"


def _base() -> str:
    base = (settings.rebuild_public_base_url or "").rstrip("/")
    if not base:
        raise EvidenceUrlError(
            "REBUILD_PUBLIC_BASE_URL is not set, so uploaded photos have no "
            "address CREAO could fetch. Point it at a public origin (tunnel, "
            "deployed host, or a CDN/bucket in front of this API) or set "
            "CREAO_EVIDENCE_TRANSPORT=demo_url for the staged demo fixture. "
            "The backend will not send URLs it knows are unreachable."
        )
    return base


def _url(path_template: str, object_id: str) -> str:
    base = _base()
    exp = int(time.time()) + max(60, settings.rebuild_evidence_url_ttl_seconds)
    path = format_path(path_template, object_id)
    sig = _sign(path, object_id, exp)
    return f"{base}{path}?exp={exp}&sig={sig}"


def upload_url(upload_id: str) -> str:
    return _url(UPLOAD_PATH, upload_id)


def payload_url(investigation_id: str) -> str:
    return _url(PAYLOAD_PATH, investigation_id)


def claim_pdf_url(submission_id: str) -> str:
    """Signed URL for a generated recovery PDF (agent must fetch it over HTTPS)."""
    return _url(CLAIM_PDF_PATH, submission_id)


# --------------------------------------------------------------------------- #
# Pre-flight reachability check
# --------------------------------------------------------------------------- #
@dataclass
class UrlCheck:
    url: str
    ok: bool
    detail: str
    content_type: str | None = None


def is_public_host(url: str) -> tuple[bool, str]:
    """Reject hosts CREAO's cloud cannot reach (loopback, RFC1918, .local)."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False, f"unsupported scheme '{parsed.scheme}'"
    if parsed.scheme == "http" and not settings.creao_allow_private_evidence_hosts:
        return False, "must be https"

    host = parsed.hostname or ""
    if not host:
        return False, "missing host"
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False, "loopback/local hostname"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True, "public hostname"
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return False, f"private address {ip}"
    return True, "public address"


def expected_content_ok(content_type: str | None) -> bool:
    if not content_type:
        return False
    ctype = content_type.split(";")[0].strip().lower()
    return ctype.startswith(_ALLOWED_TYPE_PREFIXES) or ctype in _ALLOWED_TYPES


async def check_urls(urls: list[str], *, timeout: float = 20.0) -> list[UrlCheck]:
    """Fetch every URL and confirm CREAO would receive a usable file.

    This is what stops a run from being created with links that only resolve on
    the developer's machine.
    """
    checks: list[UrlCheck] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for url in urls:
            public, why = is_public_host(url)
            if not public:
                checks.append(UrlCheck(url=url, ok=False, detail=why))
                continue
            try:
                response = await client.get(url, headers={"Range": "bytes=0-1023"})
            except Exception as exc:  # noqa: BLE001 - report, do not raise
                checks.append(UrlCheck(url=url, ok=False, detail=f"unreachable: {exc}"))
                continue

            ctype = response.headers.get("content-type")
            if response.status_code not in {200, 206}:
                checks.append(
                    UrlCheck(url=url, ok=False, detail=f"HTTP {response.status_code}", content_type=ctype)
                )
            elif not expected_content_ok(ctype):
                checks.append(
                    UrlCheck(
                        url=url,
                        ok=False,
                        detail=f"unexpected content-type '{ctype}'",
                        content_type=ctype,
                    )
                )
            elif not response.content:
                checks.append(
                    UrlCheck(url=url, ok=False, detail="empty body", content_type=ctype)
                )
            else:
                checks.append(
                    UrlCheck(url=url, ok=True, detail="ok", content_type=ctype)
                )
    return checks


def summarise_failures(checks: list[UrlCheck]) -> str:
    return "; ".join(
        f"{c.url.split('?')[0]} → {c.detail}" for c in checks if not c.ok
    )
