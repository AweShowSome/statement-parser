# Contributing

The obvious extension point is adding a bank. This note is mostly about that.

## The rule that matters

**No bank statement may ever enter this repository.** Not as an example, not as
a test fixture, not temporarily.

`.gitignore` blanket-ignores `*.pdf`, `*.csv` and `*.xlsx` with **no negated
exceptions**, test fixtures are generated at test time rather than committed,
and CI fails the build if such a file is found anywhere in git history. Please
do not add an exception to make a fixture committable — generating it is what
keeps the blanket rule safe.

The same applies to statement *content*. While debugging against real
statements it is very easy to paste a real merchant, amount or account number
into a docstring or a test case. Everything in `tests/make_samples.py` is
invented: fictional merchants and towns, `4111-11..` test card numbers, and
`555-01xx` phone numbers, which are reserved and cannot reach a real person.
Keep it that way, and scrub anything real before you commit.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```

## Adding a bank

A bank is one module. `summit.py` is the better of the two to copy: it is
self-contained, and it deals with a layout whose quirks are documented.

**1. Detection.** Add a branch to `detect_institution()` in `__init__.py`,
matching on something that appears near the top of every statement from that
bank, and dispatch to your parser in `parse_statement()`.

**2. The parser.** Write `parse_<bank>(path, folder_label, lines, res)`. It
receives the statement as a list of text rows and fills in `res`:

| field | what to put in it |
|---|---|
| `res.txns` | the transactions, as `Txn` objects |
| `res.checks` | one `BalanceCheck` per account on the statement |
| `res.period`, `res.account`, `res.account_type` | statement metadata |
| `res.discarded` | count of lines you deliberately did not turn into a row |
| `res.error` | set this and return if the file cannot be parsed safely |

**3. The balance check is not optional.** This is the whole design. Every
statement states an opening and a closing balance; the rows you parse must sum
to the difference:

```python
res.checks.append(BalanceCheck(
    label=account_name,
    expected=round(closing - opening, 2),
    actual=round(sum(t.amount for t in txns), 2)))
```

If the statement covers several accounts, append one check per account. A single
combined total lets an error in one account be cancelled out by another.

If you cannot find the balances, **leave `res.checks` empty** rather than
inventing a check that always passes. An empty list reports as `NOT VERIFIED`,
which is honest, and `--strict` treats it as a failure.

**4. Signs.** Money leaving the account is negative, always, including on credit
cards where the statement prints purchases as positive charges. Nothing
downstream re-examines this, so getting it wrong produces a plausible CSV that
is wrong — the worst outcome this project has.

**5. Refuse rather than guess.** If the text layer is damaged, or the layout is
close to but not quite something you understand, set `res.error` and return. A
statement that is skipped is visible in the report. A statement parsed into
confident wrong numbers is not.

## Adding fixtures

Add the statement body to `SAMPLES` in `tests/make_samples.py` and describe, in
the module docstring, what it deliberately exercises — that docstring is what
stops a later refactor from quietly deleting the interesting case.

Every fixture must reconcile. Add it to `PARSEABLE` in
`tests/test_reconciliation.py` and the invariant is asserted automatically.

If you are reproducing a layout quirk, make sure the fixture actually reproduces
it. `test_fixture_really_contains_a_welded_marker` exists because a fixture that
has stopped exercising its quirk still passes every other test — it just stops
meaning anything.

## Changing the parser

Tests passing is not sufficient evidence. The synthetic fixtures cover a
fraction of what real statements do.

- Run the tool over a real corpus before and after, and diff the output CSVs
  byte-for-byte. Any difference is a regression until you can explain it as an
  intentional fix.
- Write the output somewhere outside the repository.
- If you change the balance-check or reporting logic, say in the commit message
  what the corpus diff was. Several commits in this repository's history record
  "byte-identical", which is what makes them reviewable.

A quick way to check a test is worth having: break the line it covers and
confirm the test fails. Several tests here were written, passed immediately, and
turned out to be asserting nothing until the fixture was fixed.

## Style

- Ruff, configured in `pyproject.toml`. `ruff check .` must be clean.
- Comments should say *why*, especially in the parsing code, where almost every
  odd-looking branch exists because a real statement did something absurd.
  Name the absurdity.
- Conventional commits (`fix:`, `feat:`, `refactor:`, `test:`, `docs:`, `chore:`).
- No new runtime dependencies without a good reason. The tool is deliberately
  just pdfplumber and openpyxl.
