"""Evidence file resolution after a snapshot restore.

`Upload.stored_path` is excluded from API responses, so a restored upload has
`stored_path == ""`. It used to resolve to the uploads *directory*, which made
FileResponse raise and the public evidence URL return 500 — the Researcher then
fell back to text observations only.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import Case, EvidenceItem, ItemStatus, Upload, UploadKind
from app.services import evidence_urls, state_io, storage
from app.store import store

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "8900000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    store.reset()
    monkeypatch.setattr(settings, "rebuild_public_base_url", "https://example.ngrok.app")
    monkeypatch.setattr(settings, "store_backend", "memory")
    monkeypatch.setattr(settings, "creao_mode", "mock")
    yield
    store.reset()


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _write_file(case_id: str, upload_id: str, suffix: str = ".png") -> object:
    directory = settings.upload_dir / case_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{upload_id}{suffix}"
    path.write_bytes(PNG_BYTES)
    return path


def _upload(case_id: str, upload_id: str, *, stored_path: str) -> Upload:
    return Upload(
        id=upload_id,
        case_id=case_id,
        kind=UploadKind.photo,
        original_filename="room.png",
        content_type="image/png",
        size_bytes=len(PNG_BYTES),
        sha256="deadbeef",
        evidence_id="ev_001",
        url=f"/api/uploads/{upload_id}/file",
        stored_path=stored_path,
    )


def _signed_path(upload_id: str) -> str:
    return evidence_urls.upload_url(upload_id).split("example.ngrok.app", 1)[1]


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def test_valid_stored_path_serves_the_exact_bytes(client):
    case_id = f"case_{uuid.uuid4().hex[:8]}"
    upload_id = f"upl_{uuid.uuid4().hex[:8]}"
    path = _write_file(case_id, upload_id)
    store.cases[case_id] = Case(id=case_id, title="t")
    store.uploads[upload_id] = _upload(
        case_id, upload_id, stored_path=f"{case_id}/{path.name}"
    )

    response = client.get(_signed_path(upload_id))
    assert response.status_code == 200
    assert response.content == PNG_BYTES
    assert response.headers["content-type"] == "image/png"


def test_restored_upload_with_empty_stored_path_still_serves(client):
    """The actual regression: stored_path is "" after a snapshot restore."""
    case_id = f"case_{uuid.uuid4().hex[:8]}"
    upload_id = "upl_173230835194"
    _write_file(case_id, upload_id)
    store.cases[case_id] = Case(id=case_id, title="t")
    store.uploads[upload_id] = _upload(case_id, upload_id, stored_path="")

    response = client.get(_signed_path(upload_id))
    assert response.status_code == 200, response.text
    assert response.content == PNG_BYTES


def test_resolver_never_returns_a_directory():
    case_id = f"case_{uuid.uuid4().hex[:8]}"
    upload_id = f"upl_{uuid.uuid4().hex[:8]}"
    (settings.upload_dir / case_id).mkdir(parents=True, exist_ok=True)
    upload = _upload(case_id, upload_id, stored_path="")

    assert storage.resolve_upload_path(upload) is None


def test_missing_file_is_a_sanitized_404_not_a_500(client):
    case_id = f"case_{uuid.uuid4().hex[:8]}"
    upload_id = f"upl_{uuid.uuid4().hex[:8]}"
    store.cases[case_id] = Case(id=case_id, title="t")
    store.uploads[upload_id] = _upload(case_id, upload_id, stored_path="")

    response = client.get(_signed_path(upload_id))
    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["code"] == "EVIDENCE_FILE_MISSING"
    # Nothing about the filesystem leaks.
    blob = json.dumps(body)
    assert "uploads/" not in blob and str(settings.upload_dir) not in blob

    # The authenticated local route behaves the same way.
    assert client.get(f"/api/uploads/{upload_id}/file").status_code == 404


def test_unknown_upload_is_404(client):
    # Correctly signed, but no such upload record.
    response = client.get(_signed_path("upl_missing"))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "UPLOAD_NOT_FOUND"
    assert client.get("/api/uploads/upl_missing/file").status_code == 404


# --------------------------------------------------------------------------- #
# Signatures
# --------------------------------------------------------------------------- #
def test_expired_and_tampered_signatures_are_rejected(client):
    case_id = f"case_{uuid.uuid4().hex[:8]}"
    upload_id = f"upl_{uuid.uuid4().hex[:8]}"
    _write_file(case_id, upload_id)
    store.cases[case_id] = Case(id=case_id, title="t")
    store.uploads[upload_id] = _upload(case_id, upload_id, stored_path="")

    exp = int(time.time()) - 10
    sig = evidence_urls._sign(evidence_urls.UPLOAD_PATH, upload_id, exp)
    expired = client.get(f"/api/evidence/{upload_id}/public?exp={exp}&sig={sig}")
    assert expired.status_code == 403
    assert expired.json()["detail"]["reason"] == "expired"

    tampered = client.get(f"/api/evidence/{upload_id}/public?exp={exp + 999}&sig={sig}")
    assert tampered.status_code == 403


def test_public_url_uses_the_configured_https_base():
    url = evidence_urls.upload_url("upl_x")
    assert url.startswith("https://example.ngrok.app/api/evidence/upl_x/public?")
    assert "exp=" in url and "sig=" in url
    assert "127.0.0.1" not in url and "localhost" not in url


def test_served_response_exposes_no_credentials(client):
    case_id = f"case_{uuid.uuid4().hex[:8]}"
    upload_id = f"upl_{uuid.uuid4().hex[:8]}"
    _write_file(case_id, upload_id)
    store.cases[case_id] = Case(id=case_id, title="t")
    store.uploads[upload_id] = _upload(case_id, upload_id, stored_path="")

    response = client.get(_signed_path(upload_id))
    blob = str(response.headers).lower()
    assert "capi_" not in blob and "authorization" not in blob


# --------------------------------------------------------------------------- #
# Snapshot import repairs stored_path
# --------------------------------------------------------------------------- #
def _snapshot_for(case_id: str, upload_id: str) -> dict:
    return {
        "snapshot_version": 1,
        "cases": [
            {"id": case_id, "title": "Restored", "disaster_type": "flood",
             "status": "review", "created_by": "priya", "members": [],
             "upload_count": 1, "item_count": 1,
             "created_at": "2026-10-24T00:00:00+00:00",
             "updated_at": "2026-10-24T00:00:00+00:00"}
        ],
        "uploads": [
            {"id": upload_id, "case_id": case_id, "kind": "photo",
             "original_filename": "room.png", "content_type": "image/png",
             "size_bytes": len(PNG_BYTES), "sha256": "x", "uploaded_by": "priya",
             "evidence_id": "ev_001", "url": f"/api/uploads/{upload_id}/file",
             "created_at": "2026-10-24T00:00:00+00:00"}   # no stored_path
        ],
        "investigations": [],
        "items": [
            {"id": "itm_restored", "case_id": case_id, "upload_id": upload_id,
             "agent_item_id": "itm_001", "name": "Armchair", "category": "other",
             "damage_level": "unknown", "confidence": 0.0,
             "estimated_value": 425.0, "currency": None, "status": "human_corrected",
             "supporting_evidence_ids": ["ev_001"], "user_confirmed_fields": ["estimated_value"],
             "revision": 2, "created_at": "2026-10-24T00:00:00+00:00",
             "updated_at": "2026-10-24T00:00:00+00:00", "updated_by": "priya"}
        ],
        "revisions": [], "comments": [], "findings": [], "questions": [],
        "decision_events": [], "research_runs": [], "research_candidates": [],
    }


def test_import_reattaches_stored_path_and_reports_missing(client):
    case_id = f"case_{uuid.uuid4().hex[:8]}"
    present = "upl_173230835194"
    absent = "upl_not_on_disk"
    _write_file(case_id, present)

    snapshot = _snapshot_for(case_id, present)
    snapshot["uploads"].append(
        {**snapshot["uploads"][0], "id": absent, "evidence_id": "ev_002"}
    )
    snapshot["uploads"][1]["original_filename"] = "second.png"
    report = state_io.import_snapshot(snapshot)

    assert report["uploads_repaired"] == [present]
    assert report["uploads_missing"] == [absent]
    assert store.uploads[present].stored_path, "stored_path must be repaired from disk"
    assert store.uploads[present].evidence_id == "ev_001"

    # The repaired upload serves immediately, through the signed URL.
    response = client.get(_signed_path(present))
    assert response.status_code == 200
    assert response.content == PNG_BYTES

    # The one without bytes is a clean 404, not a 500.
    missing = client.get(_signed_path(absent))
    assert missing.status_code == 404


def test_import_preserves_ids_and_human_decisions(client):
    case_id = f"case_{uuid.uuid4().hex[:8]}"
    upload_id = f"upl_{uuid.uuid4().hex[:8]}"
    _write_file(case_id, upload_id)
    state_io.import_snapshot(_snapshot_for(case_id, upload_id))

    item = client.get("/api/items/itm_restored").json()
    assert item["id"] == "itm_restored"
    assert item["revision"] == 2
    assert item["estimated_value"] == 425.0
    assert item["status"] == "human_corrected"
    assert store.uploads[upload_id].id == upload_id
    assert store.uploads[upload_id].evidence_id == "ev_001"


def test_import_makes_no_creao_calls(client, monkeypatch):
    calls: list[str] = []

    class Boom:
        def __init__(self, *a, **k):
            calls.append("client")

    monkeypatch.setattr("app.services.creao.CreaoAppClient", Boom)
    case_id = f"case_{uuid.uuid4().hex[:8]}"
    upload_id = f"upl_{uuid.uuid4().hex[:8]}"
    _write_file(case_id, upload_id)
    state_io.import_snapshot(_snapshot_for(case_id, upload_id))
    assert calls == []
