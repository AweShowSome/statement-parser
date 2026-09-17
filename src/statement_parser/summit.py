"""Summit Credit Union "Member Statement of Account".

One Summit statement covers several share accounts at once -- a savings, a
checking, sometimes a third -- each with its own header, its own transaction
table and its own New Balance. Each is reconciled separately, because a single
whole-file total would hide an error in one account being cancelled by the
other.

The recurring trap in this layout is the trailing minus. Summit prints a
decorative '-' after every number, including in the column headers, which
literally read "Amount-" and "Balance-". It is not a minus sign. But balances
really do go negative during an overdraft, and they print identically. See
summit_fix_balances().
"""

from __future__ import annotations

import re
from pathlib import Path

from .extraction import clean_desc, is_noise, parse_slash, resolve_year, to_money
from .models import BalanceCheck, FileResult, Txn

RE_SUMMIT_PERIOD = re.compile(
    r"Statement\s+For\s+(\d{1,2}/\d{1,2}/\d{2,4})\s*[-–]\s*"
    r"(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
RE_SUMMIT_ACCT = re.compile(r"^([A-Z][A-Z0-9 &'/]{3,40}?)\s+ID\s+(\d{3,5})\b")
RE_SUMMIT_TABLE = re.compile(r"^Date\s+Transaction\s+Description", re.I)
RE_SUMMIT_PREV = re.compile(r"Previous\s+Balance\s+\$?([\d,]*\.\d{2})-?", re.I)
RE_SUMMIT_NEW = re.compile(r"New\s+Balance\s+\$?([\d,]*\.\d{2})-?", re.I)
RE_SUMMIT_ROW = re.compile(r"^(\d{1,2}/\d{1,2})\s+(.+)$")
RE_SUMMIT_STOP = re.compile(
    r"^(Your\s+Account\s+Balances|Account\s+Totals|Fees?\s+Paid|"
    r"Description\s+Current|Total\s+Dividends|Insured\s+by\s+NCUA|"
    r"Page\s+\d+\s+of\s+\d+|Annual\s+Percentage|Total\s+(Deposits|Withdrawals)|"
    r"CHECKING\s+ACCOUNT\s+RECONCILEMENT|IN\s+CASE\s+OF\s+ERRORS|"
    r"Overdraft|Member\s+Statement)", re.I)


def summit_fields(rest: str, maxn: int = 2) -> tuple:
    """Pop up to `maxn` trailing numeric columns off a Summit row.

    Returns (description, [amount, balance]). A bare '-' marks an empty column
    and becomes None; the decorative trailing '-' on a real figure is stripped
    without changing its sign.
    """
    fields: list = []
    while len(fields) < maxn:
        m = re.search(r"(?:(?<=\s)|^)-\s*$", rest)
        if m:
            rest = rest[:m.start()].rstrip()
            fields.insert(0, None)
            continue
        m = re.search(r"(?:(?<=\s)|^)\$?([\d,]*\.\d{2})-?\s*$", rest)
        if m:
            fields.insert(0, to_money(m.group(1)))
            rest = rest[:m.start()].rstrip()
            continue
        break
    while len(fields) < maxn:
        fields.append(None)
    return rest.strip(), fields


def summit_sign(desc: str) -> int | None:
    """Summit names the direction in the description, which is unambiguous."""
    d = desc.strip().lower()
    if d.startswith("withdrawal"):
        return -1
    if d.startswith("deposit"):
        return +1
    if "dividend" in d or "interest paid" in d:
        return +1
    return None


def summit_account_type(name: str) -> str:
    n = name.upper()
    if "CHECKING" in n:
        return "checking"
    if "SAVINGS" in n or "MONEY MARKET" in n:
        return "savings"
    if "VISA" in n or "CREDIT" in n:
        return "credit_card"
    return "other"


def summit_fix_balances(block: dict) -> None:
    """Restore the sign of the running balance column.

    Summit's trailing '-' is decorative, so an overdrawn balance of -40.72 and a
    healthy 959.28 both print as "40.72-" / "959.28-" -- the text carries no
    sign at all. The amounts are unambiguous (every row says Deposit or
    Withdrawal), so the balance is rebuilt by running the amounts forward from
    the opening balance. The rebuild is only trusted if every rebuilt figure
    matches the printed one in absolute value; otherwise the printed column is
    left untouched, on the principle that a balance we cannot verify is better
    left as printed than confidently rewritten wrong.
    """
    opening = block.get("opening")
    txns = block.get("txns") or []
    if opening is None or not txns:
        return

    for orientation in (1, -1):
        run = opening * orientation
        rebuilt, ok = [], True
        for t in txns:
            run = round(run + t.amount, 2)
            rebuilt.append(run)
            if t.balance is not None and abs(abs(run) - abs(t.balance)) > 0.01:
                ok = False
                break
        if not ok:
            continue
        closing = block.get("closing")
        if closing is not None and abs(abs(run) - abs(closing)) > 0.01:
            continue
        # Lengths always match here: `ok` can only be True if the loop above ran
        # to completion, appending one rebuilt figure per transaction.
        for t, value in zip(txns, rebuilt, strict=True):
            t.balance = value
        return


def parse_summit(path: Path, folder_label: str, lines: list, res: FileResult) -> None:
    """Parse a Summit Credit Union 'Member Statement of Account'.

    Each share account gets its own header (`FREE CHECKING ID 0040 Previous
    Balance $1,240.50-`), its own transaction table, and its own New Balance.
    A header without a Previous Balance means "Continued from previous page."
    and re-opens the existing block rather than starting a new one.
    """
    blob = "\n".join(lines[:400])
    start = end = None
    m = RE_SUMMIT_PERIOD.search(blob)
    if m:
        start, end = parse_slash(m.group(1)), parse_slash(m.group(2))
        if start and end:
            res.period = f"{start.isoformat()} to {end.isoformat()}"

    blocks: list = []
    cur: dict | None = None
    in_table = False
    pending: Txn | None = None
    cont_used = 0
    discarded = 0

    def flush():
        nonlocal pending, cont_used
        if pending is not None and cur is not None:
            pending.description = clean_desc(pending.description)
            cur["txns"].append(pending)
        pending, cont_used = None, 0

    for raw in lines:
        s = raw.strip()
        if not s:
            continue

        ma = RE_SUMMIT_ACCT.match(s)
        if ma:
            flush()
            name, acct_id = ma.group(1).strip(), ma.group(2)
            mp = RE_SUMMIT_PREV.search(s)
            if mp:
                cur = {"name": name, "id": acct_id,
                       "opening": to_money(mp.group(1)), "closing": None,
                       "running": to_money(mp.group(1)), "txns": []}
                blocks.append(cur)
            else:
                # "... Continued from previous page." -- same account, not a new block
                cur = next((b for b in blocks if b["id"] == acct_id), cur)
            in_table = False
            continue

        if cur is not None and not in_table:
            mn = RE_SUMMIT_NEW.search(s)
            if mn:
                cur["closing"] = to_money(mn.group(1))

        if RE_SUMMIT_TABLE.match(s):
            flush()
            in_table = True
            continue

        if RE_SUMMIT_STOP.match(s):
            flush()
            in_table = False
            continue

        if not in_table or cur is None:
            continue

        mr = RE_SUMMIT_ROW.match(s)
        if mr:
            flush()
            desc, fields = summit_fields(mr.group(2))
            amount, balance = fields[0], fields[1]
            if amount is None:
                # A status row, e.g. "Qualified: Courtesy Overdraft Program - -".
                # Counted so that a real row losing its amount is visible.
                discarded += 1
                continue

            sign = summit_sign(desc)
            if sign is None and balance is not None and cur["running"] is not None:
                sign = 1 if balance > cur["running"] else -1
            amount = abs(amount) * (sign if sign else -1)
            if balance is not None:
                cur["running"] = balance

            d = resolve_year(*[int(x) for x in mr.group(1).split("/")], start, end)
            first = desc.split()[0].lower() if desc.split() else ""
            pending = Txn(
                institution="summit",
                date=d.isoformat() if d else "",
                description=desc,
                amount=round(amount, 2),
                balance=balance,
                account="..." + cur["id"],
                account_type=summit_account_type(cur["name"]),
                category=first if first in ("deposit", "withdrawal") else "other",
                statement_period=res.period,
                source_file=path.name,
                source_folder=folder_label,
            )
            cont_used = 0
            continue

        # wrapped description line
        if pending is not None and cont_used < 2 and not is_noise(s):
            pending.description += " " + s
            cont_used += 1
            continue
        flush()

    flush()

    if not blocks:
        # Some older Summit PDFs carry a damaged text layer: glyphs are missing,
        # so "Previous Balance" extracts as "PreviousBalnce" and -- far worse --
        # digits drop out of the amounts. Refuse rather than emit wrong numbers.
        res.error = ("damaged text layer (characters missing) -- amounts would be "
                     "wrong; re-download this statement or enter it by hand")
        return

    res.account_type = summit_account_type(blocks[0]["name"])
    checking = [b for b in blocks if summit_account_type(b["name"]) == "checking"]
    if checking:
        res.account_type = "checking"
    res.account = ", ".join("..." + b["id"] for b in blocks) or ""
    res.discarded = discarded
    for b in blocks:
        summit_fix_balances(b)
        res.txns.extend(b["txns"])
        if b["opening"] is not None and b["closing"] is not None:
            res.checks.append(BalanceCheck(
                label=f'{b["name"]} ...{b["id"]}',
                expected=round(b["closing"] - b["opening"], 2),
                actual=round(sum(t.amount for t in b["txns"]), 2)))
    res.txns.sort(key=lambda t: (t.date, t.account))
