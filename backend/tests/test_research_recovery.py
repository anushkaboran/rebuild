"""Artifact retrieval + recovery for the Researcher.

Regression coverage for the live run whose `research-candidates.json` existed but
was not found by the backend. The fix stops matching on the artifact *title* and
instead inspects every artifact the run actually returns, accepting the first
body that matches the contract.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import InvestigationStatus, ResearchRun
from app.services import creao, researcher
from app.store import store

RESEARCH_APP = "a7260cbc-8253-4fa7-88d3-c644267e3221"
CREAO_RUN = "11626040-0ac3-44db-84cd-59c6f22279cc"

CANDIDATE = {
    "candidate_id": "cand_001",
    "brand": "IKEA",
    "model": "STRANDMON",
    "product_name": "STRANDMON wing chair",
    "product_url": "https://www.ikea.com/us/en/p/strandmon-wing-chair",
    "matching_features": ["wing back"],
    "differing_features": ["leg colour differs"],
    "missing_verification": ["Measurements not confirmed."],
    "price_as_displayed": "$349.00",
    "currency": "USD",
    "source_accessed_at": "2026-10-24T21:00:00Z",
    "status": "unverified_possible_match",
}


class FakeClient:
    """Returns a completed run whose artifact is NOT titled research-candidates.json."""

    instances: list["FakeClient"] = []
    create_calls: int = 0
    run_status: str = "completed"
    containers: dict = {}
    body: bytes | None = None
    download_error: str | None = None
    last_inputs: dict | None = None

    def __init__(self, app_id=None, api_key=None):
        self.app_id = app_id or settings.creao_app_id
        self.api_key = api_key or settings.creao_app_api_key
        FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def create_run(self, inputs):
        FakeClient.create_calls += 1
        FakeClient.last_inputs = inputs
        return {"id": "should-not-happen", "status": "pending"}

    async def get_run(self, run_id):
        return {
            "id": run_id,
            "status": FakeClient.run_status,
            "threadId": "thread-1",
            "errorCode": None,
            "result": copy.deepcopy(FakeClient.containers),
        }

    async def poll_run(self, run_id):
        return await self.get_run(run_id)

    async def download_artifact(self, url):
        if FakeClient.download_error:
            raise creao.CreaoError(FakeClient.download_error, code="CREAO_ARTIFACT_DOWNLOAD_FAILED")
        if FakeClient.body is not None:
            return FakeClient.body
        return json.dumps(
            {
                "item_ref": (FakeClient.last_inputs or {}).get("item_ref") or "itm_1",
                "candidates": [copy.deepcopy(CANDIDATE)],
                "queries_run": ["strandmon wing chair"],
                "limitations": ["Listed prices are not purchase prices."],
            }
        ).encode()

    async def list_runs(self, limit=1):
        return {"runs": []}


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    store.reset()
    FakeClient.instances = []
    FakeClient.create_calls = 0
    FakeClient.run_status = "completed"
    FakeClient.body = None
    FakeClient.download_error = None
    FakeClient.last_inputs = None
    # The live shape: a human title, the file name only inside `id`.
    FakeClient.containers = {
        "artifacts": [
            {
                "id": "workspaces/ws/thread/work/build_result.py",
                "title": "Build script",
                "type": "text/plain",
                "url": "https://agent.creao.ai/api/v1/runs/x/artifacts/download?artifactId=script",
            },
            {
                "id": "workspaces/ws/thread/files/research-candidates.json",
                "title": "Outputs",
                "type": "application/octet-stream",
                "url": "https://agent.creao.ai/api/v1/runs/x/artifacts/download?artifactId=candidates",
            },
        ]
    }

    monkeypatch.setattr(creao, "CreaoAppClient", FakeClient)
    monkeypatch.setattr(settings, "creao_mode", "live")
    monkeypatch.setattr(settings, "creao_app_id", "9819d9a5-89e0-4d62-a284-91bac67f8cd1")
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


def _failed_run(client, case_id=None):
    case_id = case_id or client.post("/api/cases", json={"title": "Recovery"}).json()["id"]
    item = client.post(
        f"/api/cases/{case_id}/items",
        json={"name": "Brown armchair", "condition": "wing back, dark upholstery", "editor": "priya"},
    ).json()
    run = ResearchRun(
        id="rsch_3491e9cce4aa",
        case_id=case_id,
        item_id=item["id"],
        status=InvestigationStatus.failed,
        agent_run_id=CREAO_RUN,
        error="RESEARCH_INVALID_RESULT: no readable research-candidates.json was found",
    )
    store.research_runs[run.id] = run
    return case_id, item, run


# --------------------------------------------------------------------------- #
# 1. Root cause: artifact title is not the file name
# --------------------------------------------------------------------------- #
def test_artifact_found_even_when_title_is_not_the_filename(client):
    _, _, run = _failed_run(client)

    payload, source, probes = _run_fetch(run.item_id)
    assert payload["item_ref"] == run.item_id
    assert "candidates" in payload
    # The winning artifact was identified by its id/path, not its title.
    assert "Outputs" in source or "research-candidates" in source
    assert any(p.json for p in probes)


def test_all_artifacts_are_probed_when_none_looks_like_the_document(client):
    FakeClient.containers = {
        "artifacts": [
            {"id": "x/build.py", "title": "Build script", "type": "text/plain",
             "url": "https://agent.creao.ai/api/v1/runs/x/artifacts/download?artifactId=a"},
        ]
    }
    _, item, _ = _failed_run(client)

    payload, source, probes = _run_fetch(item["id"], body_override=json.dumps({
        "item_ref": item["id"], "candidates": [], "queries_run": [], "limitations": []
    }).encode())
    assert payload["candidates"] == []
    assert len(probes) == 1


def test_missing_artifact_metadata_has_its_own_error(client):
    FakeClient.containers = {"text": "done"}
    _, item, _ = _failed_run(client)
    with pytest.raises(researcher.ResearchNoArtifacts) as exc:
        _run_fetch(item["id"])
    assert exc.value.code == "RESEARCH_NO_ARTIFACTS"


def test_artifact_without_document_reports_not_found(client):
    FakeClient.containers = {
        "artifacts": [
            {"id": "x/build.py", "title": "Build script", "type": "text/plain",
             "url": "https://agent.creao.ai/api/v1/runs/x/artifacts/download?artifactId=a"},
        ]
    }
    _, item, _ = _failed_run(client)
    with pytest.raises(researcher.ResearchArtifactNotFound) as exc:
        _run_fetch(item["id"], body_override=b'{"hello": "world"}')
    assert exc.value.code == "RESEARCH_ARTIFACT_NOT_FOUND"


def test_download_failure_reports_download_error(client):
    FakeClient.download_error = "500 server error"
    _, item, _ = _failed_run(client)
    with pytest.raises(researcher.ResearchArtifactDownload) as exc:
        _run_fetch(item["id"])
    assert exc.value.code == "RESEARCH_ARTIFACT_DOWNLOAD"


def test_diagnostics_never_leak_urls_or_credentials(client):
    _, _, run = _failed_run(client)
    report = client.get(f"/api/research/{run.id}/diagnostics")
    assert report.status_code == 200, report.text

    blob = json.dumps(report.json())
    assert "artifactId" not in blob
    assert "https://" not in blob
    assert "capi_" not in blob
    body = report.json()
    assert body["run_status"] == "completed"
    assert body["artifacts_seen"] >= 1
    assert body["resolved"] is True


# --------------------------------------------------------------------------- #
# 2. Recovery
# --------------------------------------------------------------------------- #
def test_recover_existing_run_completes_the_backend_record(client):
    case_id, item, run = _failed_run(client)
    FakeClient.last_inputs = {"item_ref": item["id"]}

    response = client.post(
        f"/api/research/{run.id}/recover", json={"creao_run_id": CREAO_RUN}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["run"]["status"] == "complete"
    assert body["run"]["agent_run_id"] == CREAO_RUN
    assert body["run"]["candidates_valid"] == 1
    assert body["run"]["queries_run"] == ["strandmon wing chair"]
    assert body["run"]["limitations"]
    assert body["already_recovered"] is False
    assert body["candidates"][0]["product_name"] == "STRANDMON wing chair"
    # No new CREAO run was requested.
    assert FakeClient.create_calls == 0


def test_repeated_recovery_is_idempotent(client):
    _, item, run = _failed_run(client)
    FakeClient.last_inputs = {"item_ref": item["id"]}

    first = client.post(f"/api/research/{run.id}/recover", json={"creao_run_id": CREAO_RUN}).json()
    second = client.post(f"/api/research/{run.id}/recover", json={"creao_run_id": CREAO_RUN}).json()

    assert first["run"]["candidates_valid"] == 1
    assert second["already_recovered"] is True
    assert len(second["candidates"]) == 1
    assert len([c for c in store.research_candidates.values() if c.run_id == run.id]) == 1
    assert FakeClient.create_calls == 0


def test_recovery_accepts_a_valid_empty_result(client):
    _, item, run = _failed_run(client)
    FakeClient.body = json.dumps({
        "item_ref": item["id"], "candidates": [], "queries_run": [], "limitations": ["no match"]
    }).encode()

    body = client.post(f"/api/research/{run.id}/recover", json={"creao_run_id": CREAO_RUN}).json()
    assert body["run"]["status"] == "complete"
    assert body["candidates"] == []
    assert body["run"]["candidates_valid"] == 0


def test_recovery_rejects_wrong_item_ref_and_leaves_run_failed(client):
    _, item, run = _failed_run(client)
    FakeClient.body = json.dumps({
        "item_ref": "itm_some_other_item", "candidates": [copy.deepcopy(CANDIDATE)],
        "queries_run": [], "limitations": [],
    }).encode()

    response = client.post(f"/api/research/{run.id}/recover", json={"creao_run_id": CREAO_RUN})
    assert response.status_code == 502
    assert "item_ref mismatch" in response.json()["detail"]["message"]
    assert store.research_runs[run.id].status == InvestigationStatus.failed
    assert store.research_candidates == {}


def test_recovery_refuses_an_incomplete_creao_run(client):
    FakeClient.run_status = "running"
    _, _, run = _failed_run(client)

    response = client.post(f"/api/research/{run.id}/recover", json={"creao_run_id": CREAO_RUN})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "RESEARCH_RUN_NOT_COMPLETED"
    assert FakeClient.create_calls == 0


def test_recovery_unknown_backend_record_is_404(client):
    response = client.post("/api/research/rsch_missing/recover", json={"creao_run_id": CREAO_RUN})
    assert response.status_code == 404


def test_recovery_leaves_inventory_untouched(client):
    case_id, item, run = _failed_run(client)
    before = client.get(f"/api/items/{item['id']}").json()
    FakeClient.last_inputs = {"item_ref": item["id"]}

    client.post(f"/api/research/{run.id}/recover", json={"creao_run_id": CREAO_RUN})

    after = client.get(f"/api/items/{item['id']}").json()
    for field in ("brand", "model", "purchase_price", "currency", "estimated_value",
                  "status", "revision", "user_confirmed_fields"):
        assert after[field] == before[field], f"recovery changed {field}"


def test_recovery_uses_the_research_credential(client):
    _, item, run = _failed_run(client)
    FakeClient.last_inputs = {"item_ref": item["id"]}
    client.post(f"/api/research/{run.id}/recover", json={"creao_run_id": CREAO_RUN})

    used = FakeClient.instances[-1]
    assert used.api_key == "capi_research"
    assert used.app_id == RESEARCH_APP


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run_fetch(item_id: str, body_override: bytes | None = None):
    """Drive the service directly (no HTTP) to inspect probes."""
    import asyncio

    if body_override is not None:
        FakeClient.body = body_override
    FakeClient.last_inputs = {"item_ref": item_id}

    async def go():
        async with researcher.client() as api:
            final = await api.get_run(CREAO_RUN)
            return await researcher.fetch_payload(api, final)

    return asyncio.run(go())


# --------------------------------------------------------------------------- #
# 3. The real live document (unmodified fixture)
# --------------------------------------------------------------------------- #
LIVE_FIXTURE = Path(__file__).parent / "fixtures" / "research-candidates-live.json"
LIVE_ITEM = "itm_995ed6391131"


def test_live_document_validates_against_the_contract():
    payload = json.loads(LIVE_FIXTURE.read_text())

    # The fixture is used exactly as received.
    assert payload["item_ref"] == LIVE_ITEM
    assert len(payload["candidates"]) == 2

    researcher.validate_result(payload, expected_item_ref=LIVE_ITEM)   # must not raise


def test_live_document_round_trips_through_recovery(client):
    raw = LIVE_FIXTURE.read_bytes()
    original = json.loads(raw)

    case_id = client.post("/api/cases", json={"title": "Live run"}).json()["id"]
    item = client.post(
        f"/api/cases/{case_id}/items",
        json={"name": "Beige roll-arm armchair", "condition": "rolled arms, loose cushions",
              "editor": "priya"},
    ).json()
    run = ResearchRun(
        id="rsch_3491e9cce4aa",
        case_id=case_id,
        item_id=LIVE_ITEM,                     # matches the document's item_ref
        status=InvestigationStatus.failed,
        agent_run_id=CREAO_RUN,
        error="RESEARCH_INVALID_RESULT: no readable research-candidates.json was found",
    )
    store.research_runs[run.id] = run
    FakeClient.body = raw

    response = client.post(f"/api/research/{run.id}/recover", json={"creao_run_id": CREAO_RUN})
    assert response.status_code == 200, response.text
    body = response.json()

    # --- status + candidates -------------------------------------------------
    assert body["run"]["status"] == "complete"
    assert body["run"]["candidates_valid"] == 2
    assert body["run"]["agent_run_id"] == CREAO_RUN
    assert len(body["candidates"]) == 2
    assert {c["candidate_id"] for c in body["candidates"]} == {"CAND-1", "CAND-2"}
    assert all(c["status"] == "unverified_possible_match" for c in body["candidates"])
    assert all(c["unverified"] is True for c in body["candidates"])

    # --- values preserved exactly -------------------------------------------
    for candidate, source in zip(body["candidates"], original["candidates"]):
        assert candidate["product_name"] == source["product_name"]
        assert candidate["product_url"] == source["product_url"]
        assert candidate["brand"] == source["brand"]
        assert candidate["model"] == source["model"]
        assert candidate["matching_features"] == source["matching_features"]
        assert candidate["differing_features"] == source["differing_features"]
        assert candidate["missing_verification"] == source["missing_verification"]
        assert candidate["price_as_displayed"] == source["price_as_displayed"]
        assert candidate["source_accessed_at"] == source["source_accessed_at"]
        # Null currency is preserved, not defaulted.
        assert candidate["currency"] is None

    # --- queries_run / limitations preserved (objects, not stringified) -----
    assert body["run"]["queries_run"] == original["queries_run"]
    assert isinstance(body["run"]["queries_run"][0], dict)
    assert body["run"]["limitations"] == original["limitations"]

    # --- retrievable from the canonical GET ---------------------------------
    fetched = client.get(f"/api/research/{run.id}").json()
    assert fetched["run"] == body["run"]
    assert fetched["candidates"] == body["candidates"]

    # --- no CREAO run was created ------------------------------------------
    assert FakeClient.create_calls == 0


def test_live_document_recovery_is_idempotent(client):
    raw = LIVE_FIXTURE.read_bytes()
    case_id = client.post("/api/cases", json={"title": "Live run"}).json()["id"]
    run = ResearchRun(
        id="rsch_3491e9cce4aa", case_id=case_id, item_id=LIVE_ITEM,
        status=InvestigationStatus.failed, agent_run_id=CREAO_RUN,
    )
    store.research_runs[run.id] = run
    FakeClient.body = raw

    first = client.post(f"/api/research/{run.id}/recover", json={"creao_run_id": CREAO_RUN}).json()
    second = client.post(f"/api/research/{run.id}/recover", json={"creao_run_id": CREAO_RUN}).json()

    assert second["already_recovered"] is True
    stored = [c for c in store.research_candidates.values() if c.run_id == run.id]
    assert len(stored) == 2, "recovery duplicated candidates"
    assert len(first["candidates"]) == len(second["candidates"]) == 2
    assert FakeClient.create_calls == 0


def test_live_document_does_not_touch_inventory(client):
    raw = LIVE_FIXTURE.read_bytes()
    case_id = client.post("/api/cases", json={"title": "Live run"}).json()["id"]
    item = client.post(
        f"/api/cases/{case_id}/items",
        json={"name": "Beige armchair", "editor": "priya"},
    ).json()
    before = client.get(f"/api/items/{item['id']}").json()

    run = ResearchRun(
        id="rsch_3491e9cce4aa", case_id=case_id, item_id=LIVE_ITEM,
        status=InvestigationStatus.failed, agent_run_id=CREAO_RUN,
    )
    store.research_runs[run.id] = run
    FakeClient.body = raw
    client.post(f"/api/research/{run.id}/recover", json={"creao_run_id": CREAO_RUN})

    after = client.get(f"/api/items/{item['id']}").json()
    for field in ("brand", "model", "purchase_price", "currency", "estimated_value",
                  "status", "revision", "user_confirmed_fields", "matching_features"):
        if field in before:
            assert after[field] == before[field]
    for field in ("brand", "model", "purchase_price", "currency", "estimated_value", "status", "revision"):
        assert after[field] == before[field]
