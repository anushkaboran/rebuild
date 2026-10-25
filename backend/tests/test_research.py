"""Researcher — confirmed contract (Person 1).

The CREAO client is faked; no credits are spent. Covers the exact input keys,
strict output validation, dedupe, isolation of candidates from inventory, and
credential separation.
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import InvestigationStatus, ResearchRun
from app.services import creao
from app.store import store

RESEARCH_APP = "a7260cbc-8253-4fa7-88d3-c644267e3221"
INVESTIGATOR_APP = "9819d9a5-89e0-4d62-a284-91bac67f8cd1"

CANDIDATE = {
    "candidate_id": "cand_001",
    "brand": "IKEA",
    "model": "STRANDMON",
    "product_name": "STRANDMON wing chair",
    "product_url": "https://www.ikea.com/us/en/p/strandmon-wing-chair",
    "matching_features": ["wing back", "deep brown upholstery"],
    "differing_features": ["leg colour differs"],
    "missing_verification": "Measurements not confirmed against the photo.",
    "price_as_displayed": "$349.00",
    "currency": "USD",
    "source_accessed_at": "2026-10-24T21:00:00Z",
    "status": "unverified_possible_match",
}


class FakeClient:
    instances: list["FakeClient"] = []
    calls: list[dict] = []
    candidates: list[dict] = [CANDIDATE]
    queries_run: list[str] = ["strandmon wing chair"]
    limitations: list[str] = ["Prices shown are retail listings, not purchase prices."]
    malformed: bool = False
    wrong_item_ref: bool = False
    run_status: str = "completed"
    error_code: str | None = None
    artifact_missing: bool = False
    last_inputs: dict | None = None

    def __init__(self, app_id=None, api_key=None):
        self.app_id = app_id or settings.creao_app_id
        self.api_key = api_key or settings.creao_app_api_key
        self.inputs: dict | None = None
        self.downloads: list[str] = []
        self._polls = 0
        FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def create_run(self, inputs):
        self.inputs = inputs
        FakeClient.last_inputs = inputs
        FakeClient.calls.append({"app_id": self.app_id, "api_key": self.api_key, "inputs": inputs})
        return {"id": f"creao-run-{len(FakeClient.instances)}", "status": "pending"}

    async def get_run(self, run_id):
        self._polls += 1
        status = "running" if self._polls == 1 else FakeClient.run_status
        run = {"id": run_id, "status": status, "threadId": "thread-1", "result": None, "errorCode": None}
        if status == "completed":
            run["result"] = {
                "text": "done",
                "artifacts": [
                    {
                        "title": "research-candidates.json",
                        "type": "application/json",
                        "url": f"https://agent.creao.ai/api/v1/runs/{run_id}/artifacts/download?artifactId=x",
                    }
                ],
            }
        if status == "failed":
            run["errorCode"] = FakeClient.error_code or "AGENT_ERROR"
        return run

    async def poll_run(self, run_id):
        while True:
            run = await self.get_run(run_id)
            if run["status"] in {"completed", "failed"}:
                if run["status"] == "failed":
                    raise creao.CreaoRunFailed("run failed", code=str(run.get("errorCode")))
                return run

    async def download_artifact(self, url):
        self.downloads.append(url)
        if FakeClient.malformed:
            return b"this is not json"
        if FakeClient.artifact_missing:
            raise creao.CreaoError("404", code="CREAO_ARTIFACT_DOWNLOAD_FAILED")
        item_ref = (FakeClient.last_inputs or {}).get("item_ref")
        if FakeClient.wrong_item_ref:
            item_ref = "itm_not_the_requested_one"
        return json.dumps(
            {
                "item_ref": item_ref,
                "candidates": copy.deepcopy(FakeClient.candidates),
                "queries_run": list(FakeClient.queries_run),
                "limitations": list(FakeClient.limitations),
            }
        ).encode()

    async def list_runs(self, limit=1):
        return {"runs": []}


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    store.reset()
    FakeClient.instances = []
    FakeClient.calls = []
    FakeClient.candidates = [copy.deepcopy(CANDIDATE)]
    FakeClient.queries_run = ["strandmon wing chair"]
    FakeClient.limitations = ["Prices shown are retail listings, not purchase prices."]
    FakeClient.malformed = False
    FakeClient.wrong_item_ref = False
    FakeClient.run_status = "completed"
    FakeClient.error_code = None
    FakeClient.artifact_missing = False
    FakeClient.last_inputs = None

    monkeypatch.setattr(creao, "CreaoAppClient", FakeClient)
    monkeypatch.setattr(settings, "creao_mode", "live")
    monkeypatch.setattr(settings, "creao_app_id", INVESTIGATOR_APP)
    monkeypatch.setattr(settings, "creao_app_api_key", "capi_investigator")
    monkeypatch.setattr(settings, "creao_research_app_id", RESEARCH_APP)
    monkeypatch.setattr(settings, "creao_research_api_key", "capi_research")
    monkeypatch.setattr(settings, "creao_app_base_url", "https://agent.creao.ai/api/v1")
    monkeypatch.setattr(settings, "store_backend", "memory")
    monkeypatch.setattr(settings, "rebuild_public_base_url", "")
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _item(client, name="Brown fabric armchair"):
    case_id = client.post("/api/cases", json={"title": "Research case"}).json()["id"]
    item = client.post(
        f"/api/cases/{case_id}/items",
        json={
            "name": name,
            "room": "living room",
            "condition": "dark upholstery, wing back, water line at the base",
            "editor": "priya",
        },
    ).json()
    return case_id, item


def _start(client, item_id, **body):
    return client.post(f"/api/items/{item_id}/research", json=body or {})


# --------------------------------------------------------------------------- #
# 1. Happy path + exact input contract
# --------------------------------------------------------------------------- #
def test_start_returns_a_research_id_immediately(client):
    _, item = _item(client)
    response = _start(client, item["id"], requested_by="priya")

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["id"].startswith("rsch_")
    assert body["status"] in ("queued", "running", "complete")
    assert body["item_id"] == item["id"]


def test_agent_inputs_match_the_confirmed_contract(client):
    _, item = _item(client)
    started = _start(client, item["id"], region_currency="usd")
    client.get(f"/api/research/{started.json()['id']}")

    inputs = FakeClient.calls[-1]["inputs"]

    assert set(inputs) == {
        "item_ref",
        "visible_observations",
        "distinguishing_features",
        "evidence_context",
        "room_photos",
        "region_currency",
    }
    assert inputs["item_ref"] == item["id"]
    assert inputs["visible_observations"] == "dark upholstery, wing back, water line at the base"
    assert isinstance(inputs["evidence_context"], str), "evidence_context must be a JSON string"
    assert isinstance(json.loads(inputs["evidence_context"]), list)
    assert isinstance(inputs["room_photos"], list)
    assert inputs["region_currency"] == "USD", "explicit value, upper-cased"


def test_region_currency_is_never_inferred(client):
    _, item = _item(client)
    started = _start(client, item["id"])
    client.get(f"/api/research/{started.json()['id']}")

    inputs = FakeClient.calls[-1]["inputs"]
    assert "region_currency" not in inputs, "no default region/currency may be assumed"


def test_credentials_are_separate(client):
    _, item = _item(client)
    started = _start(client, item["id"])
    client.get(f"/api/research/{started.json()['id']}")

    used = FakeClient.calls[-1]
    assert used["app_id"] == RESEARCH_APP
    assert used["api_key"] == "capi_research"
    assert used["api_key"] != settings.creao_app_api_key

    investigator = creao.CreaoAppClient()
    assert investigator.app_id == INVESTIGATOR_APP
    assert investigator.api_key == "capi_investigator"


# --------------------------------------------------------------------------- #
# 2. Success + result contract
# --------------------------------------------------------------------------- #
def test_successful_run_returns_candidates_queries_and_limitations(client):
    _, item = _item(client)
    started = _start(client, item["id"])
    body = client.get(f"/api/research/{started.json()['id']}").json()

    run = body["run"]
    assert run["status"] == "complete"
    assert run["agent_run_id"].startswith("creao-run-")
    assert run["queries_run"] == ["strandmon wing chair"]
    assert run["limitations"]
    assert run["error"] is None

    candidate = body["candidates"][0]
    assert candidate["candidate_id"] == "cand_001"
    assert candidate["product_name"] == "STRANDMON wing chair"
    assert candidate["product_url"].startswith("https://")
    assert candidate["matching_features"] == ["wing back", "deep brown upholstery"]
    assert candidate["missing_verification"] == ["Measurements not confirmed against the photo."]
    assert candidate["price_as_displayed"] == "$349.00"
    assert candidate["currency"] == "USD"
    assert candidate["status"] == "unverified_possible_match"
    assert candidate["unverified"] is True


def test_zero_candidates_is_a_valid_completed_run(client):
    FakeClient.candidates = []
    _, item = _item(client)
    started = _start(client, item["id"])
    body = client.get(f"/api/research/{started.json()['id']}").json()

    assert body["run"]["status"] == "complete"
    assert body["candidates"] == []
    assert body["run"]["candidates_valid"] == 0


def test_run_records_a_redacted_input_snapshot(client):
    _, item = _item(client)
    started = _start(client, item["id"])
    run = client.get(f"/api/research/{started.json()['id']}").json()["run"]

    summary = run["input_summary"]
    assert summary["item_ref"] == item["id"]
    assert "evidence_context" in summary
    # No signed URLs or secrets in the public payload.
    blob = json.dumps(run)
    assert "sig=" not in blob and "exp=" not in blob
    assert "capi_" not in blob


# --------------------------------------------------------------------------- #
# 3. Strict validation failures
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "setup, expected",
    [
        (lambda: setattr(FakeClient, "malformed", True), "not valid JSON"),
        (lambda: setattr(FakeClient, "wrong_item_ref", True), "item_ref mismatch"),
        (
            lambda: setattr(FakeClient, "candidates", [copy.deepcopy(CANDIDATE)] * 4),
            "at most 3",
        ),
        (
            lambda: setattr(
                FakeClient, "candidates", [dict(copy.deepcopy(CANDIDATE), status="confirmed")]
            ),
            "unverified_possible_match",
        ),
        (
            lambda: setattr(
                FakeClient,
                "candidates",
                [dict(copy.deepcopy(CANDIDATE), product_url="ftp://example.com")],
            ),
            "unusable product_url",
        ),
    ],
)
def test_invalid_results_fail_the_run(client, setup, expected):
    setup()
    _, item = _item(client)
    started = _start(client, item["id"])
    body = client.get(f"/api/research/{started.json()['id']}").json()

    assert body["run"]["status"] == "failed"
    assert expected in body["run"]["error"]
    assert body["candidates"] == []
    assert store.research_candidates == {}


def test_creao_run_failure_is_reported(client):
    FakeClient.run_status = "failed"
    FakeClient.error_code = "AGENT_ERROR"
    _, item = _item(client)
    started = _start(client, item["id"])
    run = client.get(f"/api/research/{started.json()['id']}").json()["run"]

    assert run["status"] == "failed"
    assert "AGENT_ERROR" in run["error"]


def test_missing_artifact_fails_the_run(client):
    FakeClient.artifact_missing = True
    _, item = _item(client)
    started = _start(client, item["id"])
    run = client.get(f"/api/research/{started.json()['id']}").json()["run"]

    assert run["status"] == "failed"
    assert "research-candidates" in run["error"]


# --------------------------------------------------------------------------- #
# 4. Dedupe, reopening, isolation
# --------------------------------------------------------------------------- #
def test_duplicate_click_returns_the_active_run(client):
    _, item = _item(client)
    # Simulate a run already in flight (the background task has not finished).
    active = ResearchRun(
        id="rsch_active01",
        case_id=item["case_id"],
        item_id=item["id"],
        status=InvestigationStatus.running,
        agent_run_id="creao-run-existing",
    )
    store.research_runs[active.id] = active

    response = _start(client, item["id"])
    assert response.status_code == 200, "must not start a second paid run"
    assert response.json()["id"] == "rsch_active01"
    assert FakeClient.calls == [], "no CREAO run may be created"


def test_saved_results_can_be_reopened(client):
    _, item = _item(client)
    started = _start(client, item["id"])
    first = client.get(f"/api/research/{started.json()['id']}").json()

    history = client.get(f"/api/items/{item['id']}/research").json()
    assert [r["id"] for r in history] == [started.json()["id"]]

    again = client.get(f"/api/research/{started.json()['id']}").json()
    assert again == first


def test_inventory_is_never_touched_by_research(client):
    _, item = _item(client)
    before = client.get(f"/api/items/{item['id']}").json()

    started = _start(client, item["id"])
    client.get(f"/api/research/{started.json()['id']}")

    after = client.get(f"/api/items/{item['id']}").json()
    for field in (
        "name", "brand", "model", "purchase_price", "purchase_date", "currency",
        "estimated_value", "user_confirmed_fields", "status", "revision",
        "review_status", "condition",
    ):
        assert after[field] == before[field], f"research changed {field}"


# --------------------------------------------------------------------------- #
# 5. Config + routing
# --------------------------------------------------------------------------- #
def test_missing_research_key_is_rejected_without_calling_creao(client, monkeypatch):
    monkeypatch.setattr(settings, "creao_research_api_key", "")
    _, item = _item(client)
    response = _start(client, item["id"])

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "RESEARCH_NOT_CONFIGURED"
    assert FakeClient.instances == []


def test_mock_mode_refuses_research(client, monkeypatch):
    monkeypatch.setattr(settings, "creao_mode", "mock")
    _, item = _item(client)
    response = _start(client, item["id"])

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CREAO_MODE_MOCK"
    assert FakeClient.instances == []


def test_unknown_ids_return_404(client):
    _, item = _item(client)
    assert _start(client, "itm_missing").status_code == 404
    assert client.get("/api/research/rsch_missing").status_code == 404


def test_case_scoped_aliases_still_work(client):
    case_id, item = _item(client)
    response = client.post(
        f"/api/cases/{case_id}/items/{item['id']}/research", json={"requested_by": "priya"}
    )
    assert response.status_code == 202
    run_id = response.json()["id"]

    alias = client.get(f"/api/cases/{case_id}/items/{item['id']}/research/{run_id}")
    canonical = client.get(f"/api/research/{run_id}")
    assert alias.json()["run"]["id"] == canonical.json()["run"]["id"]
