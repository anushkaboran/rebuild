"""Unicode-safe download headers.

`signed public evidence URL → 500` was caused by writing `original_filename`
straight into a `Content-Disposition` header. HTTP/1.1 header values are
latin-1; the filename `Screenshot … 11.31.08\u202fPM.png` contains U+202F and the
ASGI encoder raised `UnicodeEncodeError` after the response had begun.

These tests go through a real ASGI response (TestClient), so the encoding error
is detectable — asserting on a mocked header would not catch it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from urllib.parse import unquote

from app.config import settings
from app.main import app
from app.models import Case, EvidenceItem, Upload, UploadKind
from app.services import evidence_urls, storage
from app.store import store

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "8900000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)

NARROW_NO_BREAK = "Screenshot 2026-10-24 at 11.31.08\u202fPM.png"
ACCENTED = "Rechnung Küche – Beleg.pdf"
CJK = "客厅照片.png"
EMOJI = "flood-damage-\U0001f30a.png"
ASCII = "room.png"


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


def _upload(client, filename: str, data: bytes = PNG_BYTES) -> tuple[str, str]:
    case_id = client.post("/api/cases", json={"title": "unicode"}).json()["id"]
    created = client.post(
        f"/api/cases/{case_id}/uploads",
        files={"file": (filename, data, "image/png")},
        data={"kind": "photo"},
    ).json()
    return case_id, created["id"]


def _signed_path(upload_id: str) -> str:
    return evidence_urls.upload_url(upload_id).split("example.ngrok.app", 1)[1]


def _assert_header_safe(value: str) -> None:
    """The header must survive latin-1 encoding — the actual bug."""
    assert value.encode("latin-1"), "header is not latin-1 encodable"
    assert "\u202f" not in value or "filename*=" in value
    assert "\r" not in value and "\n" not in value


# --------------------------------------------------------------------------- #
# The exact failing filename
# --------------------------------------------------------------------------- #
def test_signed_public_route_serves_u202f_filename(client):
    case_id, upload_id = _upload(client, NARROW_NO_BREAK)

    response = client.get(_signed_path(upload_id))

    assert response.status_code == 200, response.text
    assert response.content == PNG_BYTES
    assert response.headers["content-type"] == "image/png"

    disposition = response.headers["content-disposition"]
    _assert_header_safe(disposition)
    assert disposition.startswith("inline;")
    assert 'filename="Screenshot 2026-10-24 at 11.31.08 PM.png"' in disposition
    assert "filename*=UTF-8''" in disposition
    assert "%E2%80%AF" in disposition          # the U+202F, percent-encoded

    # Metadata keeps the exact original name.
    assert store.uploads[upload_id].original_filename == NARROW_NO_BREAK


def test_local_upload_route_serves_u202f_filename(client):
    case_id, upload_id = _upload(client, NARROW_NO_BREAK)

    response = client.get(f"/api/uploads/{upload_id}/file")

    assert response.status_code == 200
    assert response.content == PNG_BYTES
    disposition = response.headers["content-disposition"]
    _assert_header_safe(disposition)
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition


# --------------------------------------------------------------------------- #
# Other non-ASCII names
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("filename", [ACCENTED, CJK, EMOJI, ASCII])
def test_all_filenames_download_correctly(client, filename):
    _, upload_id = _upload(client, filename)

    signed = client.get(_signed_path(upload_id))
    local = client.get(f"/api/uploads/{upload_id}/file")

    for response in (signed, local):
        assert response.status_code == 200, (filename, response.text)
        assert response.content == PNG_BYTES
        assert response.headers["content-type"] == "image/png"
        _assert_header_safe(response.headers["content-disposition"])

    assert store.uploads[upload_id].original_filename == filename


def test_signed_and_local_agree_on_the_original_name(client):
    _, upload_id = _upload(client, ACCENTED)
    signed = client.get(_signed_path(upload_id)).headers["content-disposition"]
    local = client.get(f"/api/uploads/{upload_id}/file").headers["content-disposition"]

    def star(header: str) -> str:
        return unquote(header.split("filename*=UTF-8''")[1])

    assert star(signed) == ACCENTED
    assert star(local) == ACCENTED


def test_ascii_name_has_no_star_parameter(client):
    _, upload_id = _upload(client, ASCII)
    disposition = client.get(_signed_path(upload_id)).headers["content-disposition"]
    assert disposition == 'inline; filename="room.png"'


# --------------------------------------------------------------------------- #
# Helper behaviour
# --------------------------------------------------------------------------- #
def test_ascii_fallback_is_readable():
    assert storage.ascii_filename(NARROW_NO_BREAK) == "Screenshot 2026-10-24 at 11.31.08 PM.png"
    assert storage.ascii_filename(ACCENTED) == "Rechnung Kuche Beleg.pdf"
    assert storage.ascii_filename(CJK) == "download.png"
    assert storage.ascii_filename(EMOJI) == "flood-damage.png"
    assert storage.ascii_filename(ASCII) == "room.png"


def test_header_injection_characters_are_neutralised():
    header = storage.content_disposition('evil"\r\nX-Injected: 1.png')
    _assert_header_safe(header)
    assert "X-Injected" in header          # inside the quoted value only
    assert "\r" not in header and "\n" not in header


def test_empty_filename_falls_back():
    assert storage.content_disposition(None) == 'attachment; filename="download"'


# --------------------------------------------------------------------------- #
# Nothing else changes
# --------------------------------------------------------------------------- #
def test_ids_and_evidence_links_are_untouched(client):
    case_id, upload_id = _upload(client, NARROW_NO_BREAK)
    before = store.uploads[upload_id]
    item = client.post(
        f"/api/cases/{case_id}/items",
        json={"name": "Armchair", "editor": "priya"},
    ).json()
    client.get(_signed_path(upload_id))

    after = store.uploads[upload_id]
    assert after.id == before.id
    assert after.case_id == before.case_id
    assert after.evidence_id == before.evidence_id
    assert after.size_bytes == before.size_bytes
    assert after.sha256 == before.sha256
    assert client.get(f"/api/items/{item['id']}").json()["id"] == item["id"]


def test_no_creao_or_gmail_calls(client, monkeypatch):
    calls: list[str] = []

    class Boom:
        def __init__(self, *a, **k):
            calls.append("creao")

    monkeypatch.setattr("app.services.creao.CreaoAppClient", Boom)
    _, upload_id = _upload(client, NARROW_NO_BREAK)
    assert client.get(_signed_path(upload_id)).status_code == 200
    assert calls == []
