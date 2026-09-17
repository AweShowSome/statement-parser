"""PDF text extraction, plus the string->value primitives both banks share.

`pdf_lines()` is the subtle part of this project. Read its docstring before
changing anything in it; there is a regression test pinning the behaviour it
describes (tests/test_extraction.py).
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path
from typing import Optional

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    sys.exit("Missing dependency. Run:  pip install -e .")


# --------------------------------------------------------------------------
# Money and dates
# --------------------------------------------------------------------------

# Chase prints sub-dollar foreign-currency amounts with no leading zero (".88"),
# so the integer part has to be optional. There are 134 such rows in the corpus
# this parser was built against; a regex requiring a leading digit misses all
# of them silently.
MONEY = r"-?\$?\(?-?[\d,]*\.\d{2}\)?-?"
MONEY_RE = re.compile(MONEY)

MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1)
}


def to_money(s: str) -> Optional[float]:
    """'1,234.56' / '-$45.23' / '(45.23)' / '45.23-' -> float."""
    if s is None:
        return None
    t = s.strip()
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t.endswith("-"):
        neg, t = True, t[:-1]
    if t.startswith("-"):
        neg, t = True, t[1:]
    t = t.replace("$", "").replace(",", "").strip()
    if t.startswith("-"):
        neg, t = True, t[1:]
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def money_tokens(text: str) -> list:
    """All money values in `text` as (start, end, value).

    Handles Chase's detached minus sign ('- 1,150.00'), where the sign is laid
    down as its own word well to the left of the digits, and the trailing minus
    ('45.23-').
    """
    out = []
    for m in MONEY_RE.finditer(text):
        raw = m.group(0)
        val = to_money(raw)
        if val is None:
            continue
        if val >= 0 and re.search(r"-\s{0,3}$", text[:m.start()]):
            val = -val
        out.append((m.start(), m.end(), val))
    return out


def parse_slash(s: str) -> Optional[dt.date]:
    parts = s.split("/")
    if len(parts) != 3:
        return None
    try:
        mo, da, yr = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if yr < 100:
        yr += 2000
    try:
        return dt.date(yr, mo, da)
    except ValueError:
        return None


def resolve_year(mo: int, da: int, start: Optional[dt.date],
                 end: Optional[dt.date]) -> Optional[dt.date]:
    """Attach the right year to a bare MM/DD using the statement period.

    Statements print MM/DD only, so a December-to-January statement contains
    both 12/28 (last year) and 01/04 (this year). Each candidate year is scored
    by how far outside the statement period it would fall; anything inside the
    period wins outright.
    """
    if start is None and end is None:
        return None
    cands = set()
    for base in (start, end):
        if base:
            for y in (base.year - 1, base.year, base.year + 1):
                cands.add(y)
    best, best_pen = None, None
    lo = (start or end) - dt.timedelta(days=10)
    hi = (end or start) + dt.timedelta(days=10)
    for y in sorted(cands):
        try:
            d = dt.date(y, mo, da)
        except ValueError:
            continue          # 02/29 in a non-leap year
        if lo <= d <= hi:
            pen = 0
        else:
            pen = min(abs((d - lo).days), abs((d - hi).days)) + 1000
        if best_pen is None or pen < best_pen:
            best, best_pen = d, pen
    return best


# --------------------------------------------------------------------------
# Line classification
# --------------------------------------------------------------------------

def clean_desc(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip(" .-–—")
    return s.strip()


# Boilerplate that must never be mistaken for a transaction. Each entry below
# has been measured against a 34k-line corpus; the comment marks any that have
# never fired there, so they read as deliberately defensive rather than as
# something that is quietly doing work.
NOISE_PATTERNS = [
    r"page\s+\d+\s+of\s+\d+",        # page footers, which Chase puts mid-line
    r"statement\s+date\s*:",
    r"^\d+\s+of\s+\d+$",             # unseen; bare "1 of 2" without "page"
    r"^page\s+of$",
    r"^date of$",
    r"merchant name or transaction description",
    r"\(exchg\s+rate\)",
    r"^\d{1,3}\s+\d{1,3}$",          # split "Page 1 of 2" indicator
    r"^\d{12,}$",                    # statement barcode / tracking number
    r"^in case of errors",
    r"^this statement shows",        # unseen
    r"^interest paid\b",
    r"^annual percentage yield",
    r"^date\b.*\bdescription\b",
    r"^description\b.*\bamount\b",   # unseen
    r"^check\s*(no|number)",
    r"^date of\s+transaction",       # unseen
    r"^\(continued\)$",
    r"^total\b",
    r"^subtotal\b",                  # unseen
    r"jpmorgan chase bank",
    r"member fdic",
    r"equal opportunity lender",     # unseen
    r"^customer service",
    r"^deaf and hard of hearing",    # unseen
    r"^para espanol",                # unseen
    r"^www\.chase\.com",
    r"^account number\b",
    r"^beginning balance\b",
    r"^ending balance\b",
    r"^previous balance\b",
    r"^new balance\b",
    r"^minimum payment\b",
    r"^payment due date\b",
    r"^annual percentage rate",      # unseen
    r"^interest charge calculation",  # unseen
    r"^your annual percentage",
    r"^\*+$",
    r"^amount\s*$",
    r"^date\s*$",
    r"^balance\s*$",                 # unseen
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.I)


def is_noise(line: str) -> bool:
    t = line.strip()
    if not t:
        return True
    return bool(NOISE_RE.search(t))


def looks_like_header(line: str) -> bool:
    """A shouted section heading, e.g. 'ELECTRONIC WITHDRAWALS'."""
    t = line.strip()
    if len(t) < 3:
        return False
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(c.isupper() for c in letters) / len(letters)
    return upper_ratio > 0.85 and not MONEY_RE.search(t)


# --------------------------------------------------------------------------
# PDF -> text rows
# --------------------------------------------------------------------------

RE_MARKER = re.compile(r"\*\s*(start|end)\s*\*", re.I)
LINE_TOL = 3.0   # points; chars within this of a line's top are on that line


def _join(chars: list, x_tol: float) -> str:
    if not chars:
        return ""
    try:
        words = pdfplumber.utils.extract_words(
            sorted(chars, key=lambda c: c["x0"]),
            x_tolerance=x_tol, y_tolerance=LINE_TOL, keep_blank_chars=False)
        return " ".join(w["text"] for w in words)
    except Exception as exc:  # noqa: BLE001
        # The fallback concatenates characters with no spaces at all, which
        # poisons every regex downstream, so make the failure audible rather
        # than returning quietly broken text.
        print(f"warning: word extraction failed ({exc!r}); "
              f"falling back to raw character order", file=sys.stderr)
        return "".join(c.get("text", "") for c in sorted(chars, key=lambda c: c["x0"]))


def pdf_lines(path: Path, password: Optional[str], x_tol: float) -> list:
    """Text rows for one statement, in reading order.

    Chase tags each statement section with hidden `*start*` / `*end*` text. That
    marker text is often laid down on the *same baseline* as a real transaction
    -- 240 of the 1,102 marker baselines in the reference corpus -- so a plain
    extract_text() welds the two into one unreadable line, e.g.

        *end*electro0nic withdraw5al /11 Brokerage Transfer ... 8,000.00

    and an $8,000 withdrawal silently disappears. Running the reference corpus
    through plain extract_text() breaks 14 statements and loses 14 transactions.

    The fix: each baseline is split into runs of identical (fontname, size).
    Runs that spell a marker are emitted as their own marker lines; everything
    else on that baseline is regrouped and emitted as the content line.

    Note that the cut is font *identity*, not font size, and the reason is
    counter-intuitive: Chase draws body text at a bogus 0.24pt while the markers
    are a comparatively large 1.0pt. So the obvious "discard anything tiny"
    heuristic deletes the transactions and keeps the markers -- exactly
    backwards. In the reference corpus every marker run is ('Times-Roman', 1.0)
    and no content run ever uses that font or that size.

    Ordering within a baseline is start-marker, content, end-marker, so a
    transaction sharing a line with `*end*` still counts inside its section.
    """
    out: list = []
    kwargs = {"password": password} if password else {}
    with pdfplumber.open(str(path), **kwargs) as pdf:
        for page in pdf.pages:
            chars = [c for c in page.chars if c.get("text")]
            if not chars:
                text = page.extract_text(x_tolerance=x_tol, y_tolerance=3) or ""
                out.extend(text.split("\n"))
                continue

            chars.sort(key=lambda c: (c["top"], c["x0"]))
            rows: list = []
            row: list = []
            row_top = None
            for c in chars:
                if row_top is None or c["top"] - row_top <= LINE_TOL:
                    if row_top is None:
                        row_top = c["top"]
                    row.append(c)
                else:
                    rows.append(row)
                    row, row_top = [c], c["top"]
            if row:
                rows.append(row)

            items: list = []
            for row in rows:
                top = row[0]["top"]
                styles: dict = {}
                for c in row:
                    styles.setdefault(
                        (c.get("fontname"), round(c.get("size") or 0, 2)), []).append(c)

                content: list = []
                for _, run in styles.items():
                    txt = _join(run, x_tol)
                    if RE_MARKER.search(txt):
                        order = 0 if re.search(r"\*\s*start", txt, re.I) else 2
                        items.append((top, order, txt))
                    else:
                        content.extend(run)
                text = _join(content, x_tol)
                if text.strip():
                    items.append((top, 1, text))

            items.sort(key=lambda it: (it[0], it[1]))
            out.extend(txt for _, _, txt in items)
    return out
