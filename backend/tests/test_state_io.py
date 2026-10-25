"""Snapshot export/import: original IDs, integrity, conflicts, idempotency.

Uses a hand-built snapshot containing the real identifiers from Person 1's
server (item itm_995ed6391131, research rsch_3491e9cce4aa, CREAO run
11626040-0ac3-44db-84cd-59c6f22279cc) plus a human-corrected item.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import InvestigationStatus
from app.services import state_io
from app.store import store

ITEM_ID = "itm_995ed6391131"
RESEARCH_ID = "rsch_3491e9cce4aa"
CREAO_RUN = "11626040-0ac3-44db-84cd-59c6f22279cc"
CASE_ID = "case_18b532045bc7"
UPLOAD_ID = "upl_e290d454b817"
INVESTIGATION_ID = "inv_b58112068a26"

FIXTURES = Path(__file__).parent / "fixtures"
LIVE_CANDIDATES = json.loads((FIXTURES / "research-candidates-live.json").read_text())


def _snapshot() -> dict:
    return {
        "snapshot_version": 1,
        "exported_at": "2026-10-24T22:00:00+00:00",
        "cases": [
            {
                "id": CASE_ID, "title": "42 Willow Creek Rd", "address": "Asheville, NC",
                "disaster_type": "flood", "status": "review", "notes": None,
                "created_by": "priya", "members": [], "upload_count": 1, "item_count": 2,
                "created_at": "2026-10-24T05:49:00+00:00",
                "updated_at": "2026-10-24T05:56:00+00:00",
            }
        ],
        "uploads": [
            {
                "id": UPLOAD_ID, "case_id": CASE_ID, "kind": "photo",
                "original_filename": "01_room_armchair.jpg", "content_type": "image/jpeg",
                "size_bytes": 37170, "sha256": "abc", "uploaded_by": "priya",
                "evidence_id": "ev_001", "url": f"/api/uploads/{UPLOAD_ID}/file",
                "created_at": "2026-10-24T05:49:10+00:00",
            }
        ],
        "investigations": [
            {
                "id": INVESTIGATION_ID, "case_id": CASE_ID, "upload_id": UPLOAD_ID,
                "status": "complete", "mode": "live", "agent_run_id": "56821244-bbbf",
                "conversation_id": "d444d6a7", "model": None, "credits_used": None,
                "evidence_transport": "demo_url", "evidence_ids": ["ev_001"],
                "summary": "8 belongings", "error": None, "recovered": False,
                "follow_up": False, "review_answers_sent": 0, "items_created": 8,
                "items_reconciled": 0, "items_flagged": 0, "item_ids": [ITEM_ID],
                "requested_by": "priya", "created_at": "2026-10-24T05:49:19+00:00",
                "finished_at": "2026-10-24T05:51:00+00:00",
            }
        ],
        "items": [
            {
                "id": ITEM_ID, "case_id": CASE_ID, "investigation_id": INVESTIGATION_ID,
                "upload_id": UPLOAD_ID, "agent_item_id": "itm_001",
                "room": "Living room", "name": "Beige roll-arm armchair",
                "category": "other", "description": None, "condition": "rolled arms",
                "damage_level": "unknown", "confidence": 0.0, "estimated_value": 425.0,
                "currency": None, "bbox": None, "agent_reasoning": None,
                "status": "human_corrected", "agent_item_id": "itm_001",
                "supporting_evidence_ids": ["ev_001"], "review_status": "needs_review",
                "reconciliation": "new", "possible_match_item_id": None,
                "agent_proposal": None, "brand": "Renter-confirmed", "model": None,
                "purchase_date": None, "purchase_price": None,
                "location_in_image": "right foreground",
                "visible_observations": "rolled arms, loose cushions",
                "user_confirmed_fields": ["estimated_value"],
                "revision": 2, "created_at": "2026-10-24T05:51:00+00:00",
                "updated_at": "2026-10-24T06:10:00+00:00", "updated_by": "priya",
            },
            {
                "id": "itm_second", "case_id": CASE_ID, "investigation_id": INVESTIGATION_ID,
                "upload_id": UPLOAD_ID, "agent_item_id": "itm_002", "room": "Living room",
                "name": "Pendant lamp", "category": "other", "description": None,
                "condition": None, "damage_level": "unknown", "confidence": 0.0,
                "estimated_value": None, "currency": None, "bbox": None,
                "agent_reasoning": None, "status": "ai_suggested",
                "supporting_evidence_ids": ["ev_001"], "review_status": "needs_review",
                "reconciliation": "new", "possible_match_item_id": None,
                "agent_proposal": None, "brand": None, "model": None,
                "purchase_date": None, "purchase_price": None,
                "location_in_image": None, "visible_observations": None,
                "user_confirmed_fields": [], "revision": 1,
                "created_at": "2026-10-24T05:51:00+00:00",
                "updated_at": "2026-10-24T05:51:00+00:00", "updated_by": None,
            },
        ],
        "revisions": [
            {
                "id": "rev_1", "item_id": ITEM_ID, "editor": "priya",
                "note": "verified on site",
                "changes": {"estimated_value": 425.0, "condition": "rolled arms"},
                "created_at": "2026-10-24T06:10:00+00:00",
            }
        ],
        "comments": [],
        "findings": [
            {
                "id": f"{CASE_ID}:fnd_001", "case_id": CASE_ID,
                "investigation_id": INVESTIGATION_ID, "finding_id": "fnd_001",
                "finding_type": "possible_duplicate", "item_ids": ["itm_001"],
                "evidence_ids": ["ev_001"], "explanation": "same chair twice",
                "status": "needs_review", "resolution": "confirmed",
                "resolution_reason": "both frames match", "resolved_by": "priya",
                "resolved_at": "2026-10-24T06:20:00+00:00", "revision": 1,
            }
        ],
        "questions": [
            {
                "id": f"{CASE_ID}:q_001", "case_id": CASE_ID,
                "investigation_id": INVESTIGATION_ID, "question_id": "q_001",
                "item_id": ITEM_ID, "question": "Is the armchair yours?",
                "reason": "ownership", "evidence_ids": ["ev_001"], "status": "answered",
                "resolution_reason": None, "answer": "Yes, mine.",
                "answered_by": "priya", "answered_at": "2026-10-24T06:15:00+00:00",
                "revision": 1,
            }
        ],
        "decision_events": [
            {
                "id": "dec_1", "case_id": CASE_ID, "target_type": "question",
                "target_id": f"{CASE_ID}:q_001", "agent_ref": "q_001", "revision": 1,
                "value": "Yes, mine.", "reviewer": "priya", "note": None,
                "created_at": "2026-10-24T06:15:00+00:00",
            }
        ],
        "research_runs": [
            {
                "id": RESEARCH_ID, "case_id": CASE_ID, "item_id": ITEM_ID,
                "status": "failed", "mode": "live",
                "app_id": "a7260cbc-8253-4fa7-88d3-c644267e3221",
                "agent_run_id": CREAO_RUN, "thread_id": None,
                "error": "RESEARCH_INVALID_RESULT: no readable research-candidates.json was found",
                "region_currency": None, "candidate_ids": [], "candidates_valid": 0,
                "queries_run": [], "limitations": [], "input_summary": {},
                "requested_by": "priya", "created_at": "2026-10-24T21:10:00+00:00",
                "finished_at": "2026-10-24T21:11:00+00:00",
            }
        ],
        "research_candidates": [],
        "warnings": [],
    }


@pytest.fixture(autouse=True)
def clean():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def test_export_round_trips_ids_and_history(client):
    state_io.import_snapshot(_snapshot())
    exported = client.get("/api/admin/export").json()

    assert exported["cases"][0]["id"] == CASE_ID
    assert {i["id"] for i in exported["items"]} == {ITEM_ID, "itm_second"}
    assert exported["research_runs"][0]["id"] == RESEARCH_ID
    assert exported["research_runs"][0]["agent_run_id"] == CREAO_RUN
    assert exported["questions"][0]["answer"] == "Yes, mine."
    assert exported["findings"][0]["resolution"] == "confirmed"
    assert exported["decision_events"]
    # Excluded internal fields never appear.
    assert "result_payload" not in exported["investigations"][0]
    assert "agent_record" not in exported["items"][0]
    assert "stored_path" not in exported["uploads"][0]


# --------------------------------------------------------------------------- #
# Import: IDs preserved
# --------------------------------------------------------------------------- #
def test_import_preserves_every_original_id(client):
    report = state_io.import_snapshot(_snapshot())
    assert report["imported"]["cases"] == 1

    assert store.cases[CASE_ID].title == "42 Willow Creek Rd"
    assert store.items[ITEM_ID].name == "Beige roll-arm armchair"
    assert store.items[ITEM_ID].revision == 2
    assert store.items[ITEM_ID].estimated_value == 425.0
    assert store.uploads[UPLOAD_ID].evidence_id == "ev_001"
    assert store.investigations[INVESTIGATION_ID].agent_run_id == "56821244-bbbf"
    assert store.research_runs[RESEARCH_ID].agent_run_id == CREAO_RUN
    assert store.questions[f"{CASE_ID}:q_001"].answer == "Yes, mine."
    assert store.findings[f"{CASE_ID}:fnd_001"].resolution == "confirmed"
    assert store.revisions["rev_1"].editor == "priya"


def test_imported_state_is_served_by_the_normal_endpoints(client):
    state_io.import_snapshot(_snapshot())

    review = client.get(f"/api/cases/{CASE_ID}/review").json()
    assert review["items"][0]["id"] == ITEM_ID
    assert review["questions"][0]["answer"] == "Yes, mine."
    assert review["findings"][0]["resolution"] == "confirmed"

    research = client.get(f"/api/items/{ITEM_ID}/research").json()
    assert research[0]["id"] == RESEARCH_ID
    assert research[0]["agent_run_id"] == CREAO_RUN


def test_import_is_idempotent(client):
    first = state_io.import_snapshot(_snapshot())
    second = state_io.import_snapshot(_snapshot(), on_conflict="skip")

    assert first["conflicts"] == []
    assert second["conflicts"] == []
    assert len(store.items) == 2
    assert len(store.research_runs) == 1
    assert len(store.decision_events) == 1


# --------------------------------------------------------------------------- #
# Validation / conflicts
# --------------------------------------------------------------------------- #
def test_inconsistent_snapshot_is_rejected_without_writing(client):
    broken = _snapshot()
    broken["research_runs"][0]["item_id"] = "itm_does_not_exist"

    with pytest.raises(state_io.SnapshotError) as exc:
        state_io.import_snapshot(broken)

    assert exc.value.code == "SNAPSHOT_INCONSISTENT"
    assert "refusing to rewrite item_ref" in exc.value.message
    assert store.cases == {}


def test_invalid_record_shape_is_rejected(client):
    broken = _snapshot()
    broken["items"][0]["status"] = "not_a_status"

    with pytest.raises(state_io.SnapshotError) as exc:
        state_io.import_snapshot(broken)
    assert exc.value.code == "SNAPSHOT_INVALID"
    assert store.items == {}


def test_conflict_keeps_human_decisions_and_imports_nothing(client):
    state_io.import_snapshot(_snapshot())

    # Someone imported a stale copy: the human-corrected item differs.
    incoming = copy.deepcopy(_snapshot())
    incoming["items"][0]["estimated_value"] = 100.0
    incoming["items"][0]["revision"] = 1
    incoming["items"][0]["status"] = "ai_suggested"

    with pytest.raises(state_io.SnapshotError) as exc:
        state_io.import_snapshot(incoming)

    assert exc.value.code == "SNAPSHOT_CONFLICT"
    assert exc.value.conflicts[0]["collection"] == "items"
    # Original human decision is intact.
    assert store.items[ITEM_ID].estimated_value == 425.0
    assert store.items[ITEM_ID].revision == 2


def test_conflict_skip_mode_still_protects_human_values(client):
    state_io.import_snapshot(_snapshot())

    incoming = copy.deepcopy(_snapshot())
    incoming["items"][0]["estimated_value"] = 50.0
    incoming["items"][0]["status"] = "ai_suggested"
    report = state_io.import_snapshot(incoming, on_conflict="skip")

    assert report["conflicts"]
    assert store.items[ITEM_ID].estimated_value == 425.0
    assert store.items[ITEM_ID].status.value == "human_corrected"


def test_import_never_calls_creao(client, monkeypatch):
    calls: list[str] = []

    class Boom:
        def __init__(self, *a, **k):
            calls.append("client")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def create_run(self, *a, **k):
            raise AssertionError("import must never create a CREAO run")

    monkeypatch.setattr("app.services.creao.CreaoAppClient", Boom)
    state_io.import_snapshot(_snapshot())
    assert calls == []


# --------------------------------------------------------------------------- #
# The research record is usable after import
# --------------------------------------------------------------------------- #
def test_imported_research_record_can_be_recovered(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "creao_mode", "live")
    monkeypatch.setattr(settings, "creao_research_api_key", "capi_research")
    monkeypatch.setattr(settings, "creao_research_app_id", "a7260cbc-8253-4fa7-88d3-c644267e3221")
    monkeypatch.setattr(settings, "store_backend", "memory")
    state_io.import_snapshot(_snapshot())

    # Recover uses the already-completed CREAO run; the fake returns the live document.
    from tests.test_research_recovery import FakeClient

    monkeypatch.setattr("app.services.creao.CreaoAppClient", FakeClient)
    FakeClient.create_calls = 0
    FakeClient.body = json.dumps(LIVE_CANDIDATES).encode()
    FakeClient.containers = {
        "artifacts": [
            {"id": "workspaces/x/files/research-candidates.json", "title": "Outputs",
             "type": "application/octet-stream",
             "url": "https://agent.creao.ai/api/v1/runs/x/artifacts/download?artifactId=c"}
        ]
    }

    response = client.post(f"/api/research/{RESEARCH_ID}/recover", json={"creao_run_id": CREAO_RUN})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["run"]["status"] == "complete"
    assert body["run"]["candidates_valid"] == 2
    assert {c["candidate_id"] for c in body["candidates"]} == {"CAND-1", "CAND-2"}
    assert FakeClient.create_calls == 0, "recovery must not launch a run"

    # Inventory untouched by the import + recovery.
    item = client.get(f"/api/items/{ITEM_ID}").json()
    assert item["estimated_value"] == 425.0
    assert item["revision"] == 2
    assert item["brand"] == "Renter-confirmed"
    assert item["currency"] is None
