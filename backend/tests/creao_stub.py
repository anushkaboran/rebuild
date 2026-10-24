"""Local stand-in for the app-scoped CREAO API Trigger, for wire-level tests.

Implements only what Person 2's captured examples prove:

  POST /api/v1/apps/{appId}/runs              {"inputs": {...}} -> create response
  GET  /api/v1/apps/{appId}/runs/{runId}      -> poll response (pending → completed)
  GET  /api/v1/apps/{appId}/runs              -> list response
  GET  /api/v1/runs/{runId}/artifacts/download?artifactId=…  -> rebuild-result.json
                                                 (authenticated API route)

It records exactly what the real client sent so the test can assert the wire
contract: body shape, absence of agent_id, Bearer on every API call, and whether
the API key leaked to a non-API artifact host.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_ID = "9819d9a5-89e0-4d62-a284-91bac67f8cd1"
RUN_ID = "f5ddc714-e0d0-49fa-b2af-df49a1c4a814"
THREAD_ID = "e9066165-4272-4b16-9970-059784024cde"

FIXTURES = Path(__file__).parent / "fixtures"
RESULT = json.loads((FIXTURES / "api-run-result.json").read_text())

BLOB_LOG: list[dict] = []
BLOB_PORT = 0

LOG: list[dict] = []
_lock = threading.Lock()
_state = {"polls": 0, "evidence_ids": []}


def record(entry: dict) -> None:
    with _lock:
        LOG.append(entry)


def events() -> list[dict]:
    with _lock:
        return list(LOG)


def reset() -> None:
    with _lock:
        LOG.clear()
    _state["polls"] = 0
    _state["evidence_ids"] = []


def _result_for_sent_evidence() -> dict:
    """Report only the evidence the caller actually declared (like the agent)."""
    ids = set(_state["evidence_ids"]) or {e["evidence_id"] for e in RESULT["evidence"]}
    payload = json.loads(json.dumps(RESULT))
    payload["evidence"] = [e for e in payload["evidence"] if e["evidence_id"] in ids]
    payload["items"] = [
        i for i in payload["items"] if set(i.get("supporting_evidence_ids") or []) <= ids
    ]
    payload["findings"] = [
        f for f in payload["findings"] if set(f.get("evidence_ids") or []) <= ids
    ]
    payload["questions"] = []
    payload["receipt_extracts"] = [
        r for r in payload["receipt_extracts"] if r["evidence_id"] in ids
    ]
    return payload


def make_server(port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def make_blob_server(port: int = 0) -> ThreadingHTTPServer:
    """Separate host standing in for the presigned S3 bucket."""
    global BLOB_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), BlobHandler)
    BLOB_PORT = server.server_address[1]
    return server


class BlobHandler(BaseHTTPRequestHandler):
    """Serves the artifact like presigned storage: no Authorization expected."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        with _lock:
            BLOB_LOG.append(
                {
                    "path": self.path,
                    "sent_authorization": self.headers.get("Authorization") is not None,
                }
            )
        body = json.dumps(_result_for_sent_evidence()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # -- helpers ----------------------------------------------------------- #
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _authed(self) -> bool:
        return str(self.headers.get("Authorization", "")).startswith("Bearer ")

    # -- routes ------------------------------------------------------------ #
    def do_GET(self):
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)

        # Artifact download: the captured URL is an authenticated API route.
        if re.fullmatch(r"/api/v1/runs/[^/]+/artifacts/download", path):
            record({
                "route": "GET artifact",
                "artifactId": (query.get("artifactId") or [None])[0],
                "sent_authorization": self._authed(),
                "host": f"{self.headers.get('Host')}",
            })
            if not self._authed():
                return self._send(401, {"error": "AUTHENTICATION_REQUIRED"})
            # Real behaviour: 302 to presigned storage.
            location = f"http://127.0.0.1:{BLOB_PORT}/blob/rebuild-result.json?sig=stub"
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if path == f"/api/v1/apps/{APP_ID}/runs":
            record({"route": "GET list-runs", "sent_authorization": self._authed()})
            return self._send(200, {"runs": [], "limit": 1, "offset": 0})

        if path == f"/api/v1/apps/{APP_ID}/runs/{RUN_ID}":
            _state["polls"] += 1
            record({"route": "GET poll", "poll": _state["polls"], "sent_authorization": self._authed()})
            if _state["polls"] < 2:
                return self._send(200, {
                    "id": RUN_ID, "status": "running", "threadId": THREAD_ID,
                    "result": None, "errorCode": None,
                })
            artifact_url = (
                f"http://{self.headers.get('Host')}"
                f"/api/v1/runs/{RUN_ID}/artifacts/download"
                "?artifactId=workspaces%2Fws%2Fthread%2Ffiles%2Frebuild-result.json"
            )
            return self._send(200, {
                "id": RUN_ID,
                "status": "completed",
                "threadId": THREAD_ID,
                "result": {
                    "text": json.dumps({"status": "success", "summary": "Living room inventory validated."}),
                    "chatText": "I'll start by reading the app manifest.",
                    "artifacts": [
                        {
                            "id": "workspaces/ws/thread/files/rebuild-result.json",
                            "url": artifact_url,
                            "type": "application/json",
                            "title": "rebuild-result.json",
                        }
                    ],
                },
                "errorCode": None,
                "startedAt": "2026-10-24T05:09:27.558Z",
                "completedAt": "2026-10-24T05:11:33.777Z",
                "createdAt": "2026-10-24T05:09:27.530Z",
            })

        return self._send(404, {"error": "RUN_NOT_FOUND"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == f"/api/v1/apps/{APP_ID}/runs":
            raw = self._body()
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "INVALID_INPUT"})
            record({
                "route": "POST create-run",
                "sent_authorization": self._authed(),
                "body_keys": sorted(payload.keys()),
                "has_agent_id": "agent_id" in payload or "agentId" in payload,
                "inputs_keys": sorted((payload.get("inputs") or {}).keys()),
                "inputs": payload.get("inputs"),
            })
            _state["polls"] = 0
            try:
                manifest = json.loads((payload.get("inputs") or {}).get("evidence_manifest") or "[]")
                _state["evidence_ids"] = [m["evidence_id"] for m in manifest if "evidence_id" in m]
            except (json.JSONDecodeError, TypeError):
                _state["evidence_ids"] = []
            return self._send(200, {
                "id": RUN_ID,
                "status": "pending",
                "appId": APP_ID,
                "appName": "Rebuild Evidence Investigator",
            })

        return self._send(404, {"error": "NOT_FOUND"})
