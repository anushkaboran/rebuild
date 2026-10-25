"""Diagnostic: does our own signed public evidence URL actually serve the image?

The endpoint is admin-token protected (fail-closed), accepts only an upload id,
mints the signed URL server-side, and fetches it back through the configured
public origin — never returning, logging or accepting a URL.
"""

from __future__ import annotations

import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import Case, Upload, UploadKind
from app.store import store

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "8900000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)
TOKEN = "test-admin-token"


class Stub(BaseHTTPRequestHandler):
    """The 'public origin'. Behaviour is chosen by `Stub.mode`."""

    mode = "png"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        mode = Stub.mode
        if mode == "png":
            return self._send(200, PNG_BYTES, {"Content-Type": "image/png"})
        if mode == "not-png":
            return self._send(200, b"definitely not a png", {"Content-Type": "text/plain"})
        if mode == "403":
            return self._send(403, b"forbidden", {"Content-Type": "text/plain"})
        if mode == "500":
            return self._send(500, b"boom", {"Content-Type": "text/plain"})
        if mode == "slow":
            time.sleep(2)
            return self._send(200, PNG_BYTES, {"Content-Type": "image/png"})
        if mode == "redirect-away":
            return self._send(302, b"", {"Location": "http://evil.example/steal"})
        if mode == "redirect-same":
            self.send_response(302)
            self.send_header("Location", self.path.replace("/api/evidence/", "/second/"))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        return self._send(404, b"", {})


@pytest.fixture(scope="module")
def stub():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def reset(monkeypatch, stub):
    store.reset()
    Stub.mode = "png"
    port = stub.server_address[1]
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    monkeypatch.setattr(settings, "store_backend", "memory")
    monkeypatch.setattr(settings, "creao_mode", "mock")
    monkeypatch.setattr(settings, "admin_check_timeout_seconds", 0.5)
    # The public origin is the stub; the test flag permits http + loopback.
    monkeypatch.setattr(settings, "rebuild_public_base_url", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(settings, "creao_allow_private_evidence_hosts", True)
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _upload_with_file(suffix=".png", *, with_file=True) -> str:
    case_id = f"case_{uuid.uuid4().hex[:8]}"
    upload_id = f"upl_{uuid.uuid4().hex[:8]}"
    store.cases[case_id] = Case(id=case_id, title="check")
    store.uploads[upload_id] = Upload(
        id=upload_id,
        case_id=case_id,
        kind=UploadKind.photo,
        original_filename=f"room{suffix}",
        content_type="image/png",
        size_bytes=len(PNG_BYTES),
        sha256="x",
        evidence_id="ev_001",
        url=f"/api/uploads/{upload_id}/file",
        stored_path="",
    )
    if with_file:
        directory = settings.upload_dir / case_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{upload_id}{suffix}").write_bytes(PNG_BYTES)
    return upload_id


def _check(client, upload_id, token=TOKEN):
    headers = {"X-Admin-Token": token} if token is not None else {}
    return client.get(f"/api/admin/evidence/{upload_id}/check", headers=headers)


# --------------------------------------------------------------------------- #
# Authentication (fail closed)
# --------------------------------------------------------------------------- #
def test_valid_token_returns_diagnostics(client):
    upload_id = _upload_with_file()
    response = _check(client, upload_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["upload_id"] == upload_id
    assert body["http_status"] == 200
    assert body["content_type"] == "image/png"
    assert body["byte_count"] == len(PNG_BYTES)
    assert body["valid_png_signature"] is True
    assert "error_code" not in body


def test_missing_token_is_401(client):
    upload_id = _upload_with_file()
    response = _check(client, upload_id, token=None)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "ADMIN_TOKEN_INVALID"


def test_wrong_token_is_401(client):
    upload_id = _upload_with_file()
    response = _check(client, upload_id, token="nope")
    assert response.status_code == 401


def test_unset_admin_token_fails_closed_even_on_loopback(client, monkeypatch):
    """No loopback bypass: the server is exposed through ngrok."""
    monkeypatch.setattr(settings, "admin_token", "")
    upload_id = _upload_with_file()

    response = _check(client, upload_id, token=None)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ADMIN_TOKEN_NOT_CONFIGURED"

    # Even *with* an arbitrary token it stays closed.
    assert _check(client, upload_id, token="anything").status_code == 503


# --------------------------------------------------------------------------- #
# Upload / file resolution
# --------------------------------------------------------------------------- #
def test_missing_upload_is_404(client):
    response = _check(client, "upl_missing")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "UPLOAD_NOT_FOUND"


def test_missing_file_is_404(client):
    upload_id = _upload_with_file(with_file=False)
    response = _check(client, upload_id)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVIDENCE_FILE_MISSING"


def test_restored_upload_with_empty_stored_path_still_checks(client):
    """stored_path is not exported; resolution must find the file by id."""
    upload_id = _upload_with_file()
    assert store.uploads[upload_id].stored_path == ""
    assert _check(client, upload_id).status_code == 200


# --------------------------------------------------------------------------- #
# Upstream behaviour
# --------------------------------------------------------------------------- #
def test_non_png_bytes_report_false_signature(client):
    Stub.mode = "not-png"
    upload_id = _upload_with_file()
    body = _check(client, upload_id).json()

    assert body["http_status"] == 200
    assert body["valid_png_signature"] is False
    assert body["byte_count"] == len(b"definitely not a png")


def test_upstream_403_is_reported_not_raised(client):
    Stub.mode = "403"
    upload_id = _upload_with_file()
    body = _check(client, upload_id).json()

    assert body["http_status"] == 403
    assert body["error_code"] == "PUBLIC_URL_HTTP_ERROR"
    assert "403" in body["message"]
    assert body["valid_png_signature"] is False


def test_upstream_500_is_reported(client):
    Stub.mode = "500"
    upload_id = _upload_with_file()
    body = _check(client, upload_id).json()
    assert body["http_status"] == 500
    assert body["error_code"] == "PUBLIC_URL_HTTP_ERROR"


def test_timeout_is_reported(client):
    Stub.mode = "slow"
    upload_id = _upload_with_file()
    body = _check(client, upload_id).json()
    assert body["error_code"] == "FETCH_TIMEOUT"
    assert body["http_status"] is None


def test_redirect_to_another_host_is_rejected(client):
    Stub.mode = "redirect-away"
    upload_id = _upload_with_file()
    body = _check(client, upload_id).json()
    assert body["error_code"] == "REDIRECT_NOT_ALLOWED"
    assert body["byte_count"] == 0


# --------------------------------------------------------------------------- #
# No leakage, no side effects
# --------------------------------------------------------------------------- #
def test_response_contains_no_url_or_secret(client):
    upload_id = _upload_with_file()
    response = _check(client, upload_id)
    blob = response.text

    assert "sig=" not in blob
    assert "exp=" not in blob
    assert "http://" not in blob and "https://" not in blob
    assert TOKEN not in blob
    assert "authorization" not in blob.lower()


def test_endpoint_does_not_mutate_state(client):
    upload_id = _upload_with_file()
    before = store.uploads[upload_id].model_dump(mode="json")
    _check(client, upload_id)
    assert store.uploads[upload_id].model_dump(mode="json") == before


def test_no_creao_client_is_constructed(client, monkeypatch):
    calls: list[str] = []

    class Boom:
        def __init__(self, *a, **k):
            calls.append("client")

    monkeypatch.setattr("app.services.creao.CreaoAppClient", Boom)
    upload_id = _upload_with_file()
    assert _check(client, upload_id).status_code == 200
    assert calls == []
