"""Excel export: dated file with clickable apply links."""

from __future__ import annotations

import logging
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from src.models import JobRecord, today_iso

log = logging.getLogger(__name__)

COLUMNS = [
    ("Fit Score", 12),
    ("Title", 40),
    ("Company", 28),
    ("Location", 22),
    ("Source", 22),
    ("Language", 12),
    ("Posted Date", 14),
    ("Fit Reason", 60),
    ("Apply Link", 40),
]


def export_excel(jobs: list[JobRecord], exports_dir: str) -> Path:
    Path(exports_dir).mkdir(parents=True, exist_ok=True)
    out = Path(exports_dir) / f"internships_{today_iso()}.xlsx"

    # Already sorted by fit_score desc from store/rank
    ranked = sorted(jobs, key=lambda j: j.fit_score, reverse=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Internships"

    header_font = Font(bold=True)
    for col_idx, (name, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=name)
        cell.font = header_font
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.freeze_panes = "A2"

    for row_idx, job in enumerate(ranked, start=2):
        ws.cell(row=row_idx, column=1, value=job.fit_score)
        ws.cell(row=row_idx, column=2, value=job.title)
        ws.cell(row=row_idx, column=3, value=job.company)
        ws.cell(row=row_idx, column=4, value=job.location)
        ws.cell(row=row_idx, column=5, value=job.source)
        ws.cell(row=row_idx, column=6, value=job.language)
        ws.cell(row=row_idx, column=7, value=job.posted_date or "")
        ws.cell(row=row_idx, column=8, value=job.fit_reason)
        link_cell = ws.cell(row=row_idx, column=9, value=job.url)
        if job.url:
            link_cell.hyperlink = job.url
            link_cell.font = Font(color="0563C1", underline="single")

    wb.save(out)
    log.info("Excel exported: %s (%s rows)", out, len(ranked))
    return out
