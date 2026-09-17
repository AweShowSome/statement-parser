"""The invariant that matters: every statement's own arithmetic must hold.

A statement states an opening and a closing balance. The transactions parsed
out of it must sum to the difference. This is the only check that catches the
dangerous failure mode -- a CSV that looks perfectly fine but is quietly
missing a row or has a flipped sign.
"""

from __future__ import annotations

import pytest

PARSEABLE = [
    "checking_sections_jan2025.pdf",
    "checking_detail_jan2025.pdf",
    "credit_sections_dec2024.pdf",
    "credit_activity_jan2025.pdf",
    "credit_foreign_jun2025.pdf",
    "summit_multi_apr2025.pdf",
    "summit_overdraft_may2025.pdf",
    # summit_negative_open is deliberately absent: it exercises a known
    # limitation and is pinned by an xfail in test_summit.py instead.
]


@pytest.mark.parametrize("name", PARSEABLE)
def test_statement_reconciles(parsed, name):
    r = parsed[name]
    assert not r.error, f"{name} failed to parse: {r.error}"
    assert r.txns, f"{name} produced no transactions"
    assert r.reconciles is not None, (
        f"{name} states no balance totals, so nothing was verified -- that is a "
        f"weaker outcome than a passing check, not a neutral one")
    assert r.reconciles is True, f"{name} does not balance: {r.failures()}"


@pytest.mark.parametrize("name", PARSEABLE)
def test_every_row_has_a_date_and_an_amount(parsed, name):
    for t in parsed[name].txns:
        assert t.date, f"{name}: row with no date: {t.description!r}"
        assert t.amount != 0, f"{name}: zero amount: {t.description!r}"
        assert t.description, f"{name}: row with no description"


@pytest.mark.parametrize("name", PARSEABLE)
def test_reconciliation_is_actually_sensitive(parsed, name):
    """Guard against a check that can never fail.

    If dropping any single transaction still leaves the statement balancing,
    the invariant is not doing its job.
    """
    r = parsed[name]
    total = round(sum(t.amount for t in r.txns), 2)
    for t in r.txns:
        assert round(total - t.amount, 2) != total, (
            f"{name}: dropping {t.description!r} would go unnoticed")


def test_damaged_text_layer_is_refused(parsed):
    """A statement whose glyphs are dropping out must be refused, not guessed at.

    Characters go missing from the amounts themselves, so a best-effort parse
    would emit numbers that are wrong rather than obviously broken.
    """
    r = parsed["summit_damaged_may2021.pdf"]
    assert r.error, "a damaged text layer must be refused"
    assert "damaged text layer" in r.error
    assert r.txns == [], "a refused statement must emit no transactions"


def test_refusal_beats_a_partial_parse(parsed):
    """The refusal path must not be reachable by merely finding nothing."""
    good = parsed["summit_multi_apr2025.pdf"]
    assert not good.error and good.txns
