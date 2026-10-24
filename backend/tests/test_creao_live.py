"""App-scoped API Trigger with URL-based evidence (Person 2's confirmed mechanism).

Fixtures come from Person 2's captured runs (`tests/fixtures/`). No real CREAO
call is made: the client is replaced by a fake, and URL reachability is either
disabled or pointed at an intentionally unreachable host.

Marked `mocked` throughout — see tests/test_creao_live_network.py for the
optional test that actually fetches the staged demo URLs.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import creao, evidence_urls
from app.store import store

FIXTURES = Path(__file__).parent / "fixtures"
CREATE_RESPONSE = json.loads((FIXTURES / "api-create-response.json").read_text())
POLL_RESPONSE = json.loads((FIXTURES / "api-poll-response.json").read_text())
RUN_RESULT = json.loads((FIXTURES / "api-run-result.json").read_text())

APP_ID = "9819d9a5-89e0-4d62-a284-91bac67f8cd1"

# Person 2's documented demo evidence (staged files on the agent branch).
DEMO_FILES = {
    "01_room_armchair.jpg": "photo",
    "02_armchair_closeup.jpg": "photo",
    "04_receipt_lamp.png": "receipt",
    "05_receipt_groceries.png": "receipt",
}

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


class FakeCreaoAppClient:
    instances: list["FakeCreaoAppClient"] = []
    artifact_payload: dict = RUN_RESULT
    artifact_error: bool = False
    result_missing_keys: bool = False
    unknown_evidence_id: bool = False
    run_status: str = "completed"
    error_code: str | None = None
    poll_sequence: list[str] = ["pending", "running", "completed"]

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._poll_index = 0
        FakeCreaoAppClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def create_run(self, inputs):
        self.calls.append(("create_run", {"inputs": inputs}))
        return dict(CREATE_RESPONSE)

    async def list_runs(self, limit=1):
        self.calls.append(("list_runs", {"limit": limit}))
        return {"runs": [], "limit": limit, "offset": 0}

    async def get_run(self, run_id):
        status = FakeCreaoAppClient.poll_sequence[
            min(self._poll_index, len(FakeCreaoAppClient.poll_sequence) - 1)
        ]
        self._poll_index += 1
        run = dict(POLL_RESPONSE)
        run["id"] = run_id
        run["status"] = FakeCreaoAppClient.run_status if status == "completed" else status
        if FakeCreaoAppClient.run_status == "failed":
            run["status"] = "failed"
            run["errorCode"] = FakeCreaoAppClient.error_code or "AGENT_ERROR"
        return run

    async def poll_run(self, run_id):
        while True:
            run = await self.get_run(run_id)
            if run["status"] in {"completed", "succeeded"}:
                return run
            if run["status"] in {"failed", "cancelled"}:
                raise creao.CreaoRunFailed(
                    f"run ended {run['status']}", code=str(run.get("errorCode"))
                )

    async def download_artifact(self, url):
        self.calls.append(("download_artifact", {"url": url}))
        if FakeCreaoAppClient.artifact_error:
            raise creao.CreaoError("artifact 403", code="CREAO_ARTIFACT_DOWNLOAD_FAILED")
        payload = dict(FakeCreaoAppClient.artifact_payload)
        if FakeCreaoAppClient.result_missing_keys:
            payload.pop("questions")
        if FakeCreaoAppClient.unknown_evidence_id:
            payload = json.loads(json.dumps(payload))
            payload["evidence"][0]["evidence_id"] = "ev_999"
        return json.dumps(payload).encode()




def payload_for(ids) -> dict:
    """Trim Person 2's captured result to the evidence IDs a test actually sent.

    The real agent only reports evidence it was given, so a single-upload test
    must use a matching payload — otherwise the evidence-ID validator (correctly)
    rejects it.
    """
    payload = json.loads(json.dumps(RUN_RESULT))
    keep = set(ids)
    payload["evidence"] = [e for e in payload["evidence"] if e["evidence_id"] in keep]
    payload["items"] = [
        i for i in payload["items"] if set(i.get("supporting_evidence_ids") or []) <= keep
    ]
    payload["findings"] = [
        f for f in payload["findings"] if set(f.get("evidence_ids") or []) <= keep
    ]
    payload["questions"] = []
    payload["receipt_extracts"] = [
        r for r in payload["receipt_extracts"] if r["evidence_id"] in keep
    ]
    return payload


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    store.reset()
    FakeCreaoAppClient.instances = []
    FakeCreaoAppClient.artifact_payload = RUN_RESULT
    FakeCreaoAppClient.artifact_error = False
    FakeCreaoAppClient.result_missing_keys = False
    FakeCreaoAppClient.unknown_evidence_id = False
    FakeCreaoAppClient.run_status = "completed"
    FakeCreaoAppClient.error_code = None
    FakeCreaoAppClient.poll_sequence = ["pending", "running", "completed"]

    monkeypatch.setattr(creao, "CreaoAppClient", FakeCreaoAppClient)
    monkeypatch.setattr(creao.settings, "creao_mode", "mock")
    monkeypatch.setattr(creao.settings, "creao_app_api_key", "capi_test")
    monkeypatch.setattr(creao.settings, "creao_app_id", APP_ID)
    monkeypatch.setattr(creao.settings, "creao_app_base_url", "https://agent.creao.ai/api/v1")
    monkeypatch.setattr(creao.settings, "creao_evidence_transport", "unset")
    monkeypatch.setattr(creao.settings, "rebuild_public_base_url", "")
    monkeypatch.setattr(creao.settings, "rebuild_evidence_url_ttl_seconds", 900)
    monkeypatch.setattr(creao.settings, "creao_poll_interval_seconds", 0.01)
    # Tests never hit the network for URL checks unless a test enables it.
    monkeypatch.setattr(creao.settings, "creao_verify_evidence_urls", False)
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _live(monkeypatch, *, transport="url", key="capi_test_key", base="https://evidence.example.com", verify=False):
    monkeypatch.setattr(creao.settings, "creao_mode", "live")
    monkeypatch.setattr(creao.settings, "creao_app_api_key", key)
    monkeypatch.setattr(creao.settings, "creao_evidence_transport", transport)
    monkeypatch.setattr(creao.settings, "rebuild_public_base_url", base)
    monkeypatch.setattr(creao.settings, "creao_verify_evidence_urls", verify)


def _case_with_uploads(client, files=("01_room_armchair.jpg",)):
    case_id = client.post("/api/cases", json={"title": "42 Willow Creek Rd"}).json()["id"]
    upload_ids = []
    for name in files:
        body = client.post(
            f"/api/cases/{case_id}/uploads",
            files={"file": (name, io.BytesIO(PNG_BYTES), "image/jpeg")},
            data={"kind": DEMO_FILES.get(name, "photo")},
        ).json()
        upload_ids.append(body["id"])
    return case_id, upload_ids


# --------------------------------------------------------------------------- #
# 1. Input construction — the exact structure Person 2 confirmed
# --------------------------------------------------------------------------- #
def test_inputs_match_person2_confirmed_structure():
    """Byte-for-byte the documented demo request (modulo URL host)."""
    evidence = [
        creao.EvidenceFile(
            evidence_id=f"ev_{i:03d}",
            filename=name,
            content_type="image/jpeg",
            kind=kind,
            url=f"{creao.settings.creao_demo_evidence_base_url}{name}",
        )
        for i, (name, kind) in enumerate(DEMO_FILES.items(), start=1)
    ]

    inputs = creao.build_inputs(
        case_description="Living room flooded; documenting damaged belongings for recovery.",
        room_name="Living room",
        evidence=evidence,
        confirmed_items=[],
        transport="demo_url",
    )

    assert inputs["room_photos"] == [
        f"{creao.settings.creao_demo_evidence_base_url}01_room_armchair.jpg",
        f"{creao.settings.creao_demo_evidence_base_url}02_armchair_closeup.jpg",
    ]
    assert inputs["receipt_files"] == [
        f"{creao.settings.creao_demo_evidence_base_url}04_receipt_lamp.png",
        f"{creao.settings.creao_demo_evidence_base_url}05_receipt_groceries.png",
    ]
    # JSON strings, not arrays — this is the detail that breaks the agent.
    assert isinstance(inputs["evidence_manifest"], str)
    assert isinstance(inputs["confirmed_items"], str)
    assert json.loads(inputs["confirmed_items"]) == []
    manifest = json.loads(inputs["evidence_manifest"])
    assert [m["evidence_id"] for m in manifest] == ["ev_001", "ev_002", "ev_003", "ev_004"]
    assert [m["filename"] for m in manifest] == list(DEMO_FILES)
    assert "previous_result" not in inputs
    assert "agent_id" not in inputs and "input" not in inputs


def test_manifest_and_confirmed_items_are_json_strings(client, monkeypatch):
    _live(monkeypatch)
    case_id, upload_ids = _case_with_uploads(client)

    started = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    )
    client.get(f"/api/investigations/{started.json()['id']}")

    inputs = dict(FakeCreaoAppClient.instances[-1].calls)["create_run"]["inputs"]
    assert isinstance(inputs["evidence_manifest"], str)
    assert isinstance(inputs["confirmed_items"], str)
    assert isinstance(json.loads(inputs["evidence_manifest"]), list)


def test_photos_and_receipts_split_by_upload_kind(client, monkeypatch):
    _live(monkeypatch)
    case_id, upload_ids = _case_with_uploads(
        client, ("01_room_armchair.jpg", "02_armchair_closeup.jpg", "04_receipt_lamp.png")
    )

    started = client.post(
        f"/api/cases/{case_id}/investigations",
        json={"upload_id": upload_ids[0], "upload_ids": upload_ids},
    )
    assert started.status_code == 202, started.text
    client.get(f"/api/investigations/{started.json()['id']}")

    inputs = dict(FakeCreaoAppClient.instances[-1].calls)["create_run"]["inputs"]
    assert len(inputs["room_photos"]) == 2, "two uploads were declared as photos"
    assert len(inputs["receipt_files"]) == 1, "one upload was declared as a receipt"
    assert all(u.startswith("https://evidence.example.com/api/evidence/") for u in inputs["room_photos"])

    manifest = json.loads(inputs["evidence_manifest"])
    assert [m["evidence_id"] for m in manifest] == ["ev_001", "ev_002", "ev_003"]


def test_evidence_ids_are_stable_and_sequential(client, monkeypatch):
    _live(monkeypatch)
    case_id, upload_ids = _case_with_uploads(client, ("01_room_armchair.jpg", "02_armchair_closeup.jpg"))

    started = client.post(
        f"/api/cases/{case_id}/investigations",
        json={"upload_id": upload_ids[0], "upload_ids": upload_ids},
    )
    investigation = client.get(f"/api/investigations/{started.json()['id']}").json()
    assert investigation["evidence_ids"] == ["ev_001", "ev_002"]


# --------------------------------------------------------------------------- #
# 2. previous_result semantics
# --------------------------------------------------------------------------- #
def test_first_run_omits_previous_result(client, monkeypatch):
    _live(monkeypatch)
    case_id, upload_ids = _case_with_uploads(client)

    started = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    )
    client.get(f"/api/investigations/{started.json()['id']}")

    inputs = dict(FakeCreaoAppClient.instances[-1].calls)["create_run"]["inputs"]
    assert "previous_result" not in inputs


def test_follow_up_run_includes_previous_result(client, monkeypatch):
    _live(monkeypatch)
    FakeCreaoAppClient.artifact_payload = payload_for({"ev_001"})
    case_id, upload_ids = _case_with_uploads(client)

    first = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    ).json()
    client.get(f"/api/investigations/{first['id']}")

    second = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    ).json()
    client.get(f"/api/investigations/{second['id']}")

    inputs = dict(FakeCreaoAppClient.instances[-1].calls)["create_run"]["inputs"]
    assert "previous_result" in inputs
    assert inputs["previous_result"].startswith("https://evidence.example.com/api/evidence/payloads/")
    assert first["id"] in inputs["previous_result"]


def test_confirmed_items_come_from_human_revisions(client, monkeypatch):
    _live(monkeypatch)
    FakeCreaoAppClient.artifact_payload = payload_for({"ev_001"})
    case_id, upload_ids = _case_with_uploads(client)

    first = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    ).json()
    client.get(f"/api/investigations/{first['id']}")

    item = client.get(f"/api/cases/{case_id}/items").json()[0]
    client.patch(
        f"/api/items/{item['id']}",
        json={"estimated_value": 250.0, "editor": "priya", "note": "receipt found"},
    )

    second = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    ).json()
    client.get(f"/api/investigations/{second['id']}")

    inputs = dict(FakeCreaoAppClient.instances[-1].calls)["create_run"]["inputs"]
    confirmed = json.loads(inputs["confirmed_items"])
    assert any(
        c["target_id"] == item["agent_item_id"]
        and c["field"] == "estimated_value"
        and c["value"] == 250.0
        and c["confirmed_by"] == "priya"
        for c in confirmed
    ), confirmed


# --------------------------------------------------------------------------- #
# 3. Refuse to run when the URLs are not fetchable
# --------------------------------------------------------------------------- #
def test_no_api_request_when_evidence_urls_unreachable(client, monkeypatch):
    _live(monkeypatch, base="https://unreachable.invalid", verify=True)
    case_id, upload_ids = _case_with_uploads(client)

    response = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    )

    assert response.status_code in {202, 503}
    if response.status_code == 202:
        investigation = client.get(f"/api/investigations/{response.json()['id']}").json()
        assert investigation["status"] == "failed"
        assert investigation["evidence_transport"] == "url"
        assert "not fetchable" in investigation["error"] or "Evidence URLs" in investigation["error"]

    # Either way, CREAO must never have been called.
    assert FakeCreaoAppClient.instances == []
    assert client.get(f"/api/cases/{case_id}/items").json() == []


def test_private_evidence_host_is_rejected():
    ok, reason = evidence_urls.is_public_host("http://127.0.0.1:8000/api/evidence/x/public")
    assert ok is False
    assert "private" in reason or "https" in reason

    ok, reason = evidence_urls.is_public_host("https://localhost/evidence")
    assert ok is False

    ok, _ = evidence_urls.is_public_host("https://evidence.example.com/x")
    assert ok is True


def test_url_transport_without_public_base_is_a_config_error(client, monkeypatch):
    _live(monkeypatch, base="", verify=False)
    case_id, upload_ids = _case_with_uploads(client)

    response = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "CREAO_EVIDENCE_URLS_UNAVAILABLE"
    assert "REBUILD_PUBLIC_BASE_URL" in detail["message"]
    assert FakeCreaoAppClient.instances == []


def test_unset_transport_is_blocked(client, monkeypatch):
    _live(monkeypatch, transport="unset")
    case_id, upload_ids = _case_with_uploads(client)

    response = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CREAO_EVIDENCE_TRANSPORT_UNVERIFIED"
    assert FakeCreaoAppClient.instances == []


# --------------------------------------------------------------------------- #
# 4. Signed evidence URLs
# --------------------------------------------------------------------------- #
def test_signed_url_roundtrip_and_tampering(client, monkeypatch):
    _live(monkeypatch, base="https://evidence.example.com")
    case_id, upload_ids = _case_with_uploads(client)

    url = evidence_urls.upload_url(upload_ids[0])
    assert url.startswith("https://evidence.example.com/api/evidence/")
    assert "exp=" in url and "sig=" in url
    path = url.split("evidence.example.com", 1)[1]

    served = client.get(path)
    assert served.status_code == 200
    assert served.content == PNG_BYTES
    assert served.headers["content-type"].startswith("image/")
    assert "no-store" in served.headers.get("cache-control", "")

    assert client.get(path + "0").status_code == 403                    # bad sig
    assert client.get(f"/api/evidence/{upload_ids[0]}/public?exp=1&sig=x").status_code == 403
    assert client.get(f"/api/evidence/upl_unknown/public?exp=9999999999&sig=x").status_code == 403


def test_expired_link_is_refused(client, monkeypatch):
    _live(monkeypatch, base="https://evidence.example.com")
    case_id, upload_ids = _case_with_uploads(client)

    exp = int(time.time()) - 10
    sig = evidence_urls._sign(evidence_urls.UPLOAD_PATH, upload_ids[0], exp)
    response = client.get(
        f"/api/evidence/{upload_ids[0]}/public?exp={exp}&sig={sig}"
    )
    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "expired"


def test_previous_result_payload_is_served(client, monkeypatch):
    _live(monkeypatch, base="https://evidence.example.com")
    FakeCreaoAppClient.artifact_payload = payload_for({"ev_001"})
    case_id, upload_ids = _case_with_uploads(client)

    first = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    ).json()
    client.get(f"/api/investigations/{first['id']}")

    url = evidence_urls.payload_url(first["id"])
    response = client.get(url.split("evidence.example.com", 1)[1])
    assert response.status_code == 200
    assert response.json()["case_summary"] == RUN_RESULT["case_summary"]


# --------------------------------------------------------------------------- #
# 5. Result validation
# --------------------------------------------------------------------------- #
def test_unknown_evidence_id_fails_the_run(client, monkeypatch):
    _live(monkeypatch)
    FakeCreaoAppClient.unknown_evidence_id = True
    case_id, upload_ids = _case_with_uploads(client)

    started = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    )
    investigation = client.get(f"/api/investigations/{started.json()['id']}").json()

    assert investigation["status"] == "failed"
    assert "ev_999" in investigation["error"]
    assert client.get(f"/api/cases/{case_id}/items").json() == []


def test_real_captured_result_validates():
    creao.validate_result_payload(RUN_RESULT)
    creao.validate_evidence_ids(RUN_RESULT, ["ev_001", "ev_002", "ev_003", "ev_004"])


def test_validation_rejects_incomplete_result():
    broken = dict(RUN_RESULT)
    broken.pop("questions")
    with pytest.raises(creao.CreaoInvalidResult):
        creao.validate_result_payload(broken)


def test_failed_run_marks_investigation_failed(client, monkeypatch):
    _live(monkeypatch)
    FakeCreaoAppClient.run_status = "failed"
    FakeCreaoAppClient.error_code = "AGENT_ERROR"
    case_id, upload_ids = _case_with_uploads(client)

    started = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    )
    investigation = client.get(f"/api/investigations/{started.json()['id']}").json()

    assert investigation["status"] == "failed"
    assert "AGENT_ERROR" in investigation["error"]
    assert client.get(f"/api/cases/{case_id}/items").json() == []


# --------------------------------------------------------------------------- #
# 6. Happy path + modes
# --------------------------------------------------------------------------- #
def test_live_pipeline_stores_items(client, monkeypatch):
    """Full demo: all four staged evidence files, exactly as Person 2 ran it."""
    _live(monkeypatch)
    case_id, upload_ids = _case_with_uploads(client, tuple(DEMO_FILES))

    started = client.post(
        f"/api/cases/{case_id}/investigations",
        json={
            "upload_id": upload_ids[0],
            "upload_ids": upload_ids,
            "room_hint": "living room",
        },
    )
    assert started.status_code == 202, started.text
    investigation = client.get(f"/api/investigations/{started.json()['id']}").json()

    assert investigation["status"] == "complete", investigation
    assert investigation["agent_run_id"] == CREATE_RESPONSE["id"]
    assert investigation["conversation_id"] == POLL_RESPONSE["threadId"]
    assert investigation["evidence_transport"] == "url"
    assert investigation["evidence_ids"] == ["ev_001", "ev_002", "ev_003", "ev_004"]

    items = client.get(f"/api/cases/{case_id}/items").json()
    assert len(items) == 8
    assert items[0]["agent_item_id"] == "itm_001"
    assert items[0]["damage_level"] == "unknown"
    assert items[0]["estimated_value"] is None

    download = dict(FakeCreaoAppClient.instances[-1].calls)["download_artifact"]
    assert download["url"].endswith("rebuild-result.json") or "artifacts/download" in download["url"]


def test_mock_mode_is_labelled_and_offline(client, monkeypatch):
    monkeypatch.setattr(creao.settings, "creao_mode", "mock")
    case_id, upload_ids = _case_with_uploads(client)

    inv = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    ).json()
    finished = client.get(f"/api/investigations/{inv['id']}").json()

    assert finished["status"] == "complete"
    assert finished["mode"] == "mock"
    assert finished["summary"].startswith("[SIMULATED]")
    assert FakeCreaoAppClient.instances == []


def test_v1_key_is_rejected_with_guidance(client, monkeypatch):
    _live(monkeypatch, key="cr_sk_account_key")
    case_id, upload_ids = _case_with_uploads(client)

    response = client.post(
        f"/api/cases/{case_id}/investigations", json={"upload_id": upload_ids[0]}
    )
    assert response.status_code == 503
    assert "capi_" in response.json()["detail"]["message"]


def test_health_reports_state_without_leaking_the_key(client, monkeypatch):
    _live(monkeypatch, key="capi_super_secret_value")
    body = client.get("/api/health").json()
    status = body["integrations"]["creao"]

    assert status["transport"] == "app-trigger"
    assert status["app_id"] == APP_ID
    assert status["api_key_prefix"] == "capi_"
    assert status["evidence_transport"] == "url"
    assert status["public_base_url_configured"] is True
    assert "super_secret_value" not in json.dumps(body)


def test_validate_uses_read_only_list_route(client, monkeypatch):
    _live(monkeypatch)
    body = client.post("/api/creao/validate").json()
    assert body["ok"] is True
    assert set(dict(FakeCreaoAppClient.instances[-1].calls)) == {"list_runs"}


def test_validate_refuses_in_mock_mode(client, monkeypatch):
    response = client.post("/api/creao/validate")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CREAO_MODE_MOCK"
