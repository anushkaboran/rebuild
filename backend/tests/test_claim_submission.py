"""Claim submission assistant — draft-only, no email, no paid runs.

The CREAO client is faked. Nothing here contacts Gmail; `draft_created` is only
true when the agent reports a real `gmail_draft_id`.
"""

from __future__ import annotations

import copy
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import ClaimSubmission, InvestigationStatus
from app.services import claim, creao
from app.store import store

CLAIM_APP = "947ad3f0-04f6-4f81-b73a-50ff6b116bf0"

RESULT = {
    "case_ref": None,                      # filled per test
    "status": "draft_ready",
    "recipient_email": "claims@insurer.test",
    "subject": "Claim submission - case_x",
    "body_text": "Dear insurer,\n\nPlease find our claim submission.",
    "attachment_filename": "recovery-inventory.pdf",
    "gmail_draft_id": "draft_123",
    "gmail_message_id": "msg_123",
    "missing_information": [],
    "warnings": [],
}


class FakeClient:
    instances: list["FakeClient"] = []
    create_calls: list[dict] = []
    payload: dict = RESULT
    run_status: str = "completed"

    def __init__(self, app_id=None, api_key=None):
        self.app_id = app_id or settings.creao_app_id
        self.api_key = api_key or settings.creao_app_api_key
        self._polls = 0
        FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def create_run(self, inputs):
        FakeClient.create_calls.append(
            {"app_id": self.app_id, "api_key": self.api_key, "inputs": inputs}
        )
        return {"id": f"creao-claim-{len(FakeClient.create_calls)}", "status": "pending"}

    async def get_run(self, run_id):
        self._polls += 1
        status = "running" if self._polls == 1 else FakeClient.run_status
        return {
            "id": run_id, "status": status, "threadId": "t-1",
            "result": {
                "artifacts": [
                    {"title": "claim-email-draft.json", "type": "application/json",
                     "url": f"https://agent.creao.ai/api/v1/runs/{run_id}/artifacts/download?artifactId=x"}
                ]
            },
        }

    async def poll_run(self, run_id):
        while True:
            run = await self.get_run(run_id)
            if run["status"] in {"completed", "failed"}:
                if run["status"] == "failed":
                    raise creao.CreaoRunFailed("run failed", code="AGENT_ERROR")
                return run

    async def download_artifact(self, url):
        return json.dumps(FakeClient.payload).encode()

    async def list_runs(self, limit=1):
        return {"runs": []}


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    store.reset()
    FakeClient.instances = []
    FakeClient.create_calls = []
    FakeClient.payload = copy.deepcopy(RESULT)
    FakeClient.run_status = "completed"

    monkeypatch.setattr(creao, "CreaoAppClient", FakeClient)
    monkeypatch.setattr(settings, "creao_mode", "live")
    monkeypatch.setattr(settings, "creao_app_id", "9819d9a5-89e0-4d62-a284-91bac67f8cd1")
    monkeypatch.setattr(settings, "creao_app_api_key", "capi_investigator")
    monkeypatch.setattr(settings, "creao_claim_app_id", CLAIM_APP)
    monkeypatch.setattr(settings, "creao_claim_api_key", "capi_claim")
    monkeypatch.setattr(settings, "creao_app_base_url", "https://agent.creao.ai/api/v1")
    monkeypatch.setattr(settings, "store_backend", "memory")
    monkeypatch.setattr(settings, "rebuild_public_base_url", "https://evidence.example")
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


BODY = {
    "sender_name": "Priya Raman",
    "insurer_name": "Northwind Insurance",
    "recipient_email": "claims@insurer.test",
    "requested_by": "priya",
}


def _case(client, *, confirmed=True):
    case_id = client.post("/api/cases", json={"title": "42 Willow Creek Rd",
                                              "address": "Asheville, NC"}).json()["id"]
    item = client.post(
        f"/api/cases/{case_id}/items",
        json={"name": "Beige armchair", "condition": "rolled arms", "brand": "AgentGuess",
              "editor": "priya"},
    ).json()
    if confirmed:
        client.patch(
            f"/api/items/{item['id']}",
            json={"estimated_value": 425.0, "brand": "Renter-confirmed brand",
                  "editor": "priya", "if_revision": 1},
        )
    return case_id, item


def _start(client, case_id, **overrides):
    return client.post(f"/api/cases/{case_id}/claim-submission", json={**BODY, **overrides})


# --------------------------------------------------------------------------- #
# Input validation + build
# --------------------------------------------------------------------------- #
def test_requires_a_valid_recipient_email(client):
    case_id, _ = _case(client)
    for bad in ["not-an-email", "a@b", "two@at@x.com", "has space@x.com"]:
        response = _start(client, case_id, recipient_email=bad)
        assert response.status_code == 400, bad
        assert response.json()["detail"]["code"] == "INVALID_RECIPIENT_EMAIL"
    assert FakeClient.create_calls == []


def test_required_fields_are_enforced_by_the_schema(client):
    case_id, _ = _case(client)
    response = client.post(f"/api/cases/{case_id}/claim-submission",
                           json={"sender_name": "", "insurer_name": "X",
                                 "recipient_email": "a@b.co"})
    assert response.status_code == 422


def test_reviewed_record_is_a_json_string_with_four_sections(client):
    case_id, _ = _case(client)
    _start(client, case_id)

    inputs = FakeClient.create_calls[-1]["inputs"]
    assert isinstance(inputs["reviewed_record"], str)
    record = json.loads(inputs["reviewed_record"])

    assert set(record) == {
        "case_ref", "inventory", "evidence_references",
        "human_confirmations", "unresolved_issues",
    }
    assert record["case_ref"] == case_id
    assert inputs["case_ref"] == case_id
    assert inputs["sender_name"] == "Priya Raman"
    assert inputs["insurer_name"] == "Northwind Insurance"
    assert inputs["recipient_email"] == "claims@insurer.test"


def test_confirmed_facts_are_separated_from_agent_observations(client):
    case_id, item = _case(client, confirmed=True)
    _start(client, case_id)

    record = json.loads(FakeClient.create_calls[-1]["inputs"]["reviewed_record"])
    entry = next(i for i in record["inventory"] if i["item_id"] == item["id"])

    # Human-corrected value: confirmed.
    assert entry["estimated_value"]["confirmed"] is True
    assert entry["estimated_value"]["confirmed_by"] == "priya"
    assert entry["brand"]["confirmed"] is True
    assert entry["brand"]["value"] == "Renter-confirmed brand"
    # Untouched by a human: not confirmed.
    assert entry["condition"]["confirmed"] is False

    assert any(c["field"] == "estimated_value" and c["confirmed_by"] == "priya"
               for c in record["human_confirmations"])


def test_empty_human_confirmations_stay_an_empty_array(client):
    case_id, _ = _case(client, confirmed=False)
    _start(client, case_id)

    record = json.loads(FakeClient.create_calls[-1]["inputs"]["reviewed_record"])
    assert record["human_confirmations"] == []
    assert all(i["estimated_value"]["confirmed"] is False for i in record["inventory"])


def test_optional_inputs_are_only_sent_when_present(client):
    case_id, _ = _case(client)
    _start(client, case_id, policy_number="POL-1", claim_number="CLM-9")
    inputs = FakeClient.create_calls[-1]["inputs"]
    assert inputs["policy_number"] == "POL-1"
    assert inputs["claim_number"] == "CLM-9"
    assert "policy_type" not in inputs
    assert "additional_notes" not in inputs


# --------------------------------------------------------------------------- #
# PDF URL
# --------------------------------------------------------------------------- #
def test_recovery_pdf_url_is_https_and_signed(client):
    case_id, _ = _case(client)
    response = _start(client, case_id)
    assert response.status_code == 202

    inputs = FakeClient.create_calls[-1]["inputs"]
    url = inputs["recovery_pdf"]
    assert url.startswith("https://evidence.example/api/evidence/claims/")
    assert "sig=" in url and "exp=" in url
    assert "localhost" not in url and "127.0.0.1" not in url


def test_pdf_can_be_omitted(client):
    case_id, _ = _case(client)
    _start(client, case_id, include_recovery_pdf=False)
    inputs = FakeClient.create_calls[-1]["inputs"]
    assert "recovery_pdf" not in inputs


def test_pdf_without_public_base_is_rejected_before_any_run(client, monkeypatch):
    monkeypatch.setattr(settings, "rebuild_public_base_url", "")
    case_id, _ = _case(client)
    response = _start(client, case_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PUBLIC_BASE_URL_NOT_CONFIGURED"
    assert FakeClient.create_calls == []


# --------------------------------------------------------------------------- #
# Result validation
# --------------------------------------------------------------------------- #
def _run(client, case_id, payload=None):
    if payload is not None:
        FakeClient.payload = payload
    started = _start(client, case_id)
    assert started.status_code == 202, started.text
    return client.get(f"/api/claim-submissions/{started.json()['submission']['id']}").json()


def test_draft_ready_requires_a_real_draft_id(client):
    case_id, _ = _case(client)
    payload = copy.deepcopy(RESULT)
    payload["case_ref"] = case_id
    payload["gmail_draft_id"] = None
    body = _run(client, case_id, payload)

    assert body["submission"]["status"] == "failed"
    assert "gmail_draft_id" in body["submission"]["error"]
    assert body["submission"]["result_status"] == "draft_failed"


def test_needs_information_is_accepted(client):
    case_id, _ = _case(client)
    payload = copy.deepcopy(RESULT)
    payload["case_ref"] = case_id
    payload["status"] = "needs_information"
    payload["gmail_draft_id"] = None
    payload["gmail_message_id"] = None
    payload["subject"] = None
    payload["body_text"] = None
    payload["attachment_filename"] = None
    payload["missing_information"] = ["policy_number"]
    body = _run(client, case_id, payload)

    assert body["submission"]["result_status"] == "needs_information"
    assert body["submission"]["missing_information"] == ["policy_number"]
    assert body["draft_created"] is False


def test_draft_failed_is_accepted_and_terminal(client):
    case_id, _ = _case(client)
    payload = copy.deepcopy(RESULT)
    payload["case_ref"] = case_id
    payload["status"] = "draft_failed"
    payload["gmail_draft_id"] = None
    payload["gmail_message_id"] = None
    payload["warnings"] = ["recovery_pdf could not be downloaded"]
    body = _run(client, case_id, payload)

    assert body["submission"]["result_status"] == "draft_failed"
    assert body["submission"]["warnings"]
    assert body["sent"] is False


def test_non_contract_keys_are_rejected(client):
    case_id, _ = _case(client)
    payload = copy.deepcopy(RESULT)
    payload["case_ref"] = case_id
    payload["extra_key"] = "nope"
    body = _run(client, case_id, payload)
    assert body["submission"]["status"] == "failed"
    assert "exactly the ten contract keys" in body["submission"]["error"]


def test_altered_recipient_is_rejected(client):
    case_id, _ = _case(client)
    payload = copy.deepcopy(RESULT)
    payload["case_ref"] = case_id
    payload["recipient_email"] = "someone.else@insurer.test"
    body = _run(client, case_id, payload)
    assert body["submission"]["status"] == "failed"
    assert "recipient_email" in body["submission"]["error"]


def test_wrong_case_ref_is_rejected(client):
    case_id, _ = _case(client)
    payload = copy.deepcopy(RESULT)
    payload["case_ref"] = "case_somewhere_else"
    body = _run(client, case_id, payload)
    assert body["submission"]["status"] == "failed"
    assert "case_ref mismatch" in body["submission"]["error"]


# --------------------------------------------------------------------------- #
# Duplicate prevention / no retries
# --------------------------------------------------------------------------- #
def test_active_submission_is_returned_not_duplicated(client):
    case_id, _ = _case(client)
    active = ClaimSubmission(
        id="clm_active", case_id=case_id, status=InvestigationStatus.running,
        recipient_email=BODY["recipient_email"],
    )
    store.claim_submissions[active.id] = active

    response = _start(client, case_id)
    assert response.status_code == 200
    assert response.json()["submission"]["id"] == "clm_active"
    assert FakeClient.create_calls == []


def test_existing_draft_blocks_a_new_run_without_resubmit_flag(client):
    case_id, _ = _case(client)
    payload = copy.deepcopy(RESULT)
    payload["case_ref"] = case_id
    _run(client, case_id, payload)
    assert len(FakeClient.create_calls) == 1

    blocked = _start(client, case_id)
    assert blocked.status_code == 409
    detail = blocked.json()["detail"]
    assert detail["code"] == "CLAIM_DUPLICATE"
    assert detail["gmail_draft_id"] == "draft_123"
    assert len(FakeClient.create_calls) == 1, "no second agent run may start"

    forced = _start(client, case_id, allow_resubmit=True)
    assert forced.status_code == 202
    assert len(FakeClient.create_calls) == 2


def test_uncertain_failure_blocks_automatic_retry(client):
    case_id, _ = _case(client)
    uncertain = ClaimSubmission(
        id="clm_uncertain", case_id=case_id, status=InvestigationStatus.failed,
        result_status="draft_failed", recipient_email=BODY["recipient_email"],
        error="CREAO_TIMEOUT: no response",
    )
    store.claim_submissions[uncertain.id] = uncertain

    response = _start(client, case_id)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CLAIM_DUPLICATE"
    assert FakeClient.create_calls == []


def test_creao_failure_marks_failed_never_ready(client):
    case_id, _ = _case(client)
    FakeClient.run_status = "failed"
    started = _start(client, case_id)
    body = client.get(f"/api/claim-submissions/{started.json()['submission']['id']}").json()

    assert body["submission"]["status"] == "failed"
    assert body["submission"]["gmail_draft_id"] is None
    assert "AGENT_ERROR" in body["submission"]["error"]


# --------------------------------------------------------------------------- #
# Credentials, history, no Gmail
# --------------------------------------------------------------------------- #
def test_uses_the_claim_app_and_credential(client):
    case_id, _ = _case(client)
    _start(client, case_id)
    used = FakeClient.create_calls[-1]
    assert used["app_id"] == CLAIM_APP
    assert used["api_key"] == "capi_claim"
    assert used["api_key"] != settings.creao_app_api_key


def test_missing_claim_key_is_a_config_error(client, monkeypatch):
    monkeypatch.setattr(settings, "creao_claim_api_key", "")
    case_id, _ = _case(client)
    response = _start(client, case_id)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CLAIM_NOT_CONFIGURED"
    assert FakeClient.create_calls == []


def test_history_is_listed_newest_first(client):
    case_id, _ = _case(client)
    payload = copy.deepcopy(RESULT)
    payload["case_ref"] = case_id
    _run(client, case_id, payload)

    history = client.get(f"/api/cases/{case_id}/claim-submissions").json()
    assert len(history) == 1
    assert history[0]["case_id"] == case_id
    assert history[0]["result_status"] == "draft_ready"


def test_unknown_submission_is_404(client):
    assert client.get("/api/claim-submissions/clm_missing").status_code == 404


def test_backend_never_marks_anything_as_sent(client):
    case_id, _ = _case(client)
    payload = copy.deepcopy(RESULT)
    payload["case_ref"] = case_id
    body = _run(client, case_id, payload)
    assert body["sent"] is False
    # Nothing in the stored record claims an email was sent.
    assert not hasattr(store.claim_submissions[body["submission"]["id"]], "sent_at")
