"""Content-level assertions on the generated recovery PDF.

These parse the real PDF text, so a regression in valuation wording, currency
handling or the human-review section fails the build.
"""

from __future__ import annotations

import io

import pytest
from app.models import (
    Case,
    DamageLevel,
    EvidenceItem,
    Finding,
    Investigation,
    InvestigationStatus,
    ItemStatus,
    Question,
    Upload,
    UploadKind,
)
from app.services import claim, pdf


def _render(case, items, *, findings=(), questions=(), uploads=(), revisions=()):
    return pdf.build_case_pdf(
        case=case,
        items=items,
        uploads=uploads,
        investigations=[Investigation(id="inv_1", case_id=case.id, upload_id="upl_1",
                                      status=InvestigationStatus.complete)],
        revisions=revisions,
        findings=findings,
        questions=questions,
    )


def _text(blob: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(blob))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


CASE = Case(id="case_pdf", title="42 Willow Creek Rd", address="Asheville, NC")


def _item(**over) -> EvidenceItem:
    base = dict(
        id="itm_1", case_id=CASE.id, name="Armchair", room="living room",
        damage_level=DamageLevel.unknown, status=ItemStatus.ai_suggested,
        estimated_value=None, currency=None, revision=1,
    )
    base.update(over)
    return EvidenceItem(**base)


# --------------------------------------------------------------------------- #
# Valuation
# --------------------------------------------------------------------------- #
def test_all_values_unknown_says_not_estimated():
    blob = _render(CASE, [_item(), _item(id="itm_2", name="Rug")])
    text = _text(blob)

    assert "Not estimated" in text
    assert "Estimated item value" in text
    assert "Estimated replacement cost" not in text
    assert "USD 0" not in text


def test_unknown_currency_is_not_labelled_usd():
    blob = _render(CASE, [_item(estimated_value=425.0, currency=None)])
    text = _text(blob)

    assert "425" in text
    assert "USD 425" not in text


def test_partial_total_reports_unvalued_count():
    items = [
        _item(estimated_value=100.0, currency="USD"),
        _item(id="itm_2", name="Rug"),
        _item(id="itm_3", name="Lamp"),
    ]
    text = _text(_render(CASE, items))

    assert "partial" in text
    assert "2 of 3" in text
    assert "USD 100" in text


def test_multiple_currencies_are_never_combined():
    items = [
        _item(estimated_value=100.0, currency="USD"),
        _item(id="itm_2", name="Chair", estimated_value=200.0, currency="EUR"),
    ]
    text = _text(_render(CASE, items))

    assert "USD 100" in text and "EUR 200" in text
    assert "USD 300" not in text and "300" not in text.split("Estimated item value")[1][:80]


def test_no_coverage_or_payout_language():
    text = _text(_render(CASE, [_item(estimated_value=50.0, currency="USD")]))
    lowered = text.lower()
    # The disclaimer may *negate* these words; assert no affirmative claim.
    for phrase in ("is covered", "you are eligible", "eligible for", "payout of",
                   "will be reimbursed", "claim is approved"):
        assert phrase not in lowered


# --------------------------------------------------------------------------- #
# Human review information
# --------------------------------------------------------------------------- #
def test_question_answers_and_findings_are_shown():
    questions = [
        Question(id="c:q_001", case_id=CASE.id, question_id="q_001",
                 question="Is the armchair yours?", status="answered",
                 answer="Yes, mine.", answered_by="priya", revision=1),
    ]
    findings = [
        Finding(id="c:fnd_001", case_id=CASE.id, finding_id="fnd_001",
                finding_type="possible_duplicate", explanation="same chair",
                resolution="confirmed", resolution_reason="two frames match",
                resolved_by="priya", revision=1),
    ]
    text = _text(_render(CASE, [_item()], questions=questions, findings=findings))

    assert "Human review record" in text
    assert "Yes, mine." in text and "priya" in text
    assert "confirmed" in text and "two frames match" in text


def test_unanswered_questions_are_listed_as_unresolved():
    question = Question(id="c:q_002", case_id=CASE.id, question_id="q_002",
                        question="Can you add a photo of the base?", status="open")
    text = _text(_render(CASE, [_item()], questions=[question]))

    assert "Unresolved questions" in text
    assert "Can you add a photo of the base?" in text


def test_agent_observations_are_separated_from_human_decisions():
    item = _item(visible_observations="water line across the base",
                 status=ItemStatus.human_corrected, revision=2, updated_by="priya")
    text = _text(_render(CASE, [item]))

    assert "Agent observations" in text
    assert "not human-confirmed" in text
    assert "water line across the base" in text


def test_no_human_confirmations_is_stated_not_invented():
    text = _text(_render(CASE, [_item()], questions=[], findings=[]))
    assert "No question answers were recorded" in text
    assert "No finding decisions were recorded" in text


# --------------------------------------------------------------------------- #
# Researcher separation
# --------------------------------------------------------------------------- #
def test_unverified_researcher_data_never_appears():
    from app.store import store
    from app.models import ResearchCandidate

    store.reset()
    store.research_candidates["rc_1"] = ResearchCandidate(
        id="rc_1", run_id="rsch_1", case_id=CASE.id, item_id="itm_1",
        brand="IKEA", model="STRANDMON", product_name="STRANDMON wing chair",
        product_url="https://example.test/p", price_as_displayed="$349.00",
        currency="USD", status="unverified_possible_match",
    )
    try:
        text = _text(_render(CASE, [_item()]))
        for value in ("STRANDMON", "IKEA", "$349", "ikea"):
            assert value not in text, f"researcher data leaked into PDF: {value}"
    finally:
        store.reset()


# --------------------------------------------------------------------------- #
# Claim Assistant uses the same generator
# --------------------------------------------------------------------------- #
def test_claim_assistant_pdf_uses_the_same_output():
    from app.store import store

    store.reset()
    try:
        store.cases[CASE.id] = CASE
        store.items["itm_1"] = _item(estimated_value=425.0, currency=None)
        store.items["itm_2"] = _item(id="itm_2", name="Rug")   # no value
        blob = claim.build_recovery_pdf(CASE)
        text = _text(blob)

        assert blob.startswith(b"%PDF")
        assert "Estimated item value" in text
        assert "USD 425" not in text          # unknown currency stays unlabelled
        assert "425" in text
        assert "partial" in text              # one of two belongings unvalued
    finally:
        store.reset()
