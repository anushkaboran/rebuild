"""GET /api/cases/{case_id}/review — review-ready inventory.

Uses Person 2's real captured result as the agent payload (tests/fixtures), so
the assertions run against the exact fields the CREAO investigator emits.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import creao
from app.store import store

FIXTURES = Path(__file__).parent / "fixtures"
POLL = json.loads((FIXTURES / "api-poll-response.json").read_text())
RESULT = json.loads((FIXTURES / "api-run-result.json").read_text())

DEMO = (
    ("01_room_armchair.jpg", "photo"),
    ("02_armchair_closeup.jpg", "photo"),
    ("04_receipt_lamp.png", "receipt"),
    ("05_receipt_groceries.png", "receipt"),
)

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "8900000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


class FakeClient:
    def __init__(self) -> None:
        self._polls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def create_run(self, inputs):
        return {"id": "run-review-1", "status": "pending", "appId": "app", "appName": "x"}

    async def get_run(self, run_id):
        self._polls += 1
        run = dict(POLL)
        run["id"] = run_id
        run["status"] = "running" if self._polls == 1 else "completed"
        return run

    async def poll_run(self, run_id):
        while True:
            run = await self.get_run(run_id)
            if run["status"] == "completed":
                return run

    async def download_artifact(self, url):
        return json.dumps(RESULT).encode()

    async def list_runs(self, limit=1):
        return {"runs": []}


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    store.reset()
    monkeypatch.setattr(creao, "CreaoAppClient", FakeClient)
    monkeypatch.setattr(creao.settings, "creao_mode", "live")
    monkeypatch.setattr(creao.settings, "creao_app_api_key", "capi_test")
    monkeypatch.setattr(creao.settings, "creao_app_id", "9819d9a5-89e0-4d62-a284-91bac67f8cd1")
    monkeypatch.setattr(creao.settings, "creao_app_base_url", "https://agent.creao.ai/api/v1")
    monkeypatch.setattr(creao.settings, "creao_evidence_transport", "demo_url")
    monkeypatch.setattr(creao.settings, "store_backend", "memory")
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _investigated_case(client) -> str:
    case_id = client.post("/api/cases", json={"title": "42 Willow Creek Rd"}).json()["id"]
    uploads = []
    for filename, kind in DEMO:
        uploads.append(
            client.post(
                f"/api/cases/{case_id}/uploads",
                files={"file": (filename, io.BytesIO(PNG_BYTES), "image/jpeg")},
                data={"kind": kind},
            ).json()["id"]
        )
    started = client.post(
        f"/api/cases/{case_id}/investigations",
        json={"upload_id": uploads[0], "upload_ids": uploads, "room_hint": "living room"},
    )
    assert started.status_code == 202, started.text
    investigation = client.get(f"/api/investigations/{started.json()['id']}").json()
    assert investigation["status"] == "complete", investigation
    return case_id


# --------------------------------------------------------------------------- #
def test_review_returns_the_complete_inventory(client):
    case_id = _investigated_case(client)

    review = client.get(f"/api/cases/{case_id}/review")
    assert review.status_code == 200, review.text
    body = review.json()

    assert body["case"]["id"] == case_id
    assert len(body["items"]) == 8
    assert len(body["evidence"]) == 4
    assert len(body["findings"]) == 6
    assert len(body["questions"]) == 6
    assert body["stats"]["total_items"] == 8
    assert body["source"] == "memory"

    item = body["items"][0]
    for field in (
        "id",
        "agent_item_id",
        "name",
        "brand",
        "model",
        "purchase_date",
        "purchase_price",
        "currency",
        "location_in_image",
        "visible_observations",
        "supporting_evidence_ids",
        "user_confirmed_fields",
        "review_status",
        "estimated_value",
        "revision",
    ):
        assert field in item, f"missing {field}"


def test_backend_id_and_agent_id_are_both_present(client):
    case_id = _investigated_case(client)
    body = client.get(f"/api/cases/{case_id}/review").json()

    stored = {i["agent_item_id"]: i["id"] for i in client.get(f"/api/cases/{case_id}/items").json()}
    item = body["items"][0]

    assert item["agent_item_id"] == "itm_001"
    assert item["id"] == stored["itm_001"], "review must expose the PATCH-able backend id"
    assert item["id"].startswith("itm_")


def test_agent_fields_come_through_verbatim(client):
    case_id = _investigated_case(client)
    body = client.get(f"/api/cases/{case_id}/review").json()
    item = body["items"][0]

    assert item["name"] == "Brown fabric armchair"
    assert item["location_in_image"].startswith("ev_001")
    assert "water line" in item["visible_observations"].lower() or "upholstery" in item["visible_observations"].lower()
    assert item["supporting_evidence_ids"] == ["ev_001", "ev_002"]
    assert item["review_status"] == "needs_review"
    assert item["revision"] == 1


def test_currency_is_null_when_the_agent_reported_none(client):
    case_id = _investigated_case(client)
    body = client.get(f"/api/cases/{case_id}/review").json()

    # The captured agent output has currency: null for every item.
    assert all(i["currency"] is None for i in body["items"])


def test_purchase_price_is_never_copied_into_estimated_value(client):
    case_id = _investigated_case(client)
    body = client.get(f"/api/cases/{case_id}/review").json()

    for item in body["items"]:
        assert item["purchase_price"] in (None, ""), "this run reported no prices"
        assert item["estimated_value"] is None, "no valuation was asserted by the agent"


def test_human_correction_is_visible_after_review(client):
    case_id = _investigated_case(client)
    before = client.get(f"/api/cases/{case_id}/review").json()
    target = before["items"][0]

    patched = client.patch(
        f"/api/items/{target['id']}",
        json={
            "estimated_value": 425.0,
            "condition": "Renter confirmed: base is cracked",
            "editor": "priya",
            "note": "verified on site",
            "if_revision": target["revision"],
        },
    )
    assert patched.status_code == 200, patched.text

    after = client.get(f"/api/cases/{case_id}/review").json()
    updated = next(i for i in after["items"] if i["id"] == target["id"])

    assert updated["estimated_value"] == 425.0
    assert updated["condition"] == "Renter confirmed: base is cracked"
    assert updated["status"] == "human_corrected"
    assert updated["revision"] == target["revision"] + 1
    assert updated["updated_by"] == "priya"

    # The decision is reported separately, with the fields that changed.
    decision = next(d for d in after["decisions"] if d["item_id"] == target["id"])
    assert decision["editor"] == "priya"
    assert decision["note"] == "verified on site"
    assert decision["changes"]["estimated_value"] == 425.0
    assert decision["agent_item_id"] == target["agent_item_id"]


def test_stale_correction_is_still_rejected(client):
    case_id = _investigated_case(client)
    item = client.get(f"/api/cases/{case_id}/review").json()["items"][0]

    first = client.patch(
        f"/api/items/{item['id']}",
        json={"estimated_value": 100.0, "editor": "priya", "if_revision": 1},
    )
    assert first.status_code == 200

    stale = client.patch(
        f"/api/items/{item['id']}",
        json={"estimated_value": 999.0, "editor": "marcus", "if_revision": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current"]["estimated_value"] == 100.0


def test_review_exposes_no_secrets_or_storage_paths(client):
    case_id = _investigated_case(client)
    body = client.get(f"/api/cases/{case_id}/review").json()
    blob = json.dumps(body)

    assert "stored_path" not in blob
    assert "backend/uploads" not in blob
    assert "api_key" not in blob.lower()
    assert "X-Amz" not in blob                    # no presigned artifact URLs
    assert "artifacts/download" not in blob
    assert "http://" not in blob and "https://" not in blob

    for evidence in body["evidence"]:
        assert evidence["url"].startswith("/api/uploads/")
        assert evidence["evidence_id"].startswith("ev_")


def test_review_404_for_unknown_case(client):
    assert client.get("/api/cases/case_missing/review").status_code == 404
