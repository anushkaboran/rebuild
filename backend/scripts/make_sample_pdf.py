"""Generate a sample recovery-record PDF from the demo seed (no server needed).

Usage: cd backend && python scripts/make_sample_pdf.py ../../sample-recovery-record.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.routers.demo import seed_demo  # noqa: E402
from app.services import pdf  # noqa: E402
from app.store import store  # noqa: E402
from app.models import Revision, utcnow  # noqa: E402
from app.store import new_id  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "sample-recovery-record.pdf")
    result = seed_demo()
    case = store.cases[result["case_id"]]
    items = store.list_items(case.id)

    # A couple of audit rows so the "correction history" page has content.
    for item in items[:2]:
        rev = Revision(
            id=new_id("rev"),
            item_id=item.id,
            editor=item.updated_by or "reviewer",
            note="Checked against receipt",
            changes={"condition": item.condition, "estimated_value": item.estimated_value},
        )
        store.revisions[rev.id] = rev

    blob = pdf.build_case_pdf(
        case=case,
        items=items,
        uploads=store.list_uploads(case.id),
        investigations=store.list_investigations(case.id),
        revisions=store.revisions.values(),
    )
    out.write_bytes(blob)
    print(f"wrote {out} ({len(blob)} bytes)")


if __name__ == "__main__":
    main()
