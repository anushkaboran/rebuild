"""Opt-in network test: Person 2's staged demo evidence must be fetchable.

This is the only test that touches the public internet, so it is skipped unless
`CREAO_NETWORK_TESTS=1` is set. It proves the URL-based evidence channel end to
end from *our* side: the four staged files resolve over HTTPS and come back with
readable image content types — exactly the check the backend runs before it
creates a real run.

Run it with:

    CREAO_NETWORK_TESTS=1 pytest tests/test_creao_network.py -v
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.services import creao, evidence_urls

pytestmark = pytest.mark.skipif(
    os.environ.get("CREAO_NETWORK_TESTS") != "1",
    reason="set CREAO_NETWORK_TESTS=1 to exercise the public demo URLs",
)

DEMO_FILES = (
    ("01_room_armchair.jpg", "photo"),
    ("02_armchair_closeup.jpg", "photo"),
    ("04_receipt_lamp.png", "receipt"),
    ("05_receipt_groceries.png", "receipt"),
)


def _demo_urls() -> list[str]:
    base = creao.settings.creao_demo_evidence_base_url.rstrip("/")
    return [f"{base}/{name}" for name, _ in DEMO_FILES]


def test_staged_demo_urls_are_reachable_and_readable():
    checks = asyncio.run(evidence_urls.check_urls(_demo_urls()))

    assert len(checks) == 4
    failures = [(c.url, c.detail) for c in checks if not c.ok]
    assert not failures, f"staged demo evidence is not fetchable: {failures}"

    for check in checks:
        assert check.content_type.startswith("image/"), check.content_type


def test_demo_transport_builds_exactly_person2_urls():
    evidence = [
        creao.EvidenceFile(
            evidence_id=f"ev_{i:03d}",
            filename=name,
            content_type="image/jpeg",
            kind=kind,
        )
        for i, (name, kind) in enumerate(DEMO_FILES, start=1)
    ]
    for item in evidence:
        item.url = creao._resolve_evidence_url(item, "demo_url")

    assert [e.url for e in evidence if e.kind == "photo"] == _demo_urls()[:2]
    assert [e.url for e in evidence if e.kind == "receipt"] == _demo_urls()[2:]
