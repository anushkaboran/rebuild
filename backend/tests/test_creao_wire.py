"""Wire-level test: real httpx over TCP against the documented app-scoped shapes.

Unlike tests/test_creao_live.py (which replaces the client), this exercises the
actual HTTP the backend produces:

  * POST body is exactly {"inputs": {...}} with **no** agent_id
  * every API call carries `Authorization: Bearer`
  * polling hits /apps/{appId}/runs/{runId} until a terminal status
  * the artifact is fetched from the authenticated API route
  * a non-API (presigned) artifact host never receives the key
"""

from __future__ import annotations

import io
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import creao
from app.store import store
from tests import creao_stub

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


@pytest.fixture(scope="module")
def stub_server():
    server = creao_stub.make_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def blob_server(stub_server):
    server = creao_stub.make_blob_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def wire_client(stub_server, blob_server, monkeypatch):
    store.reset()
    creao_stub.reset()
    creao_stub.BLOB_LOG.clear()
    port = stub_server.server_address[1]

    monkeypatch.setattr(creao.settings, "creao_mode", "live")
    monkeypatch.setattr(creao.settings, "creao_app_api_key", "capi_wire_test")
    monkeypatch.setattr(creao.settings, "creao_app_id", creao_stub.APP_ID)
    monkeypatch.setattr(creao.settings, "creao_app_base_url", f"http://127.0.0.1:{port}/api/v1")
    monkeypatch.setattr(creao.settings, "creao_evidence_transport", "url")
    monkeypatch.setattr(creao.settings, "rebuild_public_base_url", "https://evidence.example.com")
    monkeypatch.setattr(creao.settings, "creao_verify_evidence_urls", False)
    monkeypatch.setattr(creao.settings, "creao_poll_interval_seconds", 0.01)

    with TestClient(app) as client:
        yield client
    store.reset()


DEMO_UPLOADS = (
    ("01_room_armchair.jpg", "photo"),
    ("02_armchair_closeup.jpg", "photo"),
    ("04_receipt_lamp.png", "receipt"),
    ("05_receipt_groceries.png", "receipt"),
)


def _investigate(client):
    """Mirror Person 2's demo: two room photos plus two receipts."""
    case_id = client.post("/api/cases", json={"title": "Wire test"}).json()["id"]
    upload_ids = []
    for name, kind in DEMO_UPLOADS:
        upload_ids.append(
            client.post(
                f"/api/cases/{case_id}/uploads",
                files={"file": (name, io.BytesIO(PNG_BYTES), "image/jpeg")},
                data={"kind": kind, "uploaded_by": "priya"},
            ).json()["id"]
        )
    upload = {"id": upload_ids[0]}
    started = client.post(
        f"/api/cases/{case_id}/investigations",
        json={"upload_id": upload_ids[0], "upload_ids": upload_ids, "room_hint": "living room"},
    )
    assert started.status_code == 202, started.text
    detail = client.get(f"/api/investigations/{started.json()['id']}").json()
    return case_id, upload, detail


def test_full_run_over_the_wire(wire_client):
    case_id, upload, investigation = _investigate(wire_client)

    assert investigation["status"] == "complete", investigation
    assert investigation["agent_run_id"] == creao_stub.RUN_ID
    assert investigation["conversation_id"] == creao_stub.THREAD_ID
    assert investigation["evidence_transport"] == "url"

    items = wire_client.get(f"/api/cases/{case_id}/items").json()
    assert len(items) == 8
    assert items[0]["agent_item_id"] == "itm_001"
    assert items[0]["supporting_evidence_ids"] == ["ev_001", "ev_002"]

    log = {e["route"]: e for e in creao_stub.events()}

    create = log["POST create-run"]
    assert create["body_keys"] == ["inputs"], "app-scoped body is {inputs} only"
    assert create["has_agent_id"] is False, "agent_id must not be sent on this route"
    assert create["sent_authorization"] is True
    assert set(create["inputs_keys"]) == {
        "case_description",
        "room_name",
        "room_photos",
        "receipt_files",
        "evidence_manifest",
        "confirmed_items",
    }, "first run must not include previous_result"
    assert create["inputs"]["room_name"] == "living room"
    assert create["inputs"]["case_description"].startswith("Post-flood renter claim")

    # Evidence URLs are signed links this backend serves, not local paths.
    photos = create["inputs"]["room_photos"]
    receipts = create["inputs"]["receipt_files"]
    assert len(photos) == 2 and len(receipts) == 2, "classified by upload kind"
    for url in photos + receipts:
        assert url.startswith("https://evidence.example.com/api/evidence/")
        assert "exp=" in url and "sig=" in url
    manifest = json.loads(create["inputs"]["evidence_manifest"])
    assert [m["evidence_id"] for m in manifest] == ["ev_001", "ev_002", "ev_003", "ev_004"]

    # Manifest and confirmed items are JSON-encoded strings, not arrays.
    assert isinstance(create["inputs"]["evidence_manifest"], str)
    assert isinstance(create["inputs"]["confirmed_items"], str)

    poll = log["GET poll"]
    assert poll["poll"] >= 2, "must poll until terminal"
    assert poll["sent_authorization"] is True

    artifact = log["GET artifact"]
    assert artifact["sent_authorization"] is True, "artifact API route requires the key"
    assert "rebuild-result.json" in (artifact["artifactId"] or "")

    # The 302 to storage must be followed, and must NOT carry the API key.
    assert creao_stub.BLOB_LOG, "artifact redirect was not followed"
    assert creao_stub.BLOB_LOG[0]["sent_authorization"] is False, (
        "API key must never be forwarded to the presigned storage host"
    )


def test_validate_over_the_wire(wire_client):
    body = wire_client.post("/api/creao/validate").json()

    assert body["ok"] is True
    assert body["app_id"] == creao_stub.APP_ID
    routes = [e["route"] for e in creao_stub.events()]
    assert routes == ["GET list-runs"]


def test_artifact_download_scopes_auth_by_host(monkeypatch):
    """Same-host artifact URL gets the key; foreign host does not."""
    import asyncio

    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(creao.settings, "creao_app_api_key", "capi_test")
    monkeypatch.setattr(creao.settings, "creao_app_base_url", "https://agent.creao.ai/api/v1")

    class FakeSameHostResponse:
        status_code = 200
        content = b'{"ok":true}'
        text = "{}"
        is_redirect = False
        headers: dict = {}

    class FakeAsyncClient:
        def __init__(self, *a, **kw):
            self._has_auth = "Authorization" in (kw.get("headers") or {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def get(self, url, **kw):
            seen.append((url, self._has_auth))
            return FakeSameHostResponse()

        async def request(self, *a, **kw):
            return FakeSameHostResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(creao.httpx, "AsyncClient", FakeAsyncClient)
    client_obj = creao.CreaoAppClient()
    try:
        asyncio.run(client_obj.download_artifact("https://agent.creao.ai/api/v1/runs/x/artifacts/download"))
        asyncio.run(client_obj.download_artifact("https://s3.amazonaws.com/bucket/rebuild-result.json"))
    finally:
        asyncio.run(client_obj.__aexit__(None, None, None))

    assert seen[0] == ("https://agent.creao.ai/api/v1/runs/x/artifacts/download", True)
    assert seen[1] == ("https://s3.amazonaws.com/bucket/rebuild-result.json", False)
