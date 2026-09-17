"""PDF -> text rows, and the money/date primitives.

The marker-collision tests here pin the single subtlest behaviour in the
project. Read pdf_lines()'s docstring before changing them.
"""

from __future__ import annotations

import datetime as dt

import pdfplumber
import pytest

from statement_parser.extraction import (
    is_noise,
    money_tokens,
    parse_slash,
    pdf_lines,
    resolve_year,
    to_money,
)

# --------------------------------------------------------------------------
# Hidden section markers welded onto a real transaction
# --------------------------------------------------------------------------

def _naive_lines(path):
    out = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            out.extend((page.extract_text(x_tolerance=1.5, y_tolerance=3) or "")
                       .split("\n"))
    return out


def test_fixture_really_contains_a_welded_marker(samples):
    """The fixture is only worth anything if the collision is actually there.

    If this fails, the fixture stopped reproducing the bug and the test below
    it is passing for the wrong reason.
    """
    welded = [ln for ln in _naive_lines(samples["credit_foreign_jun2025.pdf"])
              if "*end*" in ln.lower() and "MOUNTAIN" in ln]
    assert welded, ("the sample no longer puts a marker on the same baseline as "
                    "a transaction -- check MARKER_WELD_DY in make_samples.py")


def test_marker_is_split_off_its_transaction(samples):
    lines = pdf_lines(samples["credit_foreign_jun2025.pdf"], None, 1.5)
    txn = [ln for ln in lines if "MOUNTAIN OUTFITTERS" in ln]
    assert len(txn) == 1
    assert "*end*" not in txn[0].lower(), "marker text leaked into a transaction"
    assert txn[0].strip().endswith("212.55"), "the amount must survive the split"
    assert any(ln.strip().lower() == "*end*purchase" for ln in lines), \
        "the marker itself must still be emitted, or sections never close"


def test_transaction_sharing_a_line_with_end_marker_stays_in_its_section(parsed):
    """Ordering within a baseline is start-marker, content, end-marker.

    A row welded onto `*end*` must still be counted inside the section it
    belongs to, otherwise it loses its category and its sign.
    """
    r = parsed["credit_foreign_jun2025.pdf"]
    row = next(t for t in r.txns if "MOUNTAIN OUTFITTERS" in t.description)
    assert row.category == "purchases"
    assert row.amount == -212.55


def test_split_is_what_makes_the_statement_balance(samples, monkeypatch):
    """Without the font-run split the welded row is lost and the file breaks."""
    import statement_parser

    monkeypatch.setattr(statement_parser, "pdf_lines",
                        lambda p, pw, x: _naive_lines(p))
    r = statement_parser.parse_statement(
        samples["credit_foreign_jun2025.pdf"], "t", None, 1.5)
    assert r.reconciles is False
    assert len(r.txns) == 3


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ("1,234.56", 1234.56),
    ("-$45.23", -45.23),
    ("(45.23)", -45.23),
    ("45.23-", -45.23),        # Summit's decorative minus, read as a value
    ("$0.00", 0.0),
    (".88", 0.88),             # sub-dollar, no leading zero
    ("-.06", -0.06),
    ("not money", None),
])
def test_to_money(raw, want):
    assert to_money(raw) == want


def test_sub_dollar_amount_is_found_in_a_line():
    """A money regex requiring a leading digit misses these entirely."""
    toks = money_tokens("06/21 KIOSK NEWSSTAND FAIRVIEW PT .88")
    assert toks and toks[-1][2] == 0.88


def test_foreign_exchange_detail_lines_are_discarded_and_counted(parsed):
    """Chase prints the conversion under the purchase:

        06/18 EURO
              35.00 X 1.174285714 (EXCHG RATE)

    Those two lines are not a transaction. Discarding them is correct, but the
    count has to move, because the same path would swallow a real row whose
    amount failed to match.
    """
    r = parsed["credit_foreign_jun2025.pdf"]
    assert r.discarded == 2, "the FX stub lines were not counted as discards"
    assert not any("EURO" in t.description for t in r.txns)
    assert not any("EXCHG" in t.description.upper() for t in r.txns)


def test_sub_dollar_amount_survives_into_the_output(parsed):
    r = parsed["credit_foreign_jun2025.pdf"]
    amounts = sorted(t.amount for t in r.txns)
    assert -0.88 in amounts, "the .88 purchase was dropped"
    assert -0.06 in amounts


def test_detached_minus_binds_to_the_following_amount():
    """Chase lays the sign down as its own word, well left of the digits."""
    toks = money_tokens("01/19 Some Payment - 1,150.00")
    assert toks[-1][2] == -1150.00


def test_a_hyphen_inside_text_is_not_a_minus_sign():
    toks = money_tokens("12/14 MUSIC SERVICE 877-555-0166 NY 16.99")
    assert toks[-1][2] == 16.99


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def test_parse_slash_two_digit_year():
    assert parse_slash("01/15/25") == dt.date(2025, 1, 15)


def test_parse_slash_rejects_impossible_dates():
    assert parse_slash("13/45/25") is None


def test_year_rollover_across_a_december_to_january_period():
    """The statement prints MM/DD only; December belongs to the earlier year."""
    start, end = dt.date(2024, 12, 16), dt.date(2025, 1, 15)
    assert resolve_year(12, 28, start, end) == dt.date(2024, 12, 28)
    assert resolve_year(1, 4, start, end) == dt.date(2025, 1, 4)


def test_year_rollover_in_a_real_statement(parsed):
    r = parsed["credit_sections_dec2024.pdf"]
    by_desc = {t.description: t.date for t in r.txns}
    dec = next(v for k, v in by_desc.items() if "WHOLESALE CLUB" in k)
    jan = next(v for k, v in by_desc.items() if "MUSIC SERVICE" in k)
    assert dec.startswith("2024-12"), f"December row got {dec}"
    assert jan.startswith("2025-01"), f"January row got {jan}"


def test_a_date_from_neither_boundary_year_still_resolves():
    """A December charge posting on a January statement belongs to last year.

    Neither the period start nor the period end falls in 2024 here, so the
    candidate years have to reach beyond the two boundary years.
    """
    start, end = dt.date(2025, 1, 3), dt.date(2025, 2, 3)
    assert resolve_year(12, 28, start, end) == dt.date(2024, 12, 28)


def test_leap_day_in_a_non_leap_year_is_skipped_not_crashed():
    d = resolve_year(2, 29, dt.date(2023, 2, 1), dt.date(2023, 2, 28))
    assert d is None or d.month == 2


def test_resolve_year_without_a_period_gives_up():
    assert resolve_year(1, 5, None, None) is None


# --------------------------------------------------------------------------
# Noise
# --------------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "Page 1 of 4",
    "Total Deposits and Additions $6,500.00",
    "JPMorgan Chase Bank, N.A.",
    "Beginning Balance 5,482.19",
    "",
])
def test_boilerplate_is_noise(line):
    assert is_noise(line)


@pytest.mark.parametrize("line", [
    "01/06 Remote Online Deposit 1 2,500.00",
    "12/17 WHOLESALE CLUB #1075 SPRINGFIELD IL 312.44",
    "04/02 Deposit Payroll Example Employer 2,000.00- 3,240.50-",
])
def test_transactions_are_not_noise(line):
    assert not is_noise(line)
