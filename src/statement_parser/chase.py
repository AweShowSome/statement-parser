"""Chase statements: personal/business checking & savings, and credit cards.

Chase lays out a statement as a run of named sections (DEPOSITS AND ADDITIONS,
CHECKS PAID, ELECTRONIC WITHDRAWALS, ...), each delimited by hidden `*start*` /
`*end*` markers. The section a row sits in is what tells us whether it is money
in or money out, because the printed amounts are unsigned.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Optional

from .extraction import (
    MONEY,
    MONTHS,
    clean_desc,
    is_noise,
    looks_like_header,
    money_tokens,
    parse_slash,
    resolve_year,
    to_money,
)
from .models import BalanceCheck, FileResult, Txn

# --------------------------------------------------------------------------
# Section mapping
# --------------------------------------------------------------------------
# Each section maps to (category, sign), where sign is the direction a normally
# printed (unsigned) amount takes. sign None => the printed amount is already
# signed and should be used as-is.

CHECKING_SECTIONS = {
    "transaction detail": ("transaction_detail", None),
    "deposits and additions": ("deposits", +1),
    "checks paid": ("checks_paid", -1),
    "atm & debit card withdrawals": ("card_withdrawals", -1),
    "atm debit withdrawals": ("card_withdrawals", -1),
    "atm & debit card withdrawal": ("card_withdrawals", -1),
    "electronic withdrawals": ("electronic_withdrawals", -1),
    "other withdrawals": ("other_withdrawals", -1),
    "withdrawals and debits": ("other_withdrawals", -1),
    "fees": ("fees", -1),
    "fees and other withdrawals": ("fees", -1),
    "other fees": ("fees", -1),
    "interest": ("interest", +1),
}

CREDIT_SECTIONS = {
    "account activity": ("activity", None),
    "payments and other credits": ("payments_credits", None),
    "purchase": ("purchases", None),
    "purchases": ("purchases", None),
    "cash advances": ("cash_advances", None),
    "balance transfers": ("balance_transfers", None),
    "fees charged": ("fees", None),
    "interest charged": ("interest", None),
    "transactions this cycle": ("activity", None),
}

SKIP_SECTIONS = {
    "checking summary", "savings summary", "daily ending balance",
    "service charge summary", "atm & debit card summary",
    "account summary", "summary of account activity", "overdraft",
    "important information", "rewards summary", "totals year-to-date",
    "year-to-date", "chase business", "interest charge calculation",
    "daily balance", "monthly service fee",
}


def match_section(name: str, table: dict) -> Optional[tuple]:
    """Map a heading to (category, sign), tolerating Chase's wording drift.

    Exact matches win. Failing that a prefix match in either direction is
    accepted, but only for a name long enough to be distinctive -- the length
    guard applies to BOTH directions, so a stray short word cannot open a
    section.
    """
    n = re.sub(r"[^a-z& ]", "", name.lower()).strip()
    n = re.sub(r"\s+", " ", n)
    if n in table:
        return table[n]
    for key, val in table.items():
        if (n.startswith(key) or key.startswith(n)) and len(n) > 6:
            return val
    return None


def is_skip_section(name: str) -> bool:
    n = re.sub(r"[^a-z& ]", "", name.lower()).strip()
    return any(n.startswith(s) or s in n for s in SKIP_SECTIONS)


# --------------------------------------------------------------------------
# Statement metadata
# --------------------------------------------------------------------------

def detect_account_type(lines: list) -> str:
    blob = "\n".join(lines[:200]).lower()
    credit_hits = sum(
        k in blob for k in
        ("minimum payment due", "payment due date", "previous balance",
         "opening/closing date", "account activity", "credit limit",
         "cash advances", "purchases interest charge"))
    check_hits = sum(
        k in blob for k in
        ("checking summary", "transaction detail", "deposits and additions",
         "electronic withdrawals", "beginning balance", "checks paid",
         "total checking", "business complete checking"))
    save_hits = sum(k in blob for k in ("savings summary", "chase savings",
                                        "premier savings", "interest paid"))
    if credit_hits > check_hits and credit_hits >= 2:
        return "credit_card"
    if save_hits >= 2 and save_hits > check_hits:
        return "savings"
    if check_hits:
        return "checking"
    return "credit_card" if credit_hits else "unknown"


def find_period(lines: list) -> tuple:
    """Return (start_date, end_date, label) -- dates may be None."""
    blob = "\n".join(lines[:250])

    # "January 03, 2025 through February 02, 2025"
    m = re.search(
        r"([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})\s+through\s+"
        r"([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})", blob)
    if m:
        try:
            s = dt.date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
            e = dt.date(int(m.group(6)), MONTHS[m.group(4).lower()], int(m.group(5)))
            return s, e, f"{s.isoformat()} to {e.isoformat()}"
        except (KeyError, ValueError):
            pass

    # "Opening/Closing Date 12/16/24 - 01/15/25"
    m = re.search(
        r"Opening/?Closing\s+Date\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})\s*[-–]\s*"
        r"(\d{1,2}/\d{1,2}/\d{2,4})", blob, re.I)
    if not m:
        m = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{2,4})",
                      blob)
    if m:
        s, e = parse_slash(m.group(1)), parse_slash(m.group(2))
        if s and e and e >= s:
            return s, e, f"{s.isoformat()} to {e.isoformat()}"

    # "Statement Period 01/01/2025 to 01/31/2025"
    m = re.search(r"Statement\s+(?:Period|Date)[^\d]{0,12}(\d{1,2}/\d{1,2}/\d{2,4})",
                  blob, re.I)
    if m:
        d = parse_slash(m.group(1))
        if d:
            return d, d, d.isoformat()
    return None, None, ""


def find_account(lines: list) -> str:
    """The masked account number, e.g. '...4321'."""
    blob = "\n".join(lines[:250])
    pats = [
        r"Account\s+Number\s*:?\s*([\dXx\*\- ]{6,25})",
        r"Primary\s+Account\s*:?\s*([\dXx\*\- ]{6,25})",
        r"Account\s*:?\s*([\dXx\*\- ]{8,25})",
    ]
    for p in pats:
        m = re.search(p, blob)
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if len(digits) >= 4:
                return "..." + digits[-4:]
    m = re.search(r"[Xx\*]{4,}\s*(\d{4})\b", blob)
    if m:
        return "..." + m.group(1)
    return ""


# --------------------------------------------------------------------------
# Line-level transaction matching
# --------------------------------------------------------------------------

# A record starts either with a date, or with a check number followed by a date.
RE_REC_DATE = re.compile(r"^(\d{1,2}/\d{1,2})(?:\s|$)")
RE_REC_2DATE = re.compile(r"^(\d{1,2}/\d{1,2})\s+(\d{1,2}/\d{1,2})(?:\s|$)")
RE_REC_CHECK = re.compile(r"^(\d{2,7})\s*\^?\s+(\d{1,2}/\d{1,2})(?:\s|$)")

CONTINUATION_MAX = 4


def starts_record(line: str) -> bool:
    return bool(RE_REC_DATE.match(line) or RE_REC_CHECK.match(line))


def apply_sign(amount: float, sign: Optional[int], is_credit: bool) -> float:
    if sign is not None:
        return abs(amount) * sign
    if is_credit:
        # credit card: printed purchases are positive charges -> spend negative
        return -amount
    return amount


def _ends_with_money(text: str, toks: list) -> bool:
    return bool(toks) and toks[-1][1] >= len(text.rstrip()) - 1


def split_inner_date(desc: str, start, end) -> tuple:
    """Chase card purchases read 'Card Purchase 01/04 MERCHANT ...' -- pull the
    inner date out as post/transaction date and keep the text clean."""
    m = re.match(
        r"^((?:Recurring\s+)?Card\s+Purchase(?:\s+With\s+Pin)?(?:\s+Return)?"
        r"|ATM\s+Withdrawal|ATM\s+Cash\s+Deposit|Debit\s+Card\s+Purchase)"
        r"\s+(\d{1,2}/\d{1,2})\s+(.*)$",
        desc.strip(), re.I)
    if m:
        d = resolve_year(*[int(x) for x in m.group(2).split("/")], start, end)
        return clean_desc(f"{m.group(1)} {m.group(3)}"), (d.isoformat() if d else "")
    return clean_desc(desc), ""


def build_txn(rec_lines: list, section: Optional[str], sign: Optional[int],
              is_credit: bool, start, end, new_txn) -> Optional[Txn]:
    """Parse one buffered record (a transaction plus any wrapped lines).

    Chase wraps in two different ways and they need opposite handling:

      amount at the end of the FIRST line, detail wrapping below --
        03/19 Domestic Wire Transfer Via: Example Bank ...   12,345.00
        Ref: For Further Credit To ... Trn: 3484686078Es

      description on the first line, amount pushed onto the NEXT line --
        12/22 CONTINENTAL AIR 0162483920164 800-555-0142 TX
        548.60

    So: if the head line already ends in money, parse the head and treat the
    rest as trailing description. Otherwise parse the whole joined record.

    """
    if not rec_lines or section is None:
        return None

    head = re.sub(r"\s+", " ", rec_lines[0]).strip()
    joined = re.sub(r"\s+", " ", " ".join(rec_lines)).strip()

    head_toks = money_tokens(head)
    if _ends_with_money(head, head_toks):
        text, toks = head, head_toks
        tail = clean_desc(re.sub(r"\s+", " ", " ".join(rec_lines[1:])))
    else:
        text, toks = joined, money_tokens(joined)
        if not _ends_with_money(text, toks):
            return None       # no amount anywhere -- this is prose, not a row
        tail = ""

    check_no = ""
    post_date = ""
    m2 = RE_REC_2DATE.match(text)
    mc = RE_REC_CHECK.match(text)
    m1 = RE_REC_DATE.match(text)

    if mc and section == "checks_paid":
        check_no = mc.group(1)
        date_str = mc.group(2)
        head_end = mc.end()
    elif m2:
        # "08/07 08/07 Online Transfer To Chk ..." -- Chase prints the posting
        # date, then the date the transaction was initiated.
        date_str = m2.group(1)
        d2 = resolve_year(*[int(x) for x in m2.group(2).split("/")], start, end)
        post_date = d2.isoformat() if d2 else ""
        head_end = m2.end()
    elif m1:
        date_str = m1.group(1)
        head_end = m1.end()
    else:
        return None

    d = resolve_year(*[int(x) for x in date_str.split("/")], start, end)

    amt_tok = toks[-1]
    balance = None
    if section == "transaction_detail" and len(toks) >= 2:
        prev = toks[-2]
        if re.fullmatch(r"[\s\-]*", text[prev[1]:amt_tok[0]]):
            balance = amt_tok[2]
            amt_tok = prev
    amount = round(apply_sign(amt_tok[2], sign, is_credit), 2)

    desc = text[head_end:amt_tok[0]]
    if tail:
        desc = f"{desc} {tail}"
    if check_no:
        desc = f"Check #{check_no} {desc}".strip()
    desc, inner_post = split_inner_date(desc, start, end)
    if len(desc) > 300:
        desc = desc[:297].rstrip() + "..."
    if inner_post and not post_date:
        post_date = inner_post
    if not desc:
        desc = f"Check #{check_no}" if check_no else "(no description)"

    return new_txn(
        date=d.isoformat() if d else "",
        post_date=post_date,
        description=desc,
        amount=amount,
        balance=balance,
        check_no=check_no,
        category=section or "",
    )


# --------------------------------------------------------------------------
# Whole-statement parse
# --------------------------------------------------------------------------

def parse_chase(path: Path, folder_label: str, lines: list, res: FileResult) -> None:
    res.account_type = detect_account_type(lines)
    start, end, label = find_period(lines)
    res.period = label
    res.account = find_account(lines)
    is_credit = res.account_type == "credit_card"
    table = CREDIT_SECTIONS if is_credit else CHECKING_SECTIONS

    # opening / closing balances for reconciliation
    blob = "\n".join(lines)
    m = re.search(rf"Beginning\s+Balance\s+(?:\d{{1,3}}\s+)?({MONEY})", blob, re.I) or \
        re.search(rf"Previous\s+Balance\s+(?:\d{{1,3}}\s+)?({MONEY})", blob, re.I)
    if m:
        res.opening_balance = to_money(m.group(1))
    m = re.search(rf"Ending\s+Balance\s+(?:\d{{1,3}}\s+)?({MONEY})", blob, re.I) or \
        re.search(rf"New\s+Balance\s+(?:\d{{1,3}}\s+)?({MONEY})", blob, re.I)
    if m:
        res.closing_balance = to_money(m.group(1))
    if is_credit and res.opening_balance is not None:
        # credit card balances are amounts owed; flip so the sign convention
        # (spend negative) reconciles the same way as a deposit account
        res.opening_balance = -res.opening_balance
        if res.closing_balance is not None:
            res.closing_balance = -res.closing_balance

    section, sign = (None, None)
    in_skip = False
    txns: list = []
    rec_lines: list = []
    rec_section = None
    rec_sign = None

    def new_txn(**kw) -> Txn:
        return Txn(
            institution="chase",
            account=res.account,
            account_type=res.account_type,
            statement_period=res.period,
            source_file=path.name,
            source_folder=folder_label,
            **kw,
        )

    def flush():
        """Turn the buffered record lines into a transaction."""
        nonlocal rec_lines, rec_section, rec_sign
        if not rec_lines:
            rec_lines = []
            return
        buf, rec_lines = rec_lines, []
        t = build_txn(buf, rec_section, rec_sign, is_credit, start, end, new_txn)
        if t is not None:
            txns.append(t)

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        # --- hidden Chase section markers, when present -------------------
        mk = re.search(r"\*start\*(.+)", stripped, re.I)
        if mk:
            flush()
            name = mk.group(1)
            if is_skip_section(name):
                section, sign, in_skip = None, None, True
            else:
                hit = match_section(name, table)
                section, sign = hit if hit else (None, None)
                in_skip = hit is None
            continue
        if re.search(r"\*end\*", stripped, re.I):
            flush()
            section, sign, in_skip = None, None, False
            continue

        if not stripped:
            continue

        # --- visible section headings ------------------------------------
        if looks_like_header(stripped) or re.match(
                r"^[A-Z][A-Za-z&' ]{4,60}$", stripped):
            if is_skip_section(stripped):
                flush()
                section, sign, in_skip = None, None, True
                continue
            hit = match_section(stripped, table)
            if hit:
                flush()
                section, sign = hit
                in_skip = False
                continue

        if is_noise(stripped):
            flush()
            continue
        if in_skip or section is None:
            continue

        # --- build records: a new one starts at a date / check number -----
        if starts_record(stripped):
            flush()
            rec_section, rec_sign = section, sign
            rec_lines = [stripped]
            continue

        if rec_lines and len(rec_lines) <= CONTINUATION_MAX:
            rec_lines.append(stripped)
            continue

        flush()

    flush()

    # drop anything that is clearly a balance line rather than a transaction
    txns = [t for t in txns if t.date and
            not re.match(r"^(beginning|ending|previous|new|opening|closing)\s+"
                         r"balance", t.description, re.I)]
    res.txns = txns
    if res.opening_balance is not None and res.closing_balance is not None:
        res.checks.append(BalanceCheck(
            label=res.account or "account",
            expected=round(res.closing_balance - res.opening_balance, 2),
            actual=round(sum(t.amount for t in txns), 2)))
