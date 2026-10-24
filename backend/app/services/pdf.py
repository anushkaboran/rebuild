"""PDF export of the evidence-linked recovery record.

Produces a printable packet: cover block, summary numbers, one row per
belonging with its source photo and review status, the correction audit trail,
and the list of contributors. Every item references the upload it came from, so
the record stays traceable back to the original photo or receipt.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Iterable, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models import Case, EvidenceItem, Investigation, Revision, Upload

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#d1d5db")
ACCENT = colors.HexColor("#0f766e")
ZEBRA = colors.HexColor("#f3f4f6")

DAMAGE_LABEL = {
    "none": "None",
    "light": "Light",
    "moderate": "Moderate",
    "severe": "Severe",
    "destroyed": "Destroyed",
    "unknown": "Unknown",
}

STATUS_LABEL = {
    "ai_suggested": "AI suggested — awaiting review",
    "human_confirmed": "Human confirmed",
    "human_corrected": "Human corrected",
    "rejected": "Rejected by reviewer",
}


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "RTitle", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=22, leading=26, textColor=INK, alignment=TA_LEFT, spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "RSub", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=13, textColor=MUTED,
        ),
        "h2": ParagraphStyle(
            "RH2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=12.5, leading=16, textColor=ACCENT, spaceBefore=14, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "RBody", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=13, textColor=INK,
        ),
        "small": ParagraphStyle(
            "RSmall", parent=base["Normal"], fontName="Helvetica",
            fontSize=8, leading=11, textColor=MUTED,
        ),
        "cell": ParagraphStyle(
            "RCell", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.5, leading=11, textColor=INK,
        ),
        "cellbold": ParagraphStyle(
            "RCellB", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=8.5, leading=11, textColor=INK,
        ),
        "head": ParagraphStyle(
            "RHead", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=8, leading=10, textColor=colors.white,
        ),
    }


def _money(value: float | None, currency: str = "USD") -> str:
    if value is None:
        return "—"
    return f"{currency} {value:,.0f}"


def build_case_pdf(
    *,
    case: Case,
    items: Sequence[EvidenceItem],
    uploads: Sequence[Upload],
    investigations: Sequence[Investigation],
    revisions: Iterable[Revision],
) -> bytes:
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=f"Rebuild recovery record — {case.title}",
        author="Rebuild",
        subject="Post-flood recovery evidence record",
    )

    revision_list = list(revisions)
    upload_index = {u.id: u for u in uploads}
    contributors = sorted({r.editor for r in revision_list if r.editor})

    story: list = []
    story.append(Paragraph("Rebuild — Recovery Evidence Record", styles["title"]))
    story.append(
        Paragraph(
            f"Case {case.id} · generated "
            f"{datetime.now(timezone.utc).strftime('%d %b %Y, %H:%M UTC')}",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1, color=RULE))
    story.append(Spacer(1, 10))

    # --- case block -------------------------------------------------------- #
    meta_rows = [
        ["Property", case.title, "Status", case.status.value],
        ["Address", case.address or "Not recorded", "Disaster", case.disaster_type],
        ["Opened by", case.created_by, "Opened", case.created_at.strftime("%d %b %Y")],
    ]
    meta_table = Table(
        [[Paragraph(f"<b>{a}</b>", styles["cell"]),
          Paragraph(str(b), styles["cell"]),
          Paragraph(f"<b>{c}</b>", styles["cell"]),
          Paragraph(str(d), styles["cell"])] for a, b, c, d in meta_rows],
        colWidths=[0.9 * inch, 2.6 * inch, 0.9 * inch, 2.6 * inch],
    )
    meta_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
            ]
        )
    )
    story.append(meta_table)

    if case.notes:
        story.append(Spacer(1, 8))
        story.append(Paragraph("<b>Case notes.</b> " + _escape(case.notes), styles["body"]))

    # --- summary ----------------------------------------------------------- #
    reviewed = sum(1 for i in items if i.status != "ai_suggested")
    total = sum(i.estimated_value or 0 for i in items)
    currency = next((i.currency for i in items if i.currency), "USD")

    story.append(Paragraph("Summary", styles["h2"]))
    summary_rows = [
        ["Belongings recorded", str(len(items)), "Source uploads", str(len(uploads))],
        ["Reviewed by a person", f"{reviewed} of {len(items)}", "Agent runs", str(len(investigations))],
        ["Estimated replacement cost", _money(total, currency), "Contributors", str(len(contributors)) or "1"],
    ]
    summary_table = Table(
        [[Paragraph(f"<b>{a}</b>", styles["cell"]),
          Paragraph(str(b), styles["cell"]),
          Paragraph(f"<b>{c}</b>", styles["cell"]),
          Paragraph(str(d), styles["cell"])] for a, b, c, d in summary_rows],
        colWidths=[1.7 * inch, 1.8 * inch, 1.3 * inch, 2.2 * inch],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, -1), ZEBRA),
                ("BOX", (0, 0), (-1, -1), 0.4, RULE),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(summary_table)

    # --- items ------------------------------------------------------------- #
    story.append(Paragraph("Belongings", styles["h2"]))
    if not items:
        story.append(Paragraph("No belongings have been recorded yet.", styles["body"]))
    else:
        header = [
            Paragraph("Belonging", styles["head"]),
            Paragraph("Room", styles["head"]),
            Paragraph("Damage", styles["head"]),
            Paragraph("Value", styles["head"]),
            Paragraph("Review status", styles["head"]),
            Paragraph("Evidence", styles["head"]),
        ]
        rows: list[list] = [header]
        for item in items:
            upload = upload_index.get(item.upload_id or "")
            evidence = (
                f"{upload.kind.value}: {upload.original_filename}"
                if upload
                else ("manual entry" if not item.upload_id else "unavailable")
            )
            rows.append(
                [
                    Paragraph(_escape(item.name), styles["cellbold"]),
                    Paragraph(_escape(item.room or "—"), styles["cell"]),
                    Paragraph(DAMAGE_LABEL.get(item.damage_level.value, item.damage_level.value), styles["cell"]),
                    Paragraph(_money(item.estimated_value, item.currency), styles["cell"]),
                    Paragraph(STATUS_LABEL.get(item.status.value, item.status.value), styles["cell"]),
                    Paragraph(_escape(evidence), styles["small"]),
                ]
            )

        table = Table(
            rows,
            colWidths=[1.55 * inch, 0.85 * inch, 0.8 * inch, 0.95 * inch, 1.55 * inch, 1.3 * inch],
            repeatRows=1,
        )
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), INK),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.35, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for index in range(1, len(rows)):
            if index % 2 == 0:
                style.append(("BACKGROUND", (0, index), (-1, index), ZEBRA))
        table.setStyle(TableStyle(style))
        story.append(table)
        story.append(Spacer(1, 4))
        story.append(
            Paragraph(
                "Each row is linked to the source upload listed under Evidence. "
                "Rows marked AI suggested have not yet been reviewed by a person.",
                styles["small"],
            )
        )

    # --- audit trail ------------------------------------------------------- #
    if revision_list:
        story.append(PageBreak())
        story.append(Paragraph("Correction history", styles["h2"]))
        story.append(
            Paragraph(
                "Every human change to a belonging is recorded here, including the "
                "editor, the fields changed and any note they left.",
                styles["small"],
            )
        )
        story.append(Spacer(1, 8))

        item_names = {i.id: i.name for i in items}
        trail_header = [
            Paragraph("When", styles["head"]),
            Paragraph("Belonging", styles["head"]),
            Paragraph("Editor", styles["head"]),
            Paragraph("Changed", styles["head"]),
        ]
        trail_rows: list[list] = [trail_header]
        for revision in sorted(revision_list, key=lambda r: r.created_at):
            changed = ", ".join(sorted(revision.changes.keys())) or "—"
            if revision.note:
                changed += f" — {revision.note}"
            trail_rows.append(
                [
                    Paragraph(revision.created_at.strftime("%d %b, %H:%M"), styles["cell"]),
                    Paragraph(_escape(item_names.get(revision.item_id, revision.item_id)), styles["cell"]),
                    Paragraph(_escape(revision.editor), styles["cell"]),
                    Paragraph(_escape(changed), styles["small"]),
                ]
            )

        trail = Table(trail_rows, colWidths=[0.95 * inch, 1.7 * inch, 1.1 * inch, 3.25 * inch], repeatRows=1)
        trail.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), INK),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.35, RULE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(trail)

    # --- contributors ------------------------------------------------------ #
    if contributors:
        story.append(Paragraph("Contributors", styles["h2"]))
        story.append(Paragraph(_escape(", ".join(contributors)), styles["body"]))

    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=0.6, color=RULE))
    story.append(
        Paragraph(
            "Generated by Rebuild. Findings marked AI suggested are machine "
            "estimates and require human review before submission to an insurer "
            "or relief agency. Keep the original photographs and receipts with "
            "this record.",
            styles["small"],
        )
    )

    doc.build(story)
    return buffer.getvalue()


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
