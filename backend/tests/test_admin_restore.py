"""Admin file restore for an existing upload.

The image for `upl_173230835194` was lost when the snapshot was imported (the
bytes were never in the snapshot). This endpoint puts the original file back
under the same upload record after verifying it against the size and SHA-256
saved at ingest time.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models import Case, EvidenceItem, ItemStatus, Upload, UploadKind
from app.store import store

TOKEN = "restore-test-token"
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "8900000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)
OTHER_BYTES = PNG_BYTES + b"\x00"      # same type, different bytes


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_CREATED: list[str] = []


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    store.reset()
    _CREATED.clear()
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    monkeypatch.setattr(settings, "store_backend", "memory")
    monkeypatch.setattr(settings, "creao_mode", "mock")
    monkeypatch.setattr(settings, "rebuild_public_base_url", "https://example.ngrok.app")
    yield
    store.reset()
    # Remove the case directories this test created (test-only local cleanup).
    import shutil

    for case_dir in set(_CREATED):
        shutil.rmtree(settings.upload_dir / case_dir, ignore_errors=True)


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _setup(*, sha=None, size=None, case_id=None, upload_id=None):
    # Unique per test so cases never collide on the shared uploads directory;
    # the production identifiers are exercised explicitly where it matters.
    case_id = case_id or f"case_{uuid.uuid4().hex[:12]}"
    upload_id = upload_id or f"upl_{uuid.uuid4().hex[:12]}"
    _CREATED.append(case_id)
    store.cases[case_id] = Case(id=case_id, title="42 Willow Creek Rd")
    store.uploads[upload_id] = Upload(
        id=upload_id,
        case_id=case_id,
        kind=UploadKind.photo,
        original_filename=f"{upload_id}.png",
        content_type="image/png",
        size_bytes=len(PNG_BYTES) if size is None else size,
        sha256=_sha(PNG_BYTES) if sha is None else sha,
        evidence_id="ev_001",
        url=f"/api/uploads/{upload_id}/file",
        stored_path="",                      # never exported
    )
    return case_id, upload_id


def _restore(client, upload_id, data=PNG_BYTES, *, token=TOKEN, case=None, filename="room.png"):
    form = {"case_id": case} if case else {}
    headers = {"X-Admin-Token": token} if token is not None else {}
    return client.post(
        f"/api/admin/uploads/{upload_id}/restore-file",
        files={"file": (filename, io.BytesIO(data), "image/png")},
        data=form,
        headers=headers,
    )


# --------------------------------------------------------------------------- #
# Success + verification
# --------------------------------------------------------------------------- #
def test_restores_the_original_file(client):
    case_id, upload_id = _setup()
    response = _restore(client, upload_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["upload_id"] == upload_id
    assert body["case_id"] == case_id
    assert body["restored"] is True
    assert body["already_present"] is False
    assert body["size_bytes"] == len(PNG_BYTES)
    assert body["sha256"] == _sha(PNG_BYTES)
    assert body["evidence_id"] == "ev_001"

    stored = store.uploads[upload_id]
    assert stored.stored_path == f"{case_id}/{upload_id}.png"
    path = settings.upload_dir / stored.stored_path
    assert path.read_bytes() == PNG_BYTES


def test_size_and_sha_are_both_verified(client):
    _, upload_id = _setup()
    assert _restore(client, upload_id, PNG_BYTES + b"x").status_code == 422

    # Same length as the original, different bytes → SHA mismatch, not size.
    same_size_wrong = bytes([PNG_BYTES[0]]) + b"\x00" * (len(PNG_BYTES) - 1)
    response = _restore(client, upload_id, same_size_wrong)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SHA256_MISMATCH"


def test_missing_trusted_metadata_is_rejected(client):
    for overrides in ({"sha": ""}, {"sha": "abc"}, {"size": 0}):
        store.reset()
        case_id, upload_id = _setup(**overrides)
        response = _restore(client, upload_id)
        assert response.status_code == 409, overrides
        assert response.json()["detail"]["code"] == "UPLOAD_INTEGRITY_METADATA_MISSING"
        assert store.uploads[upload_id].stored_path == ""


def test_client_cannot_supply_its_own_checksum(client):
    """No request field lets a caller whitelist arbitrary bytes."""
    case_id, upload_id = _setup()
    response = client.post(
        f"/api/admin/uploads/{upload_id}/restore-file",
        files={"file": ("x.png", io.BytesIO(OTHER_BYTES), "image/png")},
        data={"sha256": _sha(OTHER_BYTES), "size_bytes": str(len(OTHER_BYTES))},
        headers={"X-Admin-Token": TOKEN},
    )
    assert response.status_code == 422
    assert store.uploads[upload_id].stored_path == ""


# --------------------------------------------------------------------------- #
# Auth + lookup
# --------------------------------------------------------------------------- #
def test_missing_or_wrong_token(client):
    _, upload_id = _setup()
    assert _restore(client, upload_id, token=None).status_code == 401
    assert _restore(client, upload_id, token="wrong").status_code == 401


def test_unset_admin_token_fails_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "")
    _, upload_id = _setup()
    assert _restore(client, upload_id, token=None).status_code == 503
    assert _restore(client, upload_id, token="anything").status_code == 503


def test_unknown_upload_is_404(client):
    _setup()
    response = _restore(client, "upl_missing")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "UPLOAD_NOT_FOUND"


def test_case_mismatch_is_rejected(client):
    _, upload_id = _setup()
    response = _restore(client, upload_id, case="case_other")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CASE_MISMATCH"
    assert store.uploads[upload_id].stored_path == ""


# --------------------------------------------------------------------------- #
# Idempotency / conflict
# --------------------------------------------------------------------------- #
def test_identical_retry_is_idempotent(client):
    case_id, upload_id = _setup()
    first = _restore(client, upload_id).json()
    second = _restore(client, upload_id)

    assert second.status_code == 200
    assert second.json()["already_present"] is True
    assert second.json()["size_bytes"] == first["size_bytes"]
    path = settings.upload_dir / store.uploads[upload_id].stored_path
    assert path.read_bytes() == PNG_BYTES


def test_existing_different_file_is_not_overwritten(client):
    case_id, upload_id = _setup()
    directory = settings.upload_dir / case_id
    directory.mkdir(parents=True, exist_ok=True)
    existing = directory / f"{upload_id}.png"
    existing.write_bytes(b"some other photo")

    response = _restore(client, upload_id)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DESTINATION_EXISTS"
    assert existing.read_bytes() == b"some other photo"
    assert store.uploads[upload_id].stored_path == ""


def test_mismatched_bytes_leave_everything_untouched(client):
    case_id, upload_id = _setup()
    store.items["itm_995ed6391131"] = EvidenceItem(
        id="itm_995ed6391131", case_id=case_id, name="Armchair",
        agent_item_id="itm_001", status=ItemStatus.human_corrected,
        revision=2, estimated_value=425.0, user_confirmed_fields=["estimated_value"],
    )
    before_upload = store.uploads[upload_id].model_dump(mode="json")
    before_item = store.items["itm_995ed6391131"].model_dump(mode="json")

    assert _restore(client, upload_id, OTHER_BYTES).status_code == 422

    assert store.uploads[upload_id].model_dump(mode="json") == before_upload
    assert store.items["itm_995ed6391131"].model_dump(mode="json") == before_item
    assert list(store.uploads) == [upload_id]          # no new records
    assert list(store.items) == ["itm_995ed6391131"]


# --------------------------------------------------------------------------- #
# Path safety / types
# --------------------------------------------------------------------------- #
def test_path_traversal_in_identifiers_is_rejected(client):
    store.cases["../evil"] = Case(id="../evil", title="x")
    store.uploads["upl_evil"] = Upload(
        id="upl_evil", case_id="../evil", kind=UploadKind.photo,
        original_filename="x.png", content_type="image/png",
        size_bytes=len(PNG_BYTES), sha256=_sha(PNG_BYTES), url="/x", stored_path="",
    )
    response = _restore(client, "upl_evil")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "UNSAFE_PATH"


def test_unsupported_type_is_rejected(client):
    store.cases["c1"] = Case(id="c1", title="x")
    store.uploads["upl_txt"] = Upload(
        id="upl_txt", case_id="c1", kind=UploadKind.photo,
        original_filename="notes.txt", content_type="text/plain",
        size_bytes=len(PNG_BYTES), sha256=_sha(PNG_BYTES), url="/x", stored_path="",
    )
    response = _restore(client, "upl_txt")
    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_empty_file_is_rejected(client):
    store.cases["c1"] = Case(id="c1", title="x")
    store.uploads["upl_empty"] = Upload(
        id="upl_empty", case_id="c1", kind=UploadKind.photo,
        original_filename="x.png", content_type="image/png",
        size_bytes=0, sha256=_sha(b""), url="/x", stored_path="",
    )
    response = _restore(client, "upl_empty")
    # size_bytes 0 is itself untrusted metadata
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# After restoration the photo is servable
# --------------------------------------------------------------------------- #
def test_local_photo_route_serves_exact_bytes(client):
    _, upload_id = _setup()
    _restore(client, upload_id)

    response = client.get(f"/api/uploads/{upload_id}/file")
    assert response.status_code == 200
    assert response.content == PNG_BYTES
    assert response.headers["content-type"] == "image/png"


def test_signed_public_route_serves_the_restored_image(client):
    from app.services import evidence_urls

    _, upload_id = _setup()
    _restore(client, upload_id)

    url = evidence_urls.upload_url(upload_id)
    path = url.split("example.ngrok.app", 1)[1]
    response = client.get(path)

    assert response.status_code == 200
    assert response.content == PNG_BYTES


def test_response_leaks_no_paths_or_secrets(client):
    _, upload_id = _setup()
    body = _restore(client, upload_id).text
    blob = json.loads(body)

    assert "uploads/" not in body
    assert str(settings.upload_dir) not in body
    assert TOKEN not in body
    assert "sig=" not in body
    assert set(blob) == {
        "upload_id", "case_id", "restored", "already_present",
        "size_bytes", "sha256", "content_type", "filename", "evidence_id",
    }


def test_no_creao_or_gmail_clients_are_built(client, monkeypatch):
    calls: list[str] = []

    class Boom:
        def __init__(self, *a, **k):
            calls.append("creao")

    monkeypatch.setattr("app.services.creao.CreaoAppClient", Boom)
    _, upload_id = _setup()
    assert _restore(client, upload_id).status_code == 200
    assert calls == []
