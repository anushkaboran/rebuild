"""Recovery of an already-completed CREAO run.

No CREAO run is created here: the fake client raises if `create_run` is ever
called, so every test also proves the recovery path cannot spend credits.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import InvestigationStatus
from app.services import creao
from app.store import store

FIXTURES = Path(__file__).parent / "fixtures"
POLL_RESPONSE = json.loads((FIXTURES / "api-poll-response.json").read_text())
RUN_RESULT = json.loads((FIXTURES / "api-run-result.json").read_text())

APP_ID = "9819d9a5-89e0-4d62-a284-91bac67f8cd1"
RUN_ID = "56821244-bbbf-461d-bb50-52cd3aeabfe3"
DEMO = (
    ("01_room_armchair.jpg", "photo", "ev_001"),
    ("02_armchair_closeup.jpg", "photo", "ev_002"),
    ("04_receipt_lamp.png", "receipt", "ev_003"),
    ("05_receipt_groceries.png", "receipt", "ev_004"),
)

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


class RecoveryFake:
    """Read-only CREAO client. Any attempt to start a run is a test failure."""

    instances: list["RecoveryFake"] = []
    run_status: str = "completed"
    artifact_payload: dict = RUN_RESULT
    artifact_error: bool = False
    poll_sequence: list[str] = ["completed"]

    def __init__(self) -> None:
        self.calls: list[str] = []
        RecoveryFake.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def create_run(self, inputs):
        raise AssertionError("recovery must never create a CREAO run")

    async def get_run(self, run_id):
        self.calls.append("get_run")
        run = dict(POLL_RESPONSE)
        run["id"] = run_id
        run["status"] = RecoveryFake.run_status
        return run

    async def download_artifact(self, url):
        self.calls.append("download_artifact")
        if RecoveryFake.artifact_error:
            raise creao.CreaoError("403", code="CREAO_ARTIFACT_DOWNLOAD_FAILED")
        return json.dumps(RecoveryFake.artifact_payload).encode()


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    store.reset()
    RecoveryFake.instances = []
    RecoveryFake.run_status = "completed"
    RecoveryFake.artifact_payload = RUN_RESULT
    RecoveryFake.artifact_error = False

    monkeypatch.setattr(creao, "CreaoAppClient", RecoveryFake)
    monkeypatch.setattr(creao.settings, "creao_mode", "live")
    monkeypatch.setattr(creao.settings, "creao_app_api_key", "capi_test")
    monkeypatch.setattr(creao.settings, "creao_app_id", APP_ID)
    monkeypatch.setattr(creao.settings, "creao_app_base_url", "https://agent.creao.ai/api/v1")
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _case_with_demo_uploads(client) -> tuple[str, dict[str, str]]:
    case_id = client.post("/api/cases", json={"title": "42 Willow Creek Rd"}).json()["id"]
    mapping: dict[str, str] = {}
    for filename, kind, evidence_id in DEMO:
        upload = client.post(
            f"/api/cases/{case_id}/uploads",
            files={"file": (filename, io.BytesIO(PNG_BYTES), "image/jpeg")},
            data={"kind": kind},
        ).json()
        mapping[evidence_id] = upload["id"]
    return case_id, mapping


def _recover(client, case_id, *, run_id=RUN_ID, **extra):
    return client.post(
        "/api/creao/recover",
        json={"run_id": run_id, "case": {"id": case_id, "title": "42 Willow Creek Rd"}, **extra},
    )


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_recovers_completed_run_without_starting_a_new_one(client):
    case_id, mapping = _case_with_demo_uploads(client)

    response = _recover(client, case_id)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["recovered"] is True
    assert body["already_recovered"] is False
    assert body["run_id"] == RUN_ID
    assert body["run_status"] == "completed"
    assert body["items_created"] == 8
    assert body["evidence_ids"] == ["ev_001", "ev_002", "ev_003", "ev_004"]
    assert body["investigation"]["status"] == "complete"
    assert body["investigation"]["recovered"] is True
    assert body["investigation"]["agent_run_id"] == RUN_ID

    items = client.get(f"/api/cases/{case_id}/items").json()
    assert len(items) == 8
    # Items are linked to the upload behind their first evidence reference.
    by_agent_id = {i["agent_item_id"]: i for i in items}
    assert by_agent_id["itm_001"]["upload_id"] == mapping["ev_001"]
    assert by_agent_id["itm_001"]["supporting_evidence_ids"] == ["ev_001", "ev_002"]

    fake = RecoveryFake.instances[-1]
    assert "get_run" in fake.calls
    assert "download_artifact" in fake.calls


def test_recovery_does_not_duplicate_items_on_retry(client):
    case_id, _ = _case_with_demo_uploads(client)

    first = _recover(client, case_id).json()
    assert first["items_created"] == 8

    second = _recover(client, case_id).json()
    assert second["already_recovered"] is True
    assert second["items_created"] == 0
    assert second["items_already_present"] == 8
    assert len(client.get(f"/api/cases/{case_id}/items").json()) == 8


def test_retry_after_status_reset_does_not_duplicate(client):
    """Even if the investigation is forced back to failed, a re-import is safe."""
    case_id, _ = _case_with_demo_uploads(client)
    first = _recover(client, case_id).json()
    inv_id = first["investigation"]["id"]

    # Simulate the original bug: investigation marked failed, items already there.
    inv = store.investigations[inv_id]
    store.investigations[inv_id] = inv.model_copy(
        update={"status": InvestigationStatus.failed, "agent_run_id": None}
    )

    second = _recover(client, case_id, investigation_id=inv_id).json()
    assert second["items_created"] == 0
    assert second["items_already_present"] == 8
    assert len(client.get(f"/api/cases/{case_id}/items").json()) == 8


def test_recovery_preserves_human_corrections(client):
    case_id, _ = _case_with_demo_uploads(client)
    first = _recover(client, case_id).json()
    inv_id = first["investigation"]["id"]

    item = client.get(f"/api/cases/{case_id}/items").json()[0]
    patched = client.patch(
        f"/api/items/{item['id']}",
        json={
            "estimated_value": 425.0,
            "condition": "Renter confirmed: base is cracked",
            "editor": "priya",
            "note": "verified on site",
        },
    ).json()
    assert patched["revision"] == 2

    # Force a re-import of the same run.
    inv = store.investigations[inv_id]
    store.investigations[inv_id] = inv.model_copy(
        update={"status": InvestigationStatus.failed, "agent_run_id": None}
    )
    _recover(client, case_id, investigation_id=inv_id)

    after = [i for i in client.get(f"/api/cases/{case_id}/items").json() if i["id"] == item["id"]][0]
    assert after["estimated_value"] == 425.0, "agent re-import must not clobber a human value"
    assert after["condition"] == "Renter confirmed: base is cracked"
    assert after["revision"] == 2
    assert after["updated_by"] == "priya"
    revisions = client.get(f"/api/items/{item['id']}/revisions").json()
    assert len(revisions) == 1


def test_recovered_run_records_run_id_for_traceability(client):
    case_id, _ = _case_with_demo_uploads(client)
    body = _recover(client, case_id).json()
    investigation = client.get(f"/api/investigations/{body['investigation']['id']}").json()

    assert investigation["agent_run_id"] == RUN_ID
    assert investigation["conversation_id"] == POLL_RESPONSE["threadId"]
    assert investigation["evidence_transport"] == "recovered"
    assert investigation["recovered"] is True


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #
def test_mock_mode_is_refused(client, monkeypatch):
    monkeypatch.setattr(creao.settings, "creao_mode", "mock")
    response = _recover(client, "case_x")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CREAO_MODE_MOCK"


def test_unknown_case_without_metadata_is_a_clear_error(client):
    response = _recover(client, "case_missing")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "CASE_NOT_RESTORABLE"
    assert "in memory" in detail["message"]
    assert RecoveryFake.instances == [], "must not call CREAO when context is missing"


def test_case_exists_but_uploads_do_not_match(client):
    case_id = client.post("/api/cases", json={"title": "empty"}).json()["id"]
    response = _recover(client, case_id)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "EVIDENCE_METADATA_MISSING"
    assert "01_room_armchair.jpg" in response.json()["detail"]["message"]


def test_incomplete_run_is_rejected(client):
    case_id, _ = _case_with_demo_uploads(client)
    RecoveryFake.run_status = "running"

    response = _recover(client, case_id)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RUN_NOT_COMPLETED"
    assert client.get(f"/api/cases/{case_id}/items").json() == []


def test_invalid_result_is_rejected_and_nothing_is_written(client):
    case_id, _ = _case_with_demo_uploads(client)
    broken = dict(RUN_RESULT)
    broken.pop("questions")
    RecoveryFake.artifact_payload = broken

    response = _recover(client, case_id)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "CREAO_INVALID_RESULT"
    assert client.get(f"/api/cases/{case_id}/items").json() == []
    assert all(
        i.status != InvestigationStatus.complete for i in store.investigations.values()
    )


def test_unreadable_artifact_is_rejected(client):
    case_id, _ = _case_with_demo_uploads(client)
    RecoveryFake.artifact_error = True

    response = _recover(client, case_id)
    assert response.status_code == 502
    assert client.get(f"/api/cases/{case_id}/items").json() == []


def test_explicit_mapping_with_unknown_upload_is_rejected(client):
    case_id, _ = _case_with_demo_uploads(client)
    response = _recover(
        client,
        case_id,
        evidence=[
            {"evidence_id": "ev_001", "upload_id": "upl_does_not_exist", "filename": "x.jpg"}
        ],
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EVIDENCE_METADATA_MISSING"


def test_case_restoration_from_disk(client, monkeypatch, tmp_path=None):
    """A lost case can be rebuilt from supplied metadata + files still on disk."""
    case_id, mapping = _case_with_demo_uploads(client)

    # Snapshot the uploads, then wipe the in-memory store (simulating a restart).
    uploads = {u.id: u.model_dump() for u in store.uploads.values()}
    store.reset()

    evidence = [
        {
            "evidence_id": evidence_id,
            "upload_id": mapping[evidence_id],
            "filename": filename,
            "content_type": "image/jpeg",
            "kind": kind,
        }
        for filename, kind, evidence_id in DEMO
    ]

    response = client.post(
        "/api/creao/recover",
        json={
            "run_id": RUN_ID,
            "case": {"id": case_id, "title": "42 Willow Creek Rd", "address": "Asheville, NC"},
            "evidence": evidence,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["restored_case"] is True
    assert len(body["restored_uploads"]) == 4
    assert body["items_created"] == 8
    assert store.get_case(case_id) is not None
    assert len(client.get(f"/api/cases/{case_id}/items").json()) == 8
    # Upload ids are preserved exactly as before the wipe.
    assert {u.id for u in store.list_uploads(case_id)} == set(mapping.values())
