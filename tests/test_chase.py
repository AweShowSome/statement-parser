"""Chase: line wrapping, section signs, and the section state machine."""

from __future__ import annotations

import pytest

from statement_parser.chase import (
    CHECKING_SECTIONS,
    CREDIT_SECTIONS,
    apply_sign,
    is_skip_section,
    match_section,
)

# --------------------------------------------------------------------------
# Line wrapping -- it goes in both directions and they need opposite handling
# --------------------------------------------------------------------------

def test_wrap_with_amount_on_the_first_line(parsed):
    """Wires: amount ends line 1, the reference detail wraps below it."""
    r = parsed["checking_detail_jan2025.pdf"]
    row = next(t for t in r.txns if "Example Prop" in t.description)
    assert row.amount == -1150.00
    assert "Trace#" in row.description, "the wrapped detail line was dropped"


def test_wrap_with_amount_pushed_onto_the_next_line(parsed):
    """Long merchant names: description fills line 1, amount lands on line 2."""
    r = parsed["credit_sections_dec2024.pdf"]
    row = next(t for t in r.txns if "CONTINENTAL AIR" in t.description)
    assert row.amount == -548.60


def test_wrap_across_three_lines(parsed):
    r = parsed["checking_sections_jan2025.pdf"]
    row = next(t for t in r.txns if "Grocery Mart" in t.description)
    assert row.amount == -96.22


def test_detached_minus_in_a_transaction_detail_table(parsed):
    r = parsed["checking_detail_jan2025.pdf"]
    row = next(t for t in r.txns if "Builders Mart" in t.description)
    assert row.amount == -421.88
    assert row.balance == 11582.67, "the running balance column was mis-read"


# --------------------------------------------------------------------------
# Sign convention: money out is negative, everywhere
# --------------------------------------------------------------------------

def test_checking_sections_signs(parsed):
    r = parsed["checking_sections_jan2025.pdf"]
    by_cat = {}
    for t in r.txns:
        by_cat.setdefault(t.category, []).append(t.amount)
    assert all(a > 0 for a in by_cat["deposits"])
    for cat in ("checks_paid", "card_withdrawals", "electronic_withdrawals", "fees"):
        assert all(a < 0 for a in by_cat[cat]), f"{cat} should be negative"


def test_credit_card_purchases_are_negative_and_payments_positive(parsed):
    """Chase prints card purchases as positive charges; spending is money out."""
    r = parsed["credit_sections_dec2024.pdf"]
    purchases = [t.amount for t in r.txns if t.category == "purchases"]
    payments = [t.amount for t in r.txns if t.category == "payments_credits"]
    assert purchases and all(a < 0 for a in purchases)
    assert payments and all(a > 0 for a in payments)


def test_row_type_follows_the_amount(parsed):
    for t in parsed["credit_sections_dec2024.pdf"].txns:
        assert t.row()["type"] == ("credit" if t.amount > 0 else "debit")


@pytest.mark.parametrize("amount,sign,is_credit,want", [
    (100.0, -1, False, -100.0),    # withdrawal, printed unsigned
    (100.0, +1, False, 100.0),     # deposit, printed unsigned
    (-500.0, +1, False, -500.0),   # reversed deposit keeps its printed minus
    (-100.0, -1, False, 100.0),    # reversed withdrawal: money comes back
    (312.44, None, True, -312.44),  # credit-card purchase
    (-1081.25, None, True, 1081.25),  # credit-card payment
])
def test_apply_sign(amount, sign, is_credit, want):
    assert apply_sign(amount, sign, is_credit) == want


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------

def test_skipped_sections_contribute_nothing(parsed):
    """DAILY ENDING BALANCE is a table of dates and amounts that are not rows."""
    r = parsed["checking_sections_jan2025.pdf"]
    assert not any(t.amount in (7982.19, 7794.76, 8229.44, 7729.44)
                   for t in r.txns), "a daily-balance row leaked in as a transaction"
    assert len(r.txns) == 13


def test_checks_paid_keeps_the_check_number(parsed):
    r = parsed["checking_sections_jan2025.pdf"]
    checks = [t for t in r.txns if t.category == "checks_paid"]
    assert {t.check_no for t in checks} == {"1041", "1042"}
    assert all(t.amount < 0 for t in checks)


def test_two_date_columns_become_date_and_post_date(parsed):
    r = parsed["credit_activity_jan2025.pdf"]
    row = next(t for t in r.txns if "COASTAL AIR" in t.description)
    assert row.date == "2025-01-18"
    assert row.post_date == "2025-01-19"


def test_inner_card_purchase_date_is_lifted_out(parsed):
    r = parsed["checking_sections_jan2025.pdf"]
    row = next(t for t in r.txns if "Hardware Depot" in t.description)
    assert row.date == "2025-01-08"
    assert row.post_date == "2025-01-06"
    assert "01/06" not in row.description


@pytest.mark.parametrize("heading,table,want", [
    ("DEPOSITS AND ADDITIONS", CHECKING_SECTIONS, "deposits"),
    ("ELECTRONIC WITHDRAWALS", CHECKING_SECTIONS, "electronic_withdrawals"),
    ("atm debit withdrawal", CHECKING_SECTIONS, "card_withdrawals"),
    ("checks paid section", CHECKING_SECTIONS, "checks_paid"),
    ("PURCHASE", CREDIT_SECTIONS, "purchases"),
    ("payments and other credits", CREDIT_SECTIONS, "payments_credits"),
])
def test_match_section(heading, table, want):
    hit = match_section(heading, table)
    assert hit is not None and hit[0] == want


@pytest.mark.parametrize("short", ["fee", "dep", "int", "purch"])
def test_short_words_cannot_open_a_section(short):
    assert match_section(short, CHECKING_SECTIONS) is None
    assert match_section(short, CREDIT_SECTIONS) is None


def test_length_guard_applies_to_the_forward_prefix_direction_too():
    """`A or (B and L)` is not `(A or B) and L`.

    `and` binds tighter than `or`, so the length guard used to protect only the
    reverse-prefix branch and a forward-prefix match was accepted at any length.
    The real section tables barely expose it -- only "fees" is shorter than the
    guard -- so this tests the documented contract directly with a short key,
    which is the property that has to hold as sections get added.
    """
    table = {"ab": ("x", 1)}
    assert match_section("ab", table) == ("x", 1)        # exact still wins
    assert match_section("abcde", table) is None         # 5 chars: too short
    assert match_section("abcdefgh", table) == ("x", 1)  # 8 chars: distinctive


@pytest.mark.parametrize("heading", [
    "CHECKING SUMMARY", "DAILY ENDING BALANCE", "Rewards Summary",
    "Interest Charge Calculation",
])
def test_summary_sections_are_skipped(heading):
    assert is_skip_section(heading)


def test_real_sections_are_not_skipped():
    for heading in ("DEPOSITS AND ADDITIONS", "ELECTRONIC WITHDRAWALS", "PURCHASE"):
        assert not is_skip_section(heading)
