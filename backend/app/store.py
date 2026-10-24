"""In-process data store.

Deliberately simple: dicts behind a lock. For a 24-hour hackathon this keeps the
backend readable, and it is the *single* place to swap in SpacetimeDB (or
Postgres) later — routers never touch raw storage.

SpacetimeDB remains the source of truth for persistent shared state; this store
is the backend's working copy that gets mirrored out through
`services.spacetime`. See backend/README.md.
"""

from __future__ import annotations

import threading
import uuid

from .models import (
    Case,
    Comment,
    DecisionEvent,
    EvidenceItem,
    Event,
    Finding,
    Investigation,
    Question,
    Revision,
    Upload,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.cases: dict[str, Case] = {}
        self.uploads: dict[str, Upload] = {}
        self.items: dict[str, EvidenceItem] = {}
        self.investigations: dict[str, Investigation] = {}
        self.comments: dict[str, Comment] = {}
        self.revisions: dict[str, Revision] = {}   # revision_id -> Revision
        self.findings: dict[str, Finding] = {}
        self.questions: dict[str, Question] = {}
        self.decision_events: dict[str, DecisionEvent] = {}
        self.events: list[Event] = []

    # -- lifecycle ---------------------------------------------------------- #
    def reset(self) -> None:
        """Test helper: wipe everything."""
        with self._lock:
            for collection in (
                self.cases,
                self.uploads,
                self.items,
                self.investigations,
                self.comments,
                self.revisions,
                self.findings,
                self.questions,
                self.decision_events,
            ):
                collection.clear()
            self.events.clear()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    # -- queries ------------------------------------------------------------ #
    def list_cases(self) -> list[Case]:
        with self._lock:
            return sorted(self.cases.values(), key=lambda c: c.created_at, reverse=True)

    def get_case(self, case_id: str) -> Case | None:
        return self.cases.get(case_id)

    def list_uploads(self, case_id: str) -> list[Upload]:
        with self._lock:
            return sorted(
                (u for u in self.uploads.values() if u.case_id == case_id),
                key=lambda u: u.created_at,
            )

    def list_items(self, case_id: str) -> list[EvidenceItem]:
        with self._lock:
            return sorted(
                (i for i in self.items.values() if i.case_id == case_id),
                key=lambda i: i.created_at,
            )

    def list_investigations(self, case_id: str) -> list[Investigation]:
        with self._lock:
            return sorted(
                (inv for inv in self.investigations.values() if inv.case_id == case_id),
                key=lambda inv: inv.created_at,
                reverse=True,
            )

    def list_comments(self, case_id: str) -> list[Comment]:
        with self._lock:
            return sorted(
                (c for c in self.comments.values() if c.case_id == case_id),
                key=lambda c: c.created_at,
            )

    def list_findings(self, case_id: str) -> list[Finding]:
        with self._lock:
            return [f for f in self.findings.values() if f.case_id == case_id]

    def list_questions(self, case_id: str) -> list[Question]:
        with self._lock:
            return [q for q in self.questions.values() if q.case_id == case_id]

    def list_decisions(self, case_id: str) -> list[DecisionEvent]:
        with self._lock:
            return sorted(
                (d for d in self.decision_events.values() if d.case_id == case_id),
                key=lambda d: d.created_at,
            )

    def list_revisions(self, item_id: str) -> list[Revision]:
        with self._lock:
            return sorted(
                (r for r in self.revisions.values() if r.item_id == item_id),
                key=lambda r: r.created_at,
            )


store = Store()
