"""Question answers and finding resolutions.

The agent output is seeded straight into the store (a Question and a Finding
exactly as the importer writes them) so these tests stay focused on the decision
layer and don't touch CREAO.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Finding, Question
from app.store import store

CASE = "case_dec_test"


@pytest.fixture(autouse=True)
def seeded():
    store.reset()
    store.cases[CASE] = __import__("app.models", fromlist=["Case"]).Case(
        id=CASE, title="Decision test", status="review"
    )
    store.questions["inv_1:q_001"] = Question(
        id="inv_1:q_001",
        case_id=CASE,
        investigation_id="inv_1",
        question_id="q_001",
        item_id="itm_001",
        question="Is the brown armchair yours and was it in the room when the water entered?",
        reason="Ownership affects the claim.",
        evidence_ids=["ev_001", "ev_002"],
        status="open",
        revision=0,
    )
    store.questions["inv_1:q_002"] = Question(
        id="inv_1:q_002",
        case_id=CASE,
        investigation_id="inv_1",
        question_id="q_002",
        question="Can you add a photo of the armchair base?",
        status="open",
        revision=0,
    )
    store.findings["inv_1:fnd_001"] = Finding(
        id="inv_1:fnd_001",
        case_id=CASE,
        investigation_id="inv_1",
        finding_id="fnd_001",
        finding_type="possible_duplicate",
        item_ids=["itm_001"],
        evidence_ids=["ev_001", "ev_002"],
        explanation="The armchair appears in two files; counted once from ev_001.",
        status="needs_review",
        revision=0,
    )
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _answer(client, ref, answer, revision, reviewer="priya", case=CASE):
    return client.patch(
        f"/api/cases/{case}/questions/{ref}",
        json={"answer": answer, "reviewer": reviewer, "if_revision": revision},
    )


def _resolve(client, ref, resolution, reason, revision, reviewer="priya", case=CASE):
    return client.patch(
        f"/api/cases/{case}/findings/{ref}",
        json={
            "resolution": resolution,
            "reason": reason,
            "reviewer": reviewer,
            "if_revision": revision,
        },
    )


# --------------------------------------------------------------------------- #
# Questions
# --------------------------------------------------------------------------- #
def test_save_question_answer(client):
    response = _answer(client, "q_001", "Yes, mine — it was in the room.", 0)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["answer"] == "Yes, mine — it was in the room."
    assert body["answered_by"] == "priya"
    assert body["answered_at"]
    assert body["revision"] == 1
    assert body["status"] == "answered"

    # The agent's wording and references are untouched.
    assert body["question"].startswith("Is the brown armchair yours")
    assert body["evidence_ids"] == ["ev_001", "ev_002"]
    assert body["question_id"] == "q_001"


def test_update_existing_answer(client):
    _answer(client, "q_001", "First answer", 0)
    updated = _answer(client, "q_001", "Corrected answer", 1, reviewer="marcus")

    body = updated.json()
    assert body["answer"] == "Corrected answer"
    assert body["answered_by"] == "marcus"
    assert body["revision"] == 2

    history = client.get(f"/api/cases/{CASE}/review").json()["question_decisions"]
    mine = [d for d in history if d["target_id"] == "inv_1:q_001"]
    assert [d["revision"] for d in mine] == [1, 2]
    assert [d["value"] for d in mine] == ["First answer", "Corrected answer"]


def test_clear_an_answer(client):
    _answer(client, "q_001", "Yes, mine.", 0)
    cleared = _answer(client, "q_001", None, 1)

    body = cleared.json()
    assert body["answer"] is None
    assert body["answered_by"] is None
    assert body["answered_at"] is None
    assert body["status"] == "open", "a cleared question is not answered"
    assert body["revision"] == 2


def test_blank_answer_does_not_mark_answered(client):
    body = _answer(client, "q_002", "   ", 0).json()
    assert body["status"] == "open"
    assert body["answer"] is None


def test_question_answer_does_not_touch_items(client):
    from app.models import EvidenceItem

    item = EvidenceItem(
        id="itm_x", case_id=CASE, name="Armchair", agent_item_id="itm_001", estimated_value=None
    )
    store.items[item.id] = item

    _answer(client, "q_001", "Yes, mine.", 0)

    stored = store.items["itm_x"]
    assert stored.status.value == "ai_suggested"
    assert stored.revision == 1
    assert stored.updated_by is None
    assert stored.user_confirmed_fields == []


def test_question_revision_conflict_returns_current(client):
    _answer(client, "q_001", "First", 0)

    stale = _answer(client, "q_001", "Second", 0)
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "REVISION_CONFLICT"
    assert detail["current"]["answer"] == "First"
    assert detail["current"]["revision"] == 1

    # And nothing was written.
    assert store.questions["inv_1:q_001"].answer == "First"


def test_unknown_question_and_case_return_404(client):
    assert _answer(client, "q_999", "x", 0).status_code == 404
    assert _answer(client, "q_999", "x", 0, case="case_missing").status_code == 404

    missing_question = _answer(client, "q_999", "x", 0)
    assert missing_question.json()["detail"]["code"] == "QUESTION_NOT_FOUND"


def test_answer_by_backend_id_also_works(client):
    response = _answer(client, "inv_1:q_001", "Answered by id", 0)
    assert response.status_code == 200
    assert response.json()["question_id"] == "q_001"


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "resolution",
    ["confirmed", "needs_more_evidence", "unresolved"],
)
def test_each_finding_resolution(client, resolution):
    response = _resolve(client, "fnd_001", resolution, "looks right", 0)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["resolution"] == resolution
    assert body["resolved_by"] == "priya"
    assert body["resolved_at"]
    assert body["revision"] == 1
    # Agent content preserved.
    assert body["finding_id"] == "fnd_001"
    assert body["finding_type"] == "possible_duplicate"
    assert body["evidence_ids"] == ["ev_001", "ev_002"]
    assert body["explanation"].startswith("The armchair appears in two files")


def test_rejected_resolution_requires_reason(client):
    response = _resolve(client, "fnd_001", "rejected", None, 0)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "REASON_REQUIRED"
    assert store.findings["inv_1:fnd_001"].resolution is None
    assert store.findings["inv_1:fnd_001"].revision == 0

    ok = _resolve(client, "fnd_001", "rejected", "Same chair, not a duplicate claim.", 0)
    assert ok.status_code == 200
    assert ok.json()["resolution_reason"] == "Same chair, not a duplicate claim."


def test_finding_resolution_preserves_history(client):
    _resolve(client, "fnd_001", "needs_more_evidence", "need a second angle", 0)
    _resolve(client, "fnd_001", "confirmed", "second photo received", 1)

    history = client.get(f"/api/cases/{CASE}/review").json()["finding_decisions"]
    mine = [d for d in history if d["target_id"] == "inv_1:fnd_001"]
    assert [d["value"] for d in mine] == ["needs_more_evidence", "confirmed"]
    assert [d["revision"] for d in mine] == [1, 2]


def test_finding_conflict_returns_current(client):
    _resolve(client, "fnd_001", "confirmed", "ok", 0)
    stale = _resolve(client, "fnd_001", "rejected", "no", 0)

    assert stale.status_code == 409
    assert stale.json()["detail"]["current"]["resolution"] == "confirmed"


def test_unknown_finding_returns_404(client):
    response = _resolve(client, "fnd_999", "confirmed", None, 0)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "FINDING_NOT_FOUND"


def test_finding_resolution_does_not_confirm_receipt_prices(client):
    """A duplicate finding decision must not touch items or receipt prices."""
    from app.models import EvidenceItem

    item = EvidenceItem(
        id="itm_y",
        case_id=CASE,
        name="Floor lamp",
        agent_item_id="itm_001",
        purchase_price="89.99",     # printed on a receipt, not a valuation
        currency=None,
        estimated_value=None,
        status=__import__("app.models", fromlist=["ItemStatus"]).ItemStatus.ai_suggested,
    )
    store.items[item.id] = item

    _resolve(client, "fnd_001", "confirmed", "duplicate confirmed", 0)

    stored = store.items["itm_y"]
    assert stored.status.value == "ai_suggested"
    assert stored.estimated_value is None
    assert stored.purchase_price == "89.99"
    assert stored.user_confirmed_fields == []
    assert stored.revision == 1


# --------------------------------------------------------------------------- #
# Review endpoint integration
# --------------------------------------------------------------------------- #
def test_review_exposes_decisions_and_stays_backwards_compatible(client):
    _answer(client, "q_001", "Yes, mine.", 0)
    _resolve(client, "fnd_001", "confirmed", "agreed", 0)

    body = client.get(f"/api/cases/{CASE}/review").json()

    # Pre-existing top-level keys are all still there.
    for key in ("case", "evidence", "items", "findings", "questions", "decisions", "stats", "source"):
        assert key in body, f"top-level field {key} disappeared"

    question = next(q for q in body["questions"] if q["question_id"] == "q_001")
    assert question["answer"] == "Yes, mine."
    assert question["answered_by"] == "priya"
    assert question["status"] == "answered"
    assert question["revision"] == 1
    assert question["evidence_ids"] == ["ev_001", "ev_002"]

    finding = next(f for f in body["findings"] if f["finding_id"] == "fnd_001")
    assert finding["resolution"] == "confirmed"
    assert finding["resolved_by"] == "priya"
    assert finding["revision"] == 1
    assert finding["evidence_ids"] == ["ev_001", "ev_002"]

    assert len(body["question_decisions"]) == 1
    assert len(body["finding_decisions"]) == 1
    assert body["question_decisions"][0]["agent_ref"] == "q_001"
    assert body["finding_decisions"][0]["agent_ref"] == "fnd_001"
