"""CREAO preflight — verify the app-scoped API Trigger without spending credits.

Usage:
    cd backend
    CREAO_MODE=live python scripts/creao_preflight.py

Reads .env / environment (CREAO_MODE, CREAO_APP_ID, CREAO_APP_API_KEY,
CREAO_EVIDENCE_TRANSPORT). Prints the integration state and, in live mode,
probes the documented read-only route `GET /apps/{appId}/runs` to confirm the
capi_ key and app id work.

Never prints the API key — only its documented prefix (capi_ / cr_sk_).
No run is started, so no credits are consumed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.services import creao  # noqa: E402


def main() -> int:
    status = creao.configuration_status()

    print("CREAO configuration")
    print("  mode ...............", status["mode"])
    print("  transport ..........", status["transport"])
    print("  base_url ...........", status["base_url"])
    print("  app_id .............", status["app_id"])
    print("  api key ............", status["api_key_prefix"] or "not set")
    print("  evidence transport .", status["evidence_transport"])
    print("  evidence ready .....", "yes" if status["evidence_ready"] else "NO")
    print("  missing ............", ", ".join(status["missing"]) or "none")

    try:
        creao.ensure_ready()
    except creao.CreaoConfigError as exc:
        print(f"\n✗ {exc.code}: {exc.message}")
        return 2

    if creao.is_mock():
        print("\n✓ Mock mode is ready. Investigations return SIMULATED data only.")
        print("  Set CREAO_MODE=live (plus a capi_ key) for real runs.")
        return 0

    print("\nContacting CREAO (read-only: GET /apps/{appId}/runs)…")
    try:
        report = asyncio.run(creao.validate_live_credentials())
    except creao.CreaoAuthError as exc:
        print(f"\n✗ {exc.code}: {exc.message}")
        return 3
    except creao.CreaoError as exc:
        print(f"\n✗ {exc.code}: {exc.message}")
        return 4

    print("  key accepted ....... yes")
    print("  app reachable ...... yes")
    print("  recent runs visible .", report["recent_runs_visible"])

    transport = settings.creao_evidence_transport
    if transport == "unset":
        print(
            "\n⚠ The API works, but CREAO_EVIDENCE_TRANSPORT is unset.\n"
            "  A live investigation would start a run with no photographs attached,\n"
            "  so the backend refuses with 503 CREAO_EVIDENCE_TRANSPORT_UNVERIFIED.\n"
            "  Set it to 'pre_attached' (files already in the agent's file space) or\n"
            "  'data_url' (inline base64, still unverified) once demonstrated."
        )
        return 5

    print(f"\n✓ Live mode is ready (evidence transport: {transport}).")
    print("  POST /api/cases/{id}/investigations will use the real agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
