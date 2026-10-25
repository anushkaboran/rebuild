"""Pydantic request / response models — the shared API contract.

Keep these in sync with docs/contract.md. The frontend imports the generated
OpenAPI schema from /openapi.json, so a change here is a change to the contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class UploadKind(str, Enum):
    photo = "photo"
    receipt = "receipt"


class CaseStatus(str, Enum):
    open = "open"
    investigating = "investigating"
    review = "review"
    exported = "exported"
    archived = "archived"


class InvestigationStatus(str, Enum):
    queued = "queued"
    running = "running"
    complete = "complete"
    failed = "failed"


class ItemStatus(str, Enum):
    ai_suggested = "ai_suggested"
    human_confirmed = "human_confirmed"
    human_corrected = "human_corrected"
    rejected = "rejected"


class DamageLevel(str, Enum):
    none = "none"
    light = "light"
    moderate = "moderate"
    severe = "severe"
    destroyed = "destroyed"
    unknown = "unknown"


# --------------------------------------------------------------------------- #
# Shared sub-objects
# --------------------------------------------------------------------------- #
class BBox(BaseModel):
    """Normalised 0..1 bounding box locating the item inside its photo."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(ge=0, le=1)
    h: float = Field(ge=0, le=1)


class Member(BaseModel):
    id: str
    display_name: str
    role: Literal["owner", "reviewer", "viewer"] = "reviewer"


class Revision(BaseModel):
    """One edit to an evidence item — the audit trail behind collaboration."""

    id: str
    item_id: str
    editor: str
    note: Optional[str] = None
    changes: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #
class CaseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    address: Optional[str] = Field(default=None, max_length=300)
    disaster_type: str = Field(default="flood", max_length=60)
    created_by: str = Field(default="anonymous", max_length=120)
    notes: Optional[str] = Field(default=None, max_length=2000)


class CaseUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    address: Optional[str] = Field(default=None, max_length=300)
    notes: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[CaseStatus] = None


class Case(BaseModel):
    id: str
    title: str
    address: Optional[str] = None
    disaster_type: str = "flood"
    status: CaseStatus = CaseStatus.open
    notes: Optional[str] = None
    created_by: str = "anonymous"
    members: list[Member] = Field(default_factory=list)
    upload_count: int = 0
    item_count: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CaseDetail(Case):
    uploads: list["Upload"] = Field(default_factory=list)
    items: list["EvidenceItem"] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Uploads
# --------------------------------------------------------------------------- #
class Upload(BaseModel):
    id: str
    case_id: str
    kind: UploadKind
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by: str = "anonymous"
    # Stable evidence id assigned once, on first upload (ev_001…). Follow-up
    # runs reuse it so manifests and result references stay comparable.
    evidence_id: Optional[str] = None
    url: str
    # Internal storage path — never serialised to clients.
    stored_path: str = Field(default="", exclude=True)
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Evidence items
# --------------------------------------------------------------------------- #
class EvidenceItem(BaseModel):
    """One belonging identified in the shared recovery record."""

    id: str
    case_id: str
    investigation_id: Optional[str] = None
    upload_id: Optional[str] = None
    room: Optional[str] = None
    name: str
    category: str = "other"
    description: Optional[str] = None
    condition: Optional[str] = None
    damage_level: DamageLevel = DamageLevel.unknown
    confidence: float = Field(default=0.0, ge=0, le=1)
    estimated_value: Optional[float] = None
    # Nullable on purpose: the agent often reports no currency, and inventing
    # "USD" would fabricate a fact. `estimated_value` stays a separate field and
    # is never derived from `purchase_price` (which is a printed receipt value).
    currency: Optional[str] = None
    bbox: Optional[BBox] = None
    agent_reasoning: Optional[str] = None
    status: ItemStatus = ItemStatus.ai_suggested
    # Agent-supplied traceability (app-scoped API Trigger / rebuild-result.json).
    agent_item_id: Optional[str] = None
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    review_status: Optional[str] = None
    # How this row got here: "new" (first proposal), "matched" (reconciled with
    # an existing belonging), "identity_conflict" (agent changed the item id but
    # the evidence suggests it may be the same object — needs a human).
    reconciliation: str = "new"
    possible_match_item_id: Optional[str] = None
    # Latest AI proposal when a follow-up run wanted to change a row that a
    # human already decided. Kept separate from the human-confirmed values.
    agent_proposal: Optional[dict[str, Any]] = None
    # Agent-reported identity fields, copied verbatim (never inferred).
    brand: Optional[str] = None
    model: Optional[str] = None
    purchase_date: Optional[str] = None
    purchase_price: Optional[str] = None
    location_in_image: Optional[str] = None
    visible_observations: Optional[str] = None
    user_confirmed_fields: list[str] = Field(default_factory=list)
    agent_record: Optional[dict[str, Any]] = Field(default=None, exclude=True)
    revision: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    updated_by: Optional[str] = None


class EvidenceItemPatch(BaseModel):
    """Human correction. Send only the fields being changed.

    `if_revision` enables optimistic concurrency: if another collaborator saved
    first, the API returns 409 with the current item instead of silently
    clobbering their edit.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    category: Optional[str] = Field(default=None, max_length=80)
    room: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None, max_length=2000)
    condition: Optional[str] = Field(default=None, max_length=1000)
    damage_level: Optional[DamageLevel] = None
    estimated_value: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    # Agent-reported identity fields, correctable by a human reviewer.
    brand: Optional[str] = Field(default=None, max_length=200)
    model: Optional[str] = Field(default=None, max_length=200)
    purchase_date: Optional[str] = Field(default=None, max_length=60)
    purchase_price: Optional[str] = Field(default=None, max_length=60)
    status: Optional[ItemStatus] = None
    if_revision: Optional[int] = Field(default=None, ge=1)
    editor: str = Field(default="anonymous", max_length=120)
    note: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v


class EvidenceItemCreate(BaseModel):
    """Manually add a belonging the agent missed."""

    name: str = Field(min_length=1, max_length=200)
    room: Optional[str] = Field(default=None, max_length=80)
    category: str = Field(default="other", max_length=80)
    description: Optional[str] = Field(default=None, max_length=2000)
    condition: Optional[str] = Field(default=None, max_length=1000)
    damage_level: DamageLevel = DamageLevel.unknown
    estimated_value: Optional[float] = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    editor: str = Field(default="anonymous", max_length=120)


# --------------------------------------------------------------------------- #
# Investigations
# --------------------------------------------------------------------------- #
class InvestigationRequest(BaseModel):
    upload_id: str
    # Optional: investigate several uploads together (a whole room's evidence).
    # Defaults to just `upload_id`. The backend assigns ev_### IDs in this order.
    upload_ids: Optional[list[str]] = None
    room_hint: Optional[str] = Field(default=None, max_length=80)
    instructions: Optional[str] = Field(default=None, max_length=2000)
    requested_by: str = Field(default="anonymous", max_length=120)


class Investigation(BaseModel):
    id: str
    case_id: str
    upload_id: str
    status: InvestigationStatus = InvestigationStatus.queued
    agent: str = "rebuild-evidence-investigator"
    # Which integration produced this run. `mock` results are simulated and must
    # be badged as such in the UI; they can never be presented as real findings.
    mode: Literal["mock", "live"] = "mock"
    agent_run_id: Optional[str] = None
    # App-scoped runs report `threadId`; mapped here for follow-up runs.
    conversation_id: Optional[str] = None
    model: Optional[str] = None
    credits_used: Optional[float] = None
    # What evidence was actually sent, so nothing claims more than was attached.
    evidence_transport: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)
    summary: Optional[str] = None
    error: Optional[str] = None
    # True when the result was imported from an already-completed CREAO run
    # rather than observed live.
    recovered: bool = False
    # Follow-up bookkeeping
    follow_up: bool = False
    review_answers_sent: int = 0
    items_created: int = 0
    items_reconciled: int = 0
    items_flagged: int = 0
    item_ids: list[str] = Field(default_factory=list)
    # Full validated rebuild-result.json, kept for follow-up runs. Never
    # serialised to clients (it can be large and is agent-owned).
    result_payload: Optional[dict[str, Any]] = Field(default=None, exclude=True)
    requested_by: str = "anonymous"
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None


# --------------------------------------------------------------------------- #
# Researcher (Rebuild Product Researcher — confirmed contract)
# --------------------------------------------------------------------------- #
CANDIDATE_STATUS = "unverified_possible_match"


class ResearchCandidate(BaseModel):
    """One possible product match, exactly as the Researcher returns it.

    Suggestions only: nothing here is ever copied into an inventory item.
    """

    # Backend id (deterministic per run + candidate) and the agent's own id.
    id: str
    candidate_id: Optional[str] = None
    run_id: str
    case_id: str
    item_id: str
    brand: Optional[str] = None
    model: Optional[str] = None
    product_name: Optional[str] = None
    product_url: Optional[str] = None
    matching_features: list[str] = Field(default_factory=list)
    differing_features: list[str] = Field(default_factory=list)
    # The agent returns an array of strings; preserved verbatim.
    missing_verification: list[str] = Field(default_factory=list)
    price_as_displayed: Optional[str] = None
    currency: Optional[str] = None
    source_accessed_at: Optional[str] = None
    status: str = CANDIDATE_STATUS
    unverified: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class ResearchRun(BaseModel):
    id: str
    case_id: str
    item_id: str
    status: InvestigationStatus = InvestigationStatus.queued
    mode: Literal["mock", "live"] = "live"
    app_id: Optional[str] = None
    agent_run_id: Optional[str] = None
    thread_id: Optional[str] = None
    error: Optional[str] = None
    region_currency: Optional[str] = None
    candidate_ids: list[str] = Field(default_factory=list)
    candidates_valid: int = 0
    # Preserved from the agent result. Entries are usually
    # {"query": "...", "tool": "web_search"} objects; kept as returned.
    queries_run: list[Any] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    # Exact inputs sent to the agent. Never serialised: it contains signed URLs.
    input_snapshot: Optional[dict[str, Any]] = Field(default=None, exclude=True)
    # Redacted, public view of the same inputs (no URLs, no credentials).
    input_summary: dict[str, Any] = Field(default_factory=dict)
    requested_by: str = "anonymous"
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None


class ResearchRunResponse(BaseModel):
    run: ResearchRun
    candidates: list[ResearchCandidate] = Field(default_factory=list)


class ResearchRecoverRequest(BaseModel):
    """Re-import an already-completed CREAO run. Starts no new run."""

    creao_run_id: str = Field(min_length=1, max_length=120)


class ResearchRecoverResponse(BaseModel):
    run: ResearchRun
    candidates: list[ResearchCandidate] = Field(default_factory=list)
    source: str
    already_recovered: bool = False


class ResearchStartRequest(BaseModel):
    """`region_currency` is supplied explicitly; the backend never guesses one."""

    region_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    requested_by: str = Field(default="anonymous", max_length=120)

    @field_validator("region_currency")
    @classmethod
    def _upper(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v


# --------------------------------------------------------------------------- #
# Claim submission (Gmail DRAFT only — never sent)
# --------------------------------------------------------------------------- #
CLAIM_RESULT_STATUSES = ("draft_ready", "needs_information", "draft_failed")

CLAIM_RESULT_KEYS = (
    "case_ref",
    "status",
    "recipient_email",
    "subject",
    "body_text",
    "attachment_filename",
    "gmail_draft_id",
    "gmail_message_id",
    "missing_information",
    "warnings",
)


class ClaimSubmissionRequest(BaseModel):
    """The six required agent inputs plus the optional ones.

    `case_ref` and `reviewed_record` are built server-side; a caller cannot pass
    them, so the record can never disagree with the saved case.
    """

    sender_name: str = Field(min_length=1, max_length=200)
    insurer_name: str = Field(min_length=1, max_length=200)
    recipient_email: str = Field(min_length=3, max_length=320)
    policy_number: Optional[str] = Field(default=None, max_length=120)
    claim_number: Optional[str] = Field(default=None, max_length=120)
    policy_type: Optional[str] = Field(default=None, max_length=120)
    additional_notes: Optional[str] = Field(default=None, max_length=4000)
    # Generate our recovery PDF and hand the agent a signed HTTPS URL.
    include_recovery_pdf: bool = True
    # Required to start a second submission after one already produced a draft.
    allow_resubmit: bool = False
    requested_by: str = Field(default="anonymous", max_length=120)


class ClaimSubmission(BaseModel):
    id: str
    case_id: str
    status: InvestigationStatus = InvestigationStatus.queued
    mode: Literal["mock", "live"] = "live"
    app_id: Optional[str] = None
    agent_run_id: Optional[str] = None
    thread_id: Optional[str] = None
    # Agent result
    result_status: Optional[str] = None          # draft_ready | needs_information | draft_failed
    recipient_email: str
    subject: Optional[str] = None
    body_text: Optional[str] = None
    attachment_filename: Optional[str] = None
    gmail_draft_id: Optional[str] = None
    gmail_message_id: Optional[str] = None
    missing_information: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # Bookkeeping
    error: Optional[str] = None
    pdf_supplied: bool = False
    # Generated PDF bytes; never serialised to clients.
    pdf_bytes: Optional[bytes] = Field(default=None, exclude=True)
    pdf_filename: Optional[str] = None
    requested_by: str = "anonymous"
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None


class ClaimSubmissionResponse(BaseModel):
    submission: ClaimSubmission
    # True when a Gmail draft exists for this submission (id is real, never sent).
    draft_created: bool = False
    sent: bool = False


# --------------------------------------------------------------------------- #
# Agent findings and questions
# --------------------------------------------------------------------------- #
FINDING_RESOLUTIONS = ("confirmed", "rejected", "needs_more_evidence", "unresolved")


class Finding(BaseModel):
    """An agent observation plus the human decision about it.

    Agent fields (`finding_id`, `finding_type`, `explanation`, `item_ids`,
    `evidence_ids`) are never rewritten by a decision. Human fields live
    alongside them and carry their own `revision`.
    """

    id: str
    case_id: str
    investigation_id: Optional[str] = None
    finding_id: Optional[str] = None          # fnd_001
    finding_type: Optional[str] = None        # possible_duplicate | …
    item_ids: list[str] = Field(default_factory=list)      # agent itm_### ids
    evidence_ids: list[str] = Field(default_factory=list)  # ev_### ids
    explanation: Optional[str] = None
    # Agent-reported status (kept for backwards compatibility).
    status: str = "needs_review"
    # --- human decision ---
    resolution: Optional[str] = None           # confirmed | rejected | …
    resolution_reason: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    revision: int = 0                          # 0 = no human decision yet


class Question(BaseModel):
    """An agent question plus the human answer."""

    id: str
    case_id: str
    investigation_id: Optional[str] = None
    question_id: Optional[str] = None         # q_001
    item_id: Optional[str] = None             # agent itm_### id (nullable)
    question: str = ""
    reason: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)
    status: str = "open"                      # open | answered
    resolution_reason: Optional[str] = None   # kept for backwards compatibility
    # --- human answer ---
    answer: Optional[str] = None
    answered_by: Optional[str] = None
    answered_at: Optional[datetime] = None
    revision: int = 0                          # 0 = no human answer yet


class DecisionEvent(BaseModel):
    """Append-only audit trail for question answers and finding resolutions."""

    id: str
    case_id: str
    target_type: Literal["question", "finding"]
    target_id: str                            # backend id of the question/finding
    agent_ref: Optional[str] = None           # q_001 / fnd_001
    revision: int
    value: Optional[str] = None               # answer text or resolution
    reviewer: str = "anonymous"
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Decision requests
# --------------------------------------------------------------------------- #
class QuestionAnswerRequest(BaseModel):
    # null or "" clears an existing answer and returns the question to `open`.
    answer: Optional[str] = Field(default=None, max_length=4000)
    reviewer: str = Field(default="anonymous", max_length=120)
    if_revision: int = Field(ge=0)


class FindingResolutionRequest(BaseModel):
    resolution: Literal["confirmed", "rejected", "needs_more_evidence", "unresolved"]
    reason: Optional[str] = Field(default=None, max_length=2000)
    reviewer: str = Field(default="anonymous", max_length=120)
    if_revision: int = Field(ge=0)


# --------------------------------------------------------------------------- #
# Review read model (GET /api/cases/{case_id}/review)
# --------------------------------------------------------------------------- #
class ReviewEvidence(BaseModel):
    """Evidence metadata only — no storage paths, tokens or artifact URLs."""

    evidence_id: Optional[str] = None         # ev_### when the agent reported it
    upload_id: Optional[str] = None
    filename: str
    evidence_type: str                        # photo | receipt | document | unknown
    content_type: Optional[str] = None
    readable: Optional[bool] = None
    description: Optional[str] = None
    size_bytes: Optional[int] = None
    url: Optional[str] = None                 # local API route, not a signed link


class ReviewItem(BaseModel):
    """One belonging as the review UI needs it.

    `id` is the backend id used by PATCH /api/items/{id}; `agent_item_id` is the
    original CREAO id. Agent values and human corrections are both present and
    distinguishable via `status` / `user_confirmed_fields`.
    """

    id: str
    agent_item_id: Optional[str] = None
    investigation_id: Optional[str] = None
    name: str
    room: Optional[str] = None
    category: str = "other"
    brand: Optional[str] = None
    model: Optional[str] = None
    purchase_date: Optional[str] = None
    purchase_price: Optional[str] = None      # printed string, never converted
    currency: Optional[str] = None            # null when the agent had none
    location_in_image: Optional[str] = None
    visible_observations: Optional[str] = None
    condition: Optional[str] = None
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    user_confirmed_fields: list[str] = Field(default_factory=list)
    review_status: Optional[str] = None
    damage_level: DamageLevel = DamageLevel.unknown
    confidence: float = 0.0
    estimated_value: Optional[float] = None   # separate from purchase_price
    status: ItemStatus = ItemStatus.ai_suggested
    revision: int = 1
    updated_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=utcnow)
    # Follow-up reconciliation (see Investigation.review_answers_sent)
    reconciliation: str = "new"               # new | matched | identity_conflict
    possible_match_item_id: Optional[str] = None
    agent_proposal: Optional[dict[str, Any]] = None


class ReviewFinding(Finding):
    pass


class ReviewQuestion(Question):
    pass


class ReviewDecision(BaseModel):
    """Latest human decision recorded for an item, if any."""

    item_id: str
    agent_item_id: Optional[str] = None
    editor: str
    note: Optional[str] = None
    changes: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class CaseReview(BaseModel):
    case: Case
    evidence: list[ReviewEvidence] = Field(default_factory=list)
    items: list[ReviewItem] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    questions: list[ReviewQuestion] = Field(default_factory=list)
    # Latest human edit per inventory item (PATCH /api/items/{id}).
    decisions: list[ReviewDecision] = Field(default_factory=list)
    # Append-only audit trail for question answers and finding resolutions.
    # Link back with target_id (backend id) or agent_ref (q_001 / fnd_001).
    question_decisions: list[DecisionEvent] = Field(default_factory=list)
    finding_decisions: list[DecisionEvent] = Field(default_factory=list)
    investigations: list[Investigation] = Field(default_factory=list)
    stats: ReviewStats
    generated_at: datetime = Field(default_factory=utcnow)
    source: str = "memory"                    # "memory" | "spacetimedb"


# --------------------------------------------------------------------------- #
# Review / activity
# --------------------------------------------------------------------------- #
class CommentCreate(BaseModel):
    author: str = Field(default="anonymous", max_length=120)
    body: str = Field(min_length=1, max_length=2000)
    item_id: Optional[str] = None


class Comment(BaseModel):
    id: str
    case_id: str
    item_id: Optional[str] = None
    author: str
    body: str
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Run recovery (import an already-completed CREAO run)
# --------------------------------------------------------------------------- #
class RecoverCase(BaseModel):
    """Enough metadata to restore a case whose in-memory record was lost."""

    id: str = Field(min_length=1, max_length=120)
    title: str = Field(default="Recovered case", max_length=200)
    address: Optional[str] = Field(default=None, max_length=300)
    disaster_type: str = Field(default="flood", max_length=60)
    notes: Optional[str] = Field(default=None, max_length=2000)
    created_by: str = Field(default="recovery", max_length=120)


class RecoverEvidence(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=40)
    upload_id: str = Field(min_length=1, max_length=120)
    filename: str = Field(min_length=1, max_length=300)
    content_type: str = Field(default="image/jpeg", max_length=120)
    kind: UploadKind = UploadKind.photo


class RecoverRequest(BaseModel):
    """Import an existing completed CREAO run. Starts no new run."""

    run_id: str = Field(min_length=1, max_length=120)
    investigation_id: Optional[str] = Field(default=None, max_length=120)
    case: Optional[RecoverCase] = None
    evidence: list[RecoverEvidence] = Field(default_factory=list)


class RecoverResponse(BaseModel):
    recovered: bool
    already_recovered: bool = False
    run_id: str
    run_status: str
    case_id: str
    investigation: Investigation
    items_created: int = 0
    items_already_present: int = 0
    evidence_ids: list[str] = Field(default_factory=list)
    restored_case: bool = False
    restored_uploads: list[str] = Field(default_factory=list)


class ReviewStats(BaseModel):
    """Small summary the dashboard header renders."""

    case_id: str
    total_items: int = 0
    ai_suggested: int = 0
    human_confirmed: int = 0
    human_corrected: int = 0
    rejected: int = 0
    total_estimated_value: float = 0.0
    currency: str = "USD"
    contributors: list[str] = Field(default_factory=list)
    last_activity_at: Optional[datetime] = None


class Event(BaseModel):
    """Broadcast shape shared with SpacetimeDB subscribers + the frontend."""

    id: str
    type: Literal[
        "case.created",
        "case.updated",
        "upload.created",
        "investigation.started",
        "investigation.completed",
        "item.created",
        "item.updated",
        "comment.created",
        "research.started",
        "research.completed",
        "claim.started",
        "claim.completed",
    ]
    case_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class ErrorResponse(BaseModel):
    detail: str
    code: Optional[str] = None


CaseDetail.model_rebuild()
CaseReview.model_rebuild()
