"""CSV, XLSX and the human-readable parse report."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from .models import COLUMNS


def write_csv(rows: list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            out = dict(r)
            for col in ("amount", "balance"):
                v = out.get(col)
                out[col] = f"{v:.2f}" if isinstance(v, (int, float)) else ""
            w.writerow(out)


def write_xlsx(groups: dict, path: Path) -> bool:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        return False

    wb = Workbook()
    wb.remove(wb.active)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F3864")

    all_rows = [r for rows in groups.values() for r in rows]
    sheets = list(groups.items())
    if len(sheets) > 1:
        sheets.append(("All", all_rows))

    widths = {"institution": 12, "date": 12, "post_date": 12,
              "description": 52, "amount": 13, "type": 9, "balance": 14,
              "account": 14, "account_type": 13, "check_no": 10,
              "category": 22, "statement_period": 24,
              "source_file": 34, "source_folder": 18}

    used = set()
    for name, rows in sheets:
        title = re.sub(r"[\[\]:*?/\\]", "_", str(name))[:31] or "Sheet"
        base, n = title, 2
        while title.lower() in used:
            title = f"{base[:28]}_{n}"
            n += 1
        used.add(title.lower())

        ws = wb.create_sheet(title)
        ws.append(COLUMNS)
        for c in ws[1]:
            c.font = header_font
            c.fill = header_fill
            c.alignment = Alignment(horizontal="center")
        for r in rows:
            ws.append([r.get(c) for c in COLUMNS])

        for i, col in enumerate(COLUMNS, start=1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(col, 14)
        for col in ("amount", "balance"):
            letter = get_column_letter(COLUMNS.index(col) + 1)
            for cell in ws[letter][1:]:
                cell.number_format = '#,##0.00;[Red]-#,##0.00'
        ws.freeze_panes = "A2"
        if ws.max_row >= 1:
            ws.auto_filter.ref = \
                f"A1:{get_column_letter(len(COLUMNS))}{max(ws.max_row, 1)}"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return True


def write_report(results: list, path: Path) -> str:
    """Per-file row counts and balance checks."""
    lines = []
    total = 0
    for folder, res_list in results:
        lines.append(f"\n=== {folder} ===")
        for r in res_list:
            if r.error:
                lines.append(f"  [SKIP] {r.path.name}: {r.error}")
                continue
            total += len(r.txns)
            rec = r.reconciles
            if rec is True:
                flag = "balanced"
            elif rec is False:
                flag = f"CHECK: {r.failures()}"
            else:
                flag = "no balance totals on statement"
            lines.append(
                f"  {r.path.name}: {len(r.txns):>4} txns | {r.institution or '?'} "
                f"{r.account_type} {r.account} | "
                f"{r.period or 'period not found'} | {flag}")
    lines.append(f"\nTOTAL TRANSACTIONS: {total}")
    text = "\n".join(lines).strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return text
