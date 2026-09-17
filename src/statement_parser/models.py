"""Output schema and the per-statement result objects.

The one idea worth knowing here is `BalanceCheck`: every statement states its
own opening and closing balance, so the transactions we parsed out of it must
sum to the difference. That is the project's correctness signal — not "did it
crash", but "does the statement's own arithmetic still hold".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

COLUMNS = [
    "institution",     # chase | summit
    "date",            # transaction date (YYYY-MM-DD)
    "post_date",       # posting date when the statement shows one
    "description",     # merchant / memo text
    "amount",          # signed: spend negative, money in positive
    "type",            # debit | credit
    "balance",         # running balance, when the statement prints one
    "account",         # masked account number, e.g. ...4321
    "account_type",    # checking | savings | credit_card
    "check_no",        # check number for CHECKS PAID rows
    "category",        # statement section, e.g. purchases, electronic_withdrawals
    "statement_period",
    "source_file",
    "source_folder",
]


@dataclass
class Txn:
    institution: str = ""
    date: str = ""
    post_date: str = ""
    description: str = ""
    amount: float = 0.0
    type: str = ""
    balance: float | None = None
    account: str = ""
    account_type: str = ""
    check_no: str = ""
    category: str = ""
    statement_period: str = ""
    source_file: str = ""
    source_folder: str = ""

    def row(self) -> dict:
        d = asdict(self)
        d["type"] = "credit" if self.amount > 0 else "debit"
        return {k: d.get(k) for k in COLUMNS}


@dataclass
class BalanceCheck:
    """One statement's own arithmetic, used to prove nothing was dropped."""

    label: str
    expected: float      # closing - opening, per the statement
    actual: float        # sum of the transactions we parsed

    @property
    def ok(self) -> bool:
        return abs(self.expected - self.actual) < 0.01

    @property
    def drift(self) -> float:
        return round(self.actual - self.expected, 2)


@dataclass
class FileResult:
    path: Path
    institution: str = ""
    account_type: str = "unknown"
    account: str = ""
    period: str = ""
    txns: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    opening_balance: float | None = None
    closing_balance: float | None = None
    error: str = ""
    # Records the parser looked at and deliberately did not turn into a
    # transaction (foreign-exchange detail lines, Summit status rows). Counted
    # rather than silently dropped, so a future layout change shows up as this
    # number rising instead of as rows quietly going missing.
    discarded: int = 0

    @property
    def reconciles(self) -> bool | None:
        """True / False, or None when the statement stated no totals to check."""
        if not self.checks:
            return None
        return all(c.ok for c in self.checks)

    @property
    def verified(self) -> bool:
        """Did this file actually prove itself?

        `reconciles is None` means the opening/closing balances were never
        found, so nothing was verified at all. That is a weaker position than
        a clean check, and --strict treats it as a failure.
        """
        return self.reconciles is True

    def failures(self) -> str:
        return "; ".join(f"{c.label} off by {c.drift:+.2f}"
                         for c in self.checks if not c.ok)
