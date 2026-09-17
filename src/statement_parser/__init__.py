"""Turn bank statement PDFs into clean transaction CSVs.

Public API:

    from statement_parser import parse_statement
    result = parse_statement(Path("statement.pdf"), "chase", None, 1.5)
    result.txns          # list[Txn]
    result.reconciles    # True / False / None
"""

from __future__ import annotations

import re
from pathlib import Path

from .chase import parse_chase
from .extraction import pdf_lines
from .models import COLUMNS, BalanceCheck, FileResult, Txn
from .summit import parse_summit

__version__ = "1.0.0"

__all__ = [
    "COLUMNS",
    "BalanceCheck",
    "FileResult",
    "Txn",
    "detect_institution",
    "parse_statement",
    "__version__",
]


def detect_institution(lines: list) -> str:
    blob = "\n".join(lines[:250]).lower()
    if "summit credit union" in blob or "summitcreditunion.com" in blob:
        return "summit"
    if "jpmorgan chase" in blob or "chase.com" in blob or "*start*" in blob:
        return "chase"
    if re.search(r"member statement of account", blob):
        return "summit"
    return ""


def parse_statement(path: Path, folder_label: str, password: str | None,
                    x_tol: float, debug: bool = False) -> FileResult:
    """Parse one statement PDF. Never raises for a bad file -- the problem is
    recorded on `FileResult.error` so one unreadable statement cannot abort a
    whole run."""
    res = FileResult(path=path)
    try:
        lines = pdf_lines(path, password, x_tol)
    except Exception as exc:  # noqa: BLE001
        res.error = f"could not read PDF: {exc}"
        return res
    if not any(line.strip() for line in lines):
        res.error = "no extractable text (scanned image? run OCR first)"
        return res

    res.institution = detect_institution(lines)
    if res.institution == "summit":
        parse_summit(path, folder_label, lines, res)
    elif res.institution == "chase":
        parse_chase(path, folder_label, lines, res)
    else:
        res.error = "unrecognized format (not a Chase or Summit statement)"

    if debug:
        print(f"  [debug] {path.name}: {len(res.txns)} rows, "
              f"{res.institution or '?'} / {res.account_type}, period={res.period}")
    return res
