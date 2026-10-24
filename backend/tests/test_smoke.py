"""Smoke tests for the core flow: case → upload → investigation → review → PDF.

Run: cd backend && pytest -q
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import store

# Smallest valid 1x1 PNG.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def clean_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "creao" in body["integrations"]


def test_case_upload_investigate_review_export(client):
    # 1. create a case
    r = client.post("/api/cases", json={"title": "42 Willow Creek Rd", "created_by": "priya"})
    assert r.status_code == 201, r.text
    case_id = r.json()["id"]

    # 2. upload a photo
    r = client.post(
        f"/api/cases/{case_id}/uploads",
        files={"file": ("living-room.png", io.BytesIO(PNG_BYTES), "image/png")},
        data={"kind": "photo", "uploaded_by": "priya"},
    )
    assert r.status_code == 201, r.text
    upload = r.json()
    assert upload["kind"] == "photo"
    assert upload["size_bytes"] == len(PNG_BYTES)
    assert "stored_path" not in upload  # internal field must not leak

    # 3. file is served back
    r = client.get(upload["url"])
    assert r.status_code == 200
    assert r.content == PNG_BYTES

    # 4. run the investigator (stub mode — no CREAO key in CI)
    r = client.post(
        f"/api/cases/{case_id}/investigations",
        json={"upload_id": upload["id"], "room_hint": "living room"},
    )
    assert r.status_code == 202, r.text
    inv_id = r.json()["id"]

    inv = client.get(f"/api/investigations/{inv_id}").json()
    assert inv["status"] == "complete", inv
    assert inv["item_ids"], "stub investigation should produce items"

    # 5. items landed on the case
    items = client.get(f"/api/cases/{case_id}/items").json()
    assert len(items) == len(inv["item_ids"])
    assert all(i["status"] == "ai_suggested" for i in items)
    first = items[0]

    # 6. human correction bumps revision + writes audit trail
    r = client.patch(
        f"/api/items/{first['id']}",
        json={
            "estimated_value": 42.5,
            "if_revision": first["revision"],
            "editor": "marcus",
            "note": "Receipt found, value corrected",
        },
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["revision"] == first["revision"] + 1
    assert updated["status"] == "human_corrected"
    assert updated["updated_by"] == "marcus"

    revisions = client.get(f"/api/items/{first['id']}/revisions").json()
    assert len(revisions) == 1
    assert revisions[0]["editor"] == "marcus"

    # 7. stale revision is rejected, not silently overwritten
    r = client.patch(
        f"/api/items/{first['id']}",
        json={"name": "Sofa (clobber)", "if_revision": first["revision"]},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["current"]["id"] == first["id"]

    # 8. confirmed + stats
    r = client.post(f"/api/items/{items[1]['id']}/confirm", params={"editor": "priya"})
    assert r.status_code == 200
    assert r.json()["status"] == "human_confirmed"

    stats = client.get(f"/api/cases/{case_id}/stats").json()
    assert stats["total_items"] == len(items)
    assert set(stats["contributors"]) == {"marcus", "priya"}

    # 9. PDF export
    r = client.get(f"/api/cases/{case_id}/export.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert len(r.content) > 2000


def test_rejects_unsupported_file_type(client):
    case_id = client.post("/api/cases", json={"title": "x"}).json()["id"]
    r = client.post(
        f"/api/cases/{case_id}/uploads",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert r.status_code == 415


def test_404s(client):
    assert client.get("/api/cases/case_missing").status_code == 404
    assert client.get("/api/items/itm_missing").status_code == 404
    assert client.get("/api/uploads/upl_missing").status_code == 404


def test_demo_seed(client):
    r = client.post("/api/demo/seed")
    assert r.status_code == 201
    case_id = r.json()["case_id"]
    assert len(client.get(f"/api/cases/{case_id}/items").json()) == r.json()["items"]
