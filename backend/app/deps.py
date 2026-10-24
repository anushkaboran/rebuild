"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import HTTPException, status

from .models import Case, EvidenceItem, Upload
from .store import store


def get_case_or_404(case_id: str) -> Case:
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Case '{case_id}' not found."
        )
    return case


def get_upload_or_404(upload_id: str) -> Upload:
    upload = store.uploads.get(upload_id)
    if upload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Upload '{upload_id}' not found."
        )
    return upload


def get_item_or_404(item_id: str) -> EvidenceItem:
    item = store.items.get(item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Item '{item_id}' not found."
        )
    return item
