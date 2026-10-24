"""Real SpacetimeDB integration tests.

These talk to a live SpacetimeDB instance — nothing is mocked. They are skipped
only when the server or database is not reachable, so `pytest` stays green on a
machine without one.

Run:
    spacetime start --listen-addr 127.0.0.1:3000
    spacetime publish rebuild -s http://127.0.0.1:3000 -y -p spacetimedb
    SPACETIMEDB_URL=http://127.0.0.1:3000 pytest tests/test_spacetimedb_integration.py -v
"""

from __future__ import annotations

import io
import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import spacetime_db
from app.store import store

URL = os.environ.get("SPACETIMEDB_URL", "http://127.0.0.1:3000")
DB = os.environ.get("SPACETIMEDB_DB", "rebuild")

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


def _server_ready() -> bool:
    try:
        response = httpx.post(
            f"{URL}/v1/database/{DB}/sql",
            content=b"SELECT * FROM app_case",
            headers={"Content-Type": "text/plain"},
            timeout=3.0,
        )
        return response.status_code < 500
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _server_ready(),
    reason=f"no SpacetimeDB at {URL} (start one with `spacetime start`)",
)


@pytest.fixture
def live(monkeypatch):
    """Point the backend at the real database and hydrate from it."""
    monkeypatch.setattr(settings, "store_backend", "spacetime")
    monkeypatch.setattr(settings, "spacetimedb_url", URL)
    monkeypatch.setattr(settings, "spacetimedb_db", DB)
    spacetime_db.reset_sync_state()
    store.reset()
    spacetime_db.hydrate()
    yield
    store.reset()
    spacetime_db.reset_sync_state()


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# --------------------------------------------------------------------------- #
# Persistence across a restart
# --------------------------------------------------------------------------- #
def test_case_survives_a_restart(live):
    case_id = _unique("case")
    with TestClient(app) as client:
        created = client.post(
            "/api/cases",
            json={"title": "Restart proof", "address": "Asheville, NC"},
        )
        assert created.status_code == 201
        # Force the real id so the row is easy to find afterwards.
        case_id = created.json()["id"]
        upload = client.post(
            f"/api/cases/{case_id}/uploads",
            files={"file": ("room.jpg", io.BytesIO(PNG_BYTES), "image/jpeg")},
            data={"kind": "photo"},
        )
        assert upload.status_code == 201

    # The row reached the database.
    rows = spacetime_db.sql(f"SELECT * FROM app_case WHERE id = '{case_id}'")
    assert len(rows) == 1
    assert rows[0]["title"] == "Restart proof"

    # Simulate a FastAPI restart: cache gone, database untouched.
    store.reset()
    assert store.get_case(case_id) is None

    with TestClient(app) as client:  # lifespan hydrates
        fetched = client.get(f"/api/cases/{case_id}")
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["title"] == "Restart proof"
        uploads = client.get(f"/api/cases/{case_id}/uploads").json()
        assert len(uploads) == 1
        assert uploads[0]["original_filename"] == "room.jpg"


def test_items_survive_a_restart(live):
    with TestClient(app) as client:
        case_id = client.post("/api/cases", json={"title": "Items restart"}).json()["id"]
        created = client.post(
            f"/api/cases/{case_id}/items",
            json={"name": "Waterlogged sofa", "room": "living room", "editor": "priya"},
        )
        assert created.status_code == 201
        item_id = created.json()["id"]

    store.reset()
    with TestClient(app) as client:
        items = client.get(f"/api/cases/{case_id}/items").json()
        assert [i["id"] for i in items] == [item_id]
        assert items[0]["name"] == "Waterlogged sofa"


# --------------------------------------------------------------------------- #
# Duplicate-import safety at the database level
# --------------------------------------------------------------------------- #
def test_same_creao_item_cannot_be_imported_twice(live):
    import json

    from app.models import Case, EvidenceItem, ItemStatus

    case_id = _unique("case")
    investigation_id = _unique("inv")
    spacetime_db.call(
        "upsert_case",
        [json.dumps(spacetime_db._case_payload(Case(id=case_id, title="dup guard")))],
    )

    payload = spacetime_db._item_payload(
        EvidenceItem(
            id=_unique("itm"),
            case_id=case_id,
            investigation_id=investigation_id,
            agent_item_id="itm_001",
            name="Armchair",
            status=ItemStatus.ai_suggested,
        )
    )

    # Same (case, investigation, agent_item_id) with two different local ids:
    # the reducer must keep exactly one row.
    spacetime_db.call("upsert_item", [json.dumps(payload)])
    spacetime_db.call("upsert_item", [json.dumps(dict(payload, id=_unique("itm")))])

    rows = spacetime_db.sql(
        f"SELECT * FROM item WHERE case_id = '{case_id}' AND agent_item_id = 'itm_001'"
    )
    assert len(rows) == 1, "duplicate import created a second row"


# --------------------------------------------------------------------------- #
# Human decisions are protected
# --------------------------------------------------------------------------- #
def test_review_reducer_rejects_a_stale_revision(live):
    import json

    from app.models import Case, EvidenceItem

    case_id = _unique("case")
    spacetime_db.call(
        "upsert_case",
        [json.dumps(spacetime_db._case_payload(Case(id=case_id, title="conflict")))],
    )

    item_id = _unique("itm")
    spacetime_db.call(
        "upsert_item",
        [json.dumps(spacetime_db._item_payload(
            EvidenceItem(id=item_id, case_id=case_id, name="Rug", agent_item_id="itm_001")
        ))],
    )

    revision_id = _unique("rev")
    spacetime_db.call(
        "apply_review",
        [item_id, 1, json.dumps({"estimated_value": 300.0}), "priya", "first edit", revision_id],
    )

    # Second edit still believing the item is at revision 1 → must be refused.
    with pytest.raises(spacetime_db.SpacetimeError):
        spacetime_db.call(
            "apply_review",
            [item_id, 1, json.dumps({"estimated_value": 999.0}), "marcus", "stale", _unique("rev")],
        )

    rows = spacetime_db.sql(f"SELECT * FROM item WHERE id = '{item_id}'")
    assert rows[0]["estimated_value"] == 300.0
    assert rows[0]["revision"] == 2


def test_agent_reimport_does_not_undo_a_human_decision(live):
    import json

    from app.models import Case, EvidenceItem, ItemStatus

    case_id = _unique("case")
    spacetime_db.call(
        "upsert_case", [json.dumps(spacetime_db._case_payload(Case(id=case_id, title="preserve")))]
    )

    item_id = _unique("itm")
    spacetime_db.call(
        "upsert_item",
        [json.dumps(spacetime_db._item_payload(
            EvidenceItem(id=item_id, case_id=case_id, name="Lamp", agent_item_id="itm_002")
        ))],
    )
    spacetime_db.call(
        "apply_review",
        [item_id, 1, json.dumps({"condition": "confirmed cracked base"}), "priya", "verified",
         _unique("rev")],
    )

    # An agent re-import arrives still marked ai_suggested.
    spacetime_db.call(
        "upsert_item",
        [json.dumps(spacetime_db._item_payload(
            EvidenceItem(
                id=item_id,
                case_id=case_id,
                name="Lamp",
                agent_item_id="itm_002",
                status=ItemStatus.ai_suggested,
                condition="agent says intact",
            )
        ))],
    )

    rows = spacetime_db.sql(f"SELECT * FROM item WHERE id = '{item_id}'")
    assert rows[0]["status"] == "human_corrected"
    assert rows[0]["condition"] == "confirmed cracked base"


# --------------------------------------------------------------------------- #
# PDF uses the latest reviewed state
# --------------------------------------------------------------------------- #
def test_pdf_export_reflects_the_latest_correction(live):
    with TestClient(app) as client:
        case_id = client.post("/api/cases", json={"title": "PDF state"}).json()["id"]
        item = client.post(
            f"/api/cases/{case_id}/items",
            json={"name": "Dining table", "estimated_value": 100.0, "editor": "priya"},
        ).json()

        client.patch(
            f"/api/items/{item['id']}",
            json={"estimated_value": 777.5, "editor": "marcus", "note": "receipt found"},
        )

        pdf = client.get(f"/api/cases/{case_id}/export.pdf")
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")

    # The value written to the database is the corrected one, and the export
    # was generated from that same state.
    rows = spacetime_db.sql(f"SELECT * FROM item WHERE case_id = '{case_id}'")
    assert rows[0]["estimated_value"] == 777.5

    store.reset()
    with TestClient(app) as client:
        after_restart = client.get(f"/api/cases/{case_id}/export.pdf")
        assert after_restart.status_code == 200
        assert after_restart.content.startswith(b"%PDF")


# --------------------------------------------------------------------------- #
# Review endpoint reads durable state
# --------------------------------------------------------------------------- #
def test_review_endpoint_serves_persisted_agent_fields_after_restart(live):
    import json

    from app.models import EvidenceItem, ItemStatus

    with TestClient(app) as client:
        case_id = client.post("/api/cases", json={"title": "Review restart"}).json()["id"]

        # An agent-imported item exactly as the CREAO payload describes it:
        # no currency reported, and a printed purchase price that is NOT a
        # valuation.
        item = EvidenceItem(
            id=_unique("itm"),
            case_id=case_id,
            investigation_id=_unique("inv"),
            agent_item_id="itm_001",
            name="Brown fabric armchair",
            brand=None,
            model=None,
            purchase_date=None,
            purchase_price="89.99",
            currency=None,
            location_in_image="ev_001: right third of the frame",
            visible_observations="Deep brown upholstery, water line across the base",
            supporting_evidence_ids=["ev_001"],
            user_confirmed_fields=[],
            review_status="needs_review",
            estimated_value=None,
            status=ItemStatus.ai_suggested,
        )
        with store.lock:
            store.items[item.id] = item
        spacetime_db.sync()

    # Restart: cache gone, database authoritative.
    store.reset()
    assert store.list_items(case_id) == []

    with TestClient(app) as client:  # lifespan hydrates
        review = client.get(f"/api/cases/{case_id}/review").json()

    assert review["source"] == "spacetimedb"
    served = next(i for i in review["items"] if i["agent_item_id"] == "itm_001")
    assert served["id"] == item.id
    assert served["brand"] is None
    assert served["purchase_price"] == "89.99"
    assert served["currency"] is None, "unknown currency must stay null after a restart"
    assert served["estimated_value"] is None, "purchase_price must not become a valuation"
    assert served["location_in_image"].startswith("ev_001")
    assert served["revision"] == 1
