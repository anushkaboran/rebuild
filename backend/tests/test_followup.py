"""Follow-up investigations: review_answers, stable evidence IDs, reconciliation.

The CREAO client is faked with two scripted payloads (initial run, then a
follow-up that reuses one item id, renames another into a conflict, and adds a
genuinely new belonging). No network, no credits.
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
BASE = json.loads((FIXTURES / "api-run-result.json").read_text())

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "8900000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


def _item(item_id, name, evidence, **extra):
    return {
        "item_id": item_id,
        "name": name,
        "room": "Living room",
        "location_in_image": f"{evidence[0]}: somewhere",
        "visible_observations": "visible water line",
        "supporting_evidence_ids": evidence,
        "brand": None,
        "model": None,
        "purchase_date": None,
        "purchase_price": None,
        "currency": None,
        "proposed_receipt_evidence_id": None,
        "review_status": "needs_review",
        "user_confirmed_fields": [],
        **extra,
    }


def payload(items, *, evidence_count=1, question=None, finding=None):
    p = json.loads(json.dumps(BASE))
    p["items"] = items
    p["evidence"] = p["evidence"][:evidence_count]
    p["findings"] = [finding] if finding else []
    p["questions"] = [question] if question else []
    p["receipt_extracts"] = []
    return p


FIRST = payload(
    [
        _item("itm_001", "Brown fabric armchair", ["ev_001"]),
        _item("itm_002", "Pendant lamp", ["ev_001"]),
    ],
    question={
        "question_id": "q_001",
        "item_id": "itm_001",
        "question": "Is the brown armchair yours?",
        "reason": "Ownership affects the claim.",
        "status": "open",
        "resolution_reason": None,
    },
    finding={
        "finding_id": "fnd_001",
        "type": "possible_duplicate",
        "item_ids": ["itm_001"],
        "evidence_ids": ["ev_001"],
        "explanation": "Same chair in two frames.",
        "status": "needs_review",
        "resolution_reason": None,
    },
)

# Follow-up: itm_001 is the same chair (must reconcile), a renamed id refers to
# the same lamp (must be flagged, not merged), and itm_009 is genuinely new.
FOLLOW_UP = payload(
    [
        _item("itm_001", "Brown fabric armchair, water stained", ["ev_001"]),
        _item("itm_050", "Pendant lamp", ["ev_001"]),          # same name + evidence
        _item("itm_009", "Blue cotton cushion", ["ev_001"]),
        _item("itm_010", "Pendant lamp shade", ["ev_001"]),    # similar name, different object
    ],
    evidence_count=1,
    question={
        "question_id": "q_001",
        "item_id": "itm_001",
        "question": "Is the brown armchair yours? (updated wording)",
        "reason": "Ownership affects the claim.",
        "status": "open",
        "resolution_reason": None,
    },
    finding={
        "finding_id": "fnd_001",
        "type": "possible_duplicate",
        "item_ids": ["itm_001"],
        "evidence_ids": ["ev_001"],
        "explanation": "Same chair, updated explanation from the follow-up run.",
        "status": "needs_review",
        "resolution_reason": None,
    },
)


class ScriptedClient:
    """Returns FIRST on the first run, FOLLOW_UP on the next ones."""

    instances: list["ScriptedClient"] = []
    payloads: list[dict] = [FIRST, FOLLOW_UP]

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._polls = 0
        ScriptedClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def create_run(self, inputs):
        self.calls.append(inputs)
        return {"id": f"run-{len(ScriptedClient.instances)}", "status": "pending"}

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
        index = min(len(ScriptedClient.instances) - 1, len(ScriptedClient.payloads) - 1)
        return json.dumps(ScriptedClient.payloads[index]).encode()

    async def list_runs(self, limit=1):
        return {"runs": []}


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    store.reset()
    ScriptedClient.instances = []
    ScriptedClient.payloads = [FIRST, FOLLOW_UP]
    monkeypatch.setattr(creao, "CreaoAppClient", ScriptedClient)
    monkeypatch.setattr(creao.settings, "creao_mode", "live")
    monkeypatch.setattr(creao.settings, "creao_app_api_key", "capi_test")
    monkeypatch.setattr(creao.settings, "creao_app_id", "9819d9a5-89e0-4d62-a284-91bac67f8cd1")
    monkeypatch.setattr(creao.settings, "creao_app_base_url", "https://agent.creao.ai/api/v1")
    monkeypatch.setattr(creao.settings, "creao_evidence_transport", "demo_url")
    monkeypatch.setattr(creao.settings, "store_backend", "memory")
    # Tests use demo_url with placeholder filenames; skip the live URL probe.
    monkeypatch.setattr(creao.settings, "creao_verify_evidence_urls", False)
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _case(client, files=("room.jpg",)):
    case_id = client.post("/api/cases", json={"title": "Follow-up case"}).json()["id"]
    uploads = []
    for name in files:
        uploads.append(
            client.post(
                f"/api/cases/{case_id}/uploads",
                files={"file": (name, io.BytesIO(PNG_BYTES), "image/jpeg")},
                data={"kind": "photo"},
            ).json()["id"]
        )
    return case_id, uploads


def _investigate(client, case_id, uploads, room="Living room"):
    started = client.post(
        f"/api/cases/{case_id}/investigations",
        json={"upload_id": uploads[0], "upload_ids": uploads, "room_hint": room},
    )
    assert started.status_code == 202, started.text
    return client.get(f"/api/investigations/{started.json()['id']}").json()


# --------------------------------------------------------------------------- #
# 1. review_answers
# --------------------------------------------------------------------------- #
def test_review_answers_is_a_json_string_of_answered_questions_only(client):
    case_id, uploads = _case(client)
    _investigate(client, case_id, uploads)

    unanswered = client.get(f"/api/cases/{case_id}/review").json()["questions"][0]
    client.patch(
        f"/api/cases/{case_id}/questions/{unanswered['question_id']}",
        json={"answer": "Yes, mine.", "reviewer": "priya", "if_revision": 0},
    )

    _investigate(client, case_id, uploads)  # follow-up

    inputs = ScriptedClient.instances[-1].calls[-1]
    assert "review_answers" in inputs
    assert isinstance(inputs["review_answers"], str), "must be a JSON string, not an array"

    answers = json.loads(inputs["review_answers"])
    assert len(answers) == 1
    assert answers[0]["question_id"] == "q_001"
    assert answers[0]["question"].startswith("Is the brown armchair yours")
    assert answers[0]["answer"] == "Yes, mine."
    assert answers[0]["answered_by"] == "priya"
    assert answers[0]["answered_at"]

    # Answers are context, never an item confirmation.
    confirmed = json.loads(inputs["confirmed_items"])
    assert all(c.get("target_type") != "question" for c in confirmed)


def test_unanswered_questions_are_omitted(client):
    case_id, uploads = _case(client)
    _investigate(client, case_id, uploads)
    _investigate(client, case_id, uploads)

    inputs = ScriptedClient.instances[-1].calls[-1]
    assert json.loads(inputs["review_answers"]) == []


def test_answers_survive_a_follow_up_run(client):
    case_id, uploads = _case(client)
    _investigate(client, case_id, uploads)
    q = client.get(f"/api/cases/{case_id}/review").json()["questions"][0]
    client.patch(
        f"/api/cases/{case_id}/questions/{q['question_id']}",
        json={"answer": "Yes, mine.", "reviewer": "priya", "if_revision": 0},
    )

    _investigate(client, case_id, uploads)

    after = client.get(f"/api/cases/{case_id}/review").json()["questions"][0]
    assert after["answer"] == "Yes, mine."
    assert after["answered_by"] == "priya"
    assert after["revision"] == 1
    # Agent wording may be refreshed, the human answer is untouched.
    assert "updated wording" in after["question"]


# --------------------------------------------------------------------------- #
# 2. Stable evidence IDs
# --------------------------------------------------------------------------- #
def test_evidence_ids_are_stable_across_runs_and_new_evidence_gets_new_ids(client):
    case_id, uploads = _case(client)
    first = _investigate(client, case_id, uploads)

    assert first["evidence_ids"] == ["ev_001"]
    assert store.uploads[uploads[0]].evidence_id == "ev_001"

    extra = client.post(
        f"/api/cases/{case_id}/uploads",
        files={"file": ("kitchen.jpg", io.BytesIO(PNG_BYTES), "image/jpeg")},
        data={"kind": "photo"},
    ).json()["id"]

    second = _investigate(client, case_id, [uploads[0], extra])
    assert second["evidence_ids"] == ["ev_001", "ev_002"], "existing id must not shift"

    review = client.get(f"/api/cases/{case_id}/review").json()
    by_name = {e["filename"]: e["evidence_id"] for e in review["evidence"]}
    assert by_name["room.jpg"] == "ev_001"
    assert by_name["kitchen.jpg"] == "ev_002"


# --------------------------------------------------------------------------- #
# 3. Item reconciliation
# --------------------------------------------------------------------------- #
def test_same_belonging_is_reconciled_not_duplicated(client):
    case_id, uploads = _case(client)
    _investigate(client, case_id, uploads)
    before = {i["agent_item_id"]: i["id"] for i in client.get(f"/api/cases/{case_id}/items").json()}

    _investigate(client, case_id, uploads)  # follow-up repeats itm_001

    items = client.get(f"/api/cases/{case_id}/items").json()
    armchairs = [i for i in items if i["agent_item_id"] == "itm_001"]
    assert len(armchairs) == 1, "follow-up created a duplicate belonging"
    assert armchairs[0]["id"] == before["itm_001"], "backend id must be preserved"
    assert armchairs[0]["reconciliation"] == "matched"
    # Untouched by a human, so the refreshed agent wording is applied.
    assert "water stained" in armchairs[0]["name"]


def test_distinct_belongings_with_similar_names_stay_separate(client):
    case_id, uploads = _case(client)
    _investigate(client, case_id, uploads)
    _investigate(client, case_id, uploads)

    items = client.get(f"/api/cases/{case_id}/items").json()
    by_agent = {}
    for item in items:
        by_agent.setdefault(item["agent_item_id"], []).append(item)

    # The two genuinely different objects stay separate rows...
    assert len(by_agent["itm_002"]) == 1
    assert len(by_agent["itm_010"]) == 1
    assert by_agent["itm_002"][0]["id"] != by_agent["itm_010"][0]["id"]
    # ...and a similar name alone never merged or dropped either of them.
    assert by_agent["itm_010"][0]["reconciliation"] == "new"
    assert by_agent["itm_002"][0]["status"] == "ai_suggested"


def test_changed_agent_id_is_flagged_not_merged(client):
    case_id, uploads = _case(client)
    _investigate(client, case_id, uploads)
    _investigate(client, case_id, uploads)

    review = client.get(f"/api/cases/{case_id}/review").json()
    flagged = [i for i in review["items"] if i["reconciliation"] == "identity_conflict"]

    assert len(flagged) == 1
    assert flagged[0]["agent_item_id"] == "itm_050"
    assert flagged[0]["possible_match_item_id"], "must point at the suspected original"
    original = next(i for i in review["items"] if i["id"] == flagged[0]["possible_match_item_id"])
    assert original["agent_item_id"] == "itm_002"
    assert original["id"] != flagged[0]["id"], "never silently merged"


def test_retry_safe_import_creates_no_duplicates(client):
    case_id, uploads = _case(client)
    _investigate(client, case_id, uploads)
    _investigate(client, case_id, uploads)
    _investigate(client, case_id, uploads)  # third identical follow-up

    items = client.get(f"/api/cases/{case_id}/items").json()
    ids = [i["agent_item_id"] for i in items]
    assert len(ids) == len(set(ids)), f"duplicate agent items: {ids}"


# --------------------------------------------------------------------------- #
# 4. Human decisions survive follow-ups
# --------------------------------------------------------------------------- #
def test_human_item_correction_survives_follow_up(client):
    case_id, uploads = _case(client)
    _investigate(client, case_id, uploads)

    item = next(
        i for i in client.get(f"/api/cases/{case_id}/items").json() if i["agent_item_id"] == "itm_001"
    )
    client.patch(
        f"/api/items/{item['id']}",
        json={"estimated_value": 425.0, "condition": "confirmed cracked base",
              "editor": "priya", "if_revision": 1},
    )

    _investigate(client, case_id, uploads)

    after = next(
        i for i in client.get(f"/api/cases/{case_id}/items").json() if i["id"] == item["id"]
    )
    assert after["estimated_value"] == 425.0
    assert after["condition"] == "confirmed cracked base"
    assert after["revision"] == 2
    assert after["updated_by"] == "priya"
    assert after["status"] == "human_corrected"
    # The incoming AI wording is parked, not applied.
    assert after["agent_proposal"]["name"] == "Brown fabric armchair, water stained"


def test_finding_resolution_and_question_answer_survive_follow_up(client):
    case_id, uploads = _case(client)
    _investigate(client, case_id, uploads)

    review = client.get(f"/api/cases/{case_id}/review").json()
    client.patch(
        f"/api/cases/{case_id}/findings/{review['findings'][0]['finding_id']}",
        json={"resolution": "confirmed", "reason": "two frames, one chair",
              "reviewer": "priya", "if_revision": 0},
    )
    client.patch(
        f"/api/cases/{case_id}/questions/{review['questions'][0]['question_id']}",
        json={"answer": "Yes, mine.", "reviewer": "priya", "if_revision": 0},
    )

    _investigate(client, case_id, uploads)

    after = client.get(f"/api/cases/{case_id}/review").json()
    finding = after["findings"][0]
    assert finding["resolution"] == "confirmed"
    assert finding["resolution_reason"] == "two frames, one chair"
    assert finding["resolved_by"] == "priya"
    assert finding["revision"] == 1
    assert "updated explanation" in finding["explanation"]

    question = after["questions"][0]
    assert question["answer"] == "Yes, mine."
    assert question["revision"] == 1

    assert len(after["finding_decisions"]) == 1
    assert len(after["question_decisions"]) == 1


def test_follow_up_is_marked_and_counted(client):
    case_id, uploads = _case(client)
    first = _investigate(client, case_id, uploads)
    assert first["follow_up"] is False

    second = _investigate(client, case_id, uploads)
    assert second["follow_up"] is True
    assert second["items_reconciled"] >= 1
    assert second["items_flagged"] == 1
    assert second["items_created"] == 2  # itm_009 new + itm_050 flagged
    assert "item_ids" in second


def test_stale_revision_still_rejected_after_follow_up(client):
    case_id, uploads = _case(client)
    _investigate(client, case_id, uploads)
    q = client.get(f"/api/cases/{case_id}/review").json()["questions"][0]
    client.patch(
        f"/api/cases/{case_id}/questions/{q['question_id']}",
        json={"answer": "Yes", "reviewer": "priya", "if_revision": 0},
    )

    _investigate(client, case_id, uploads)

    stale = client.patch(
        f"/api/cases/{case_id}/questions/{q['question_id']}",
        json={"answer": "No", "reviewer": "marcus", "if_revision": 0},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current"]["answer"] == "Yes"
