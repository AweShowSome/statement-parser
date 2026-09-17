"""Summit Credit Union: several share accounts per statement, and the
decorative trailing minus that is not a minus sign."""

from __future__ import annotations

import pytest

from statement_parser.models import Txn
from statement_parser.summit import (
    summit_account_type,
    summit_fields,
    summit_fix_balances,
    summit_sign,
)

# --------------------------------------------------------------------------
# The decorative trailing minus
# --------------------------------------------------------------------------

@pytest.mark.parametrize("row,desc,amount,balance", [
    # Summit prints "-" after every figure; the column headers read "Amount-".
    ("Deposit Payroll Example Employer 2,000.00- 3,240.50-",
     "Deposit Payroll Example Employer", 2000.00, 3240.50),
    # A bare "-" marks an empty column.
    ("Qualified: Courtesy Overdraft Program - -",
     "Qualified: Courtesy Overdraft Program", None, None),
    ("Withdrawal ATM 20.00- -", "Withdrawal ATM", 20.00, None),
    # Sub-dollar dividend.
    ("Deposit Dividend .12- 5.46-", "Deposit Dividend", 0.12, 5.46),
])
def test_summit_fields(row, desc, amount, balance):
    got_desc, (got_amount, got_balance) = summit_fields(row)
    assert got_desc == desc
    assert got_amount == amount
    assert got_balance == balance


def test_trailing_minus_never_makes_an_amount_negative():
    """Every figure on a Summit statement carries it, including deposits."""
    _, (amount, _) = summit_fields("Deposit Payroll 2,000.00- 3,240.50-")
    assert amount == 2000.00, "the decorative minus was read as a sign"


@pytest.mark.parametrize("desc,want", [
    ("Withdrawal Card Purchase", -1),
    ("Deposit Payroll", +1),
    ("Deposit Dividend", +1),
    ("Something Else Entirely", None),
])
def test_summit_sign(desc, want):
    assert summit_sign(desc) == want


# --------------------------------------------------------------------------
# Rebuilding balance signs
# --------------------------------------------------------------------------

def _block(opening, closing, rows):
    return {"opening": opening, "closing": closing,
            "txns": [Txn(amount=a, balance=b) for a, b in rows]}


def test_overdraft_balance_sign_is_recovered():
    """-40.72 and +40.72 print identically, so the sign has to be rebuilt."""
    b = _block(120.00, 459.28, [(-160.72, 40.72), (500.00, 459.28)])
    summit_fix_balances(b)
    assert [t.balance for t in b["txns"]] == [-40.72, 459.28]


def test_rebuild_handles_an_account_that_starts_overdrawn():
    """The opening balance's own sign is missing from the text too.

    Printed opening 100.00 is really -100.00, so running the amounts forward
    from +100 does not match the printed column and the whole orientation has
    to be flipped. Without the second orientation the printed figures are left
    alone and every balance on the statement is wrong.
    """
    b = _block(100.00, 130.00, [(30.00, 70.00), (200.00, 130.00)])
    summit_fix_balances(b)
    assert [t.balance for t in b["txns"]] == [-70.00, 130.00]


def test_rebuild_is_refused_when_the_figures_disagree():
    """A balance we cannot verify is left as printed, not confidently rewritten."""
    b = _block(120.00, 459.28, [(-160.72, 999.99), (500.00, 459.28)])
    summit_fix_balances(b)
    assert b["txns"][0].balance == 999.99, "an unverifiable rebuild was applied"


def test_rebuild_without_an_opening_balance_does_nothing():
    b = _block(None, 459.28, [(-160.72, 40.72)])
    summit_fix_balances(b)
    assert b["txns"][0].balance == 40.72


def test_overdraft_in_a_real_statement(parsed):
    r = parsed["summit_overdraft_may2025.pdf"]
    assert r.reconciles is True
    balances = [t.balance for t in r.txns]
    assert balances == [-40.72, 459.28]
    assert r.txns[0].amount == -160.72
    assert r.txns[1].amount == 500.00


# --------------------------------------------------------------------------
# Several share accounts in one statement
# --------------------------------------------------------------------------

def test_each_share_account_is_reconciled_separately(parsed):
    """One combined total would let an error in one account hide in the other."""
    r = parsed["summit_multi_apr2025.pdf"]
    assert len(r.checks) == 2, "expected one balance check per share account"
    assert all(c.ok for c in r.checks), r.failures()
    labels = " ".join(c.label for c in r.checks)
    assert "0000" in labels and "0040" in labels


def test_rows_are_attributed_to_the_right_account(parsed):
    r = parsed["summit_multi_apr2025.pdf"]
    savings = [t for t in r.txns if t.account == "...0000"]
    checking = [t for t in r.txns if t.account == "...0040"]
    assert len(savings) == 1 and savings[0].amount == 0.12
    assert len(checking) == 4
    assert savings[0].account_type == "savings"
    assert all(t.account_type == "checking" for t in checking)


def test_continued_from_previous_page_does_not_start_a_new_account(parsed):
    """The header reappears without a Previous Balance; it is the same block."""
    r = parsed["summit_multi_apr2025.pdf"]
    assert len(r.checks) == 2, "the continuation header opened a third block"
    checking = [t for t in r.txns if t.account == "...0040"]
    # Two rows before the page break, two after.
    assert [t.date for t in checking] == [
        "2025-04-02", "2025-04-05", "2025-04-12", "2025-04-20"]


def test_status_rows_are_discarded_not_parsed(parsed):
    """'Qualified: Courtesy Overdraft Program - -' carries no amount."""
    r = parsed["summit_multi_apr2025.pdf"]
    assert r.discarded == 1
    assert not any("Courtesy Overdraft" in t.description for t in r.txns)


def test_sub_dollar_dividend_survives(parsed):
    r = parsed["summit_multi_apr2025.pdf"]
    assert any(t.amount == 0.12 for t in r.txns)


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN LIMITATION: summit_fix_balances() can recover the sign of the running "
    "balance column, but BalanceCheck still uses the opening balance exactly as "
    "printed, and Summit prints an overdrawn opening with no sign. An account "
    "that is already overdrawn on day one therefore reports a false CHECK "
    "failure. It fails loudly rather than silently, which is the safe direction, "
    "but it is still wrong. Remove this xfail when the check derives its opening "
    "from the verified rebuild."))
def test_account_that_starts_overdrawn_reconciles(parsed):
    r = parsed["summit_negative_open_jun2025.pdf"]
    assert r.reconciles is True, r.failures()


@pytest.mark.parametrize("name,want", [
    ("FREE CHECKING", "checking"),
    ("PRIMARY SAVINGS", "savings"),
    ("MONEY MARKET", "savings"),
    ("VISA PLATINUM", "credit_card"),
    ("SOMETHING ELSE", "other"),
])
def test_account_type_from_share_name(name, want):
    assert summit_account_type(name) == want
