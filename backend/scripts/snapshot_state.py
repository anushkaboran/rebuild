#!/usr/bin/env python3
"""Snapshot a **running** Rebuild backend using only endpoints it already has.

The point: a server started before this code existed cannot serve
`/api/admin/export`, so this script reconstructs the same snapshot document from
the older read endpoints. Every optional call is probed; anything unavailable is
reported in `warnings` instead of being silently dropped.

Usage:
    python scripts/snapshot_state.py --base-url http://127.0.0.1:8000 \
        --out rebuild-snapshot.json
    python scripts/snapshot_state.py --verify rebuild-snapshot.json

No secrets are written: signed evidence URLs are stripped and only the local
`/api/uploads/{id}/file` reference is kept.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

SNAPSHOT_VERSION = 1

# Fields the API never returns; kept out of the snapshot explicitly.
EXCLUDED = {
    "uploads": ["stored_path"],
    "items": ["agent_record"],
    "investigations": ["result_payload"],
    "research_runs": ["input_snapshot"],
}


class Exporter:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base, timeout=timeout)
        self.warnings: list[str] = []
        self.unavailable: set[str] = set()

    # -- helpers ---------------------------------------------------------- #
    def get(self, path: str, required: bool = False):
        try:
            response = self.client.get(path)
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"{path}: request failed ({exc})")
            if required:
                raise
            return None

        if response.status_code == 404:
            self.unavailable.add(path)
            if required:
                raise SystemExit(f"required endpoint missing: {path}")
            return None
        if response.status_code >= 400:
            self.warnings.append(f"{path}: HTTP {response.status_code}")
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            self.warnings.append(f"{path}: non-JSON response")
            return None

    @staticmethod
    def scrub(record: dict, collection: str) -> dict:
        for field in EXCLUDED.get(collection, []):
            record.pop(field, None)
        if collection == "uploads":
            url = record.get("url") or ""
            if "sig=" in url or "exp=" in url or urlparse(url).scheme in {"http", "https"}:
                record["url"] = f"/api/uploads/{record.get('id')}/file"
        return record

    # -- collections ------------------------------------------------------ #
    def run(self) -> dict:
        health = self.get("/api/health") or {}
        cases = self.get("/api/cases", required=True)
        if cases is None:
            raise SystemExit("could not list cases; is the server running?")

        snapshot: dict = {
            "snapshot_version": SNAPSHOT_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "base_url": self.base,
                "server_version": health.get("version"),
                "store_backend": (health.get("integrations") or {})
                .get("spacetimedb_db", {})
                .get("backend"),
                "captured_via": "legacy read endpoints",
            },
            "cases": [], "uploads": [], "investigations": [], "items": [],
            "revisions": [], "comments": [], "findings": [], "questions": [],
            "decision_events": [], "research_runs": [], "research_candidates": [],
            "warnings": [],
        }

        seen_research: set[str] = set()

        for case in cases:
            case_id = case["id"]
            detail = self.get(f"/api/cases/{case_id}") or {}
            snapshot["cases"].append(detail.get("case", case) or case)

            uploads = detail.get("uploads") or self.get(f"/api/cases/{case_id}/uploads") or []
            items = detail.get("items") or self.get(f"/api/cases/{case_id}/items") or []
            investigations = self.get(f"/api/cases/{case_id}/investigations") or []
            comments = self.get(f"/api/cases/{case_id}/comments") or []
            review = self.get(f"/api/cases/{case_id}/review") or {}

            snapshot["uploads"] += [self.scrub(u, "uploads") for u in uploads]
            snapshot["items"] += [self.scrub(i, "items") for i in items]
            snapshot["investigations"] += [
                self.scrub(i, "investigations") for i in investigations
            ]
            snapshot["comments"] += comments

            # Findings/questions/decisions only exist on servers with the review API.
            for key in ("findings", "questions"):
                snapshot[key] += review.get(key) or []
            for key in ("question_decisions", "finding_decisions"):
                snapshot["decision_events"] += review.get(key) or []
            for decision in review.get("decisions") or []:
                snapshot["decision_events"].append(
                    {
                        "id": f"item:{decision.get('item_id')}:{decision.get('created_at')}",
                        "case_id": case_id,
                        "target_type": "item",
                        "target_id": decision.get("item_id"),
                        "agent_ref": decision.get("agent_item_id"),
                        "revision": 0,
                        "value": None,
                        "reviewer": decision.get("editor") or "unknown",
                        "note": decision.get("note"),
                        "created_at": decision.get("created_at"),
                    }
                )

            # Revision history: one call per item.
            for item in items:
                revisions = self.get(f"/api/items/{item['id']}/revisions") or []
                snapshot["revisions"] += revisions

            # Research: canonical route first, case-scoped fallback.
            for item in items:
                runs = self.get(f"/api/items/{item['id']}/research")
                if runs is None:
                    runs = self.get(f"/api/cases/{case_id}/items/{item['id']}/research") or []
                for run in runs or []:
                    if run["id"] in seen_research:
                        continue
                    seen_research.add(run["id"])
                    snapshot["research_runs"].append(self.scrub(run, "research_runs"))
                    per_run = self.get(f"/api/research/{run['id']}")
                    candidates = (per_run or {}).get("candidates")
                    if candidates is None:
                        payload = self.get(
                            f"/api/cases/{case_id}/research/{run['id']}/candidates"
                        )
                        candidates = (payload or {}).get("candidates")
                    snapshot["research_candidates"] += candidates or []

        snapshot["counts"] = {
            key: len(snapshot[key])
            for key in snapshot
            if isinstance(snapshot[key], list) and key not in {"warnings"}
        }
        snapshot["unavailable_endpoints"] = sorted(self.unavailable)
        snapshot["warnings"] = self.warnings + [
            "investigation.result_payload is not exposed by any API version and is absent.",
            "EvidenceItem.agent_record is not exposed and is absent.",
            "Upload.stored_path is not exposed — copy backend/uploads/ separately if the "
            "evidence files are still needed.",
            "ResearchRun.input_snapshot (signed URLs) is intentionally excluded.",
        ]
        return snapshot


def verify(path: str) -> int:
    data = json.load(open(path))
    problems: list[str] = []

    case_ids = {c["id"] for c in data.get("cases", [])}
    item_ids = {i["id"] for i in data.get("items", [])}
    upload_ids = {u["id"] for u in data.get("uploads", [])}
    run_ids = {r["id"] for r in data.get("research_runs", [])}

    for item in data.get("items", []):
        if item["case_id"] not in case_ids:
            problems.append(f"item {item['id']} → unknown case")
        if item.get("upload_id") and item["upload_id"] not in upload_ids:
            problems.append(f"item {item['id']} → unknown upload")
    for run in data.get("research_runs", []):
        if run["item_id"] not in item_ids:
            problems.append(f"research {run['id']} → unknown item")
    for candidate in data.get("research_candidates", []):
        if candidate["run_id"] not in run_ids:
            problems.append(f"candidate {candidate['id']} → unknown research run")

    print(json.dumps({
        "file": path,
        "counts": data.get("counts"),
        "case_ids": sorted(case_ids),
        "item_ids": sorted(item_ids),
        "research": {r["id"]: r.get("agent_run_id") for r in data.get("research_runs", [])},
        "problems": problems,
        "warnings": data.get("warnings", []),
    }, indent=2))
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", default="rebuild-snapshot.json")
    parser.add_argument("--verify", metavar="FILE", help="validate an existing snapshot")
    args = parser.parse_args()

    if args.verify:
        return verify(args.verify)

    snapshot = Exporter(args.base_url).run()
    with open(args.out, "w") as handle:
        json.dump(snapshot, handle, indent=2)
    print(json.dumps({
        "written": args.out,
        "counts": snapshot["counts"],
        "unavailable_endpoints": sorted(snapshot.get("unavailable_endpoints", [])),
        "warnings": snapshot["warnings"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
