# Bank Statement Parser

Reads bank statement PDFs and writes the transactions out as CSV and a
multi-sheet Excel workbook. Two institutions, auto-detected per file:

- **Chase** — personal/business checking & savings, and credit cards
- **Summit Credit Union** — "Member Statement of Account" (one statement covers
  several share accounts; each is reconciled separately)

Every statement is checked against its own opening/closing balance, so a CSV
that is quietly missing a row shows up as a failed check rather than as a
number you trust by mistake.

## Install

```bash
git clone https://github.com/AweShowSome/statement-parser.git
cd statement-parser
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This puts a `statement-parse` command on your PATH. (`chase-parse` is kept as an
alias for the older name.) Python 3.10 or newer.

## Run

One folder:

```bash
statement-parse -i ~/statements/chase -o ~/statements/output
```

Several folders — each becomes its own CSV and its own sheet in the workbook:

```bash
statement-parse -i ~/statements/chase -i ~/statements/summit -o ~/statements/output
```

Walk subfolders under each input:

```bash
statement-parse -i ~/statements -r -o ~/statements/output
```

If you leave off `-o`, output goes to `./output` in whatever directory you ran from.

## What you get

```
output/
  chase.csv              one CSV per input folder (named after the folder)
  summit.csv
  all_transactions.csv   everything combined, sorted by date
  transactions.xlsx      one sheet per folder + an "All" sheet
  parse_report.txt       per-file row counts and balance checks
```

### Columns

| column | meaning |
|---|---|
| `institution` | `chase` or `summit` |
| `date` | transaction date, `YYYY-MM-DD` |
| `post_date` | posting date, when the statement shows a separate one |
| `description` | merchant / memo text, with wrapped lines rejoined |
| `amount` | **signed**: money out is negative, money in is positive |
| `type` | `debit` or `credit`, derived from the sign |
| `balance` | running balance, when the statement prints one |
| `account` | masked account, e.g. `...1234` |
| `account_type` | `checking`, `savings`, or `credit_card` |
| `check_no` | check number for CHECKS PAID rows |
| `category` | which statement section the row came from |
| `statement_period` | e.g. `2025-01-03 to 2025-02-03` |
| `source_file` | the PDF it came from |
| `source_folder` | the input folder it came from |

### Sign convention

Money leaving you is always negative, whatever the bank or account type:

- **Checking / savings** — withdrawals, checks, and fees negative; deposits positive.
- **Credit card** — purchases, fees, and interest negative; payments and refunds
  positive. (Chase prints purchases as positive charges; the tool flips them so
  a credit card CSV adds up the same way a checking CSV does.)

A statement that prints an explicitly negative amount inside an otherwise
one-directional section — a reversed deposit, a refunded fee — keeps that
printed sign rather than being forced to match the section.

## Reading `parse_report.txt`

This is the part worth reading. For every statement the tool adds up the
transactions it found and compares that to the statement's own beginning/ending
(or previous/new) balance:

```
=== chase ===
  20250124-statements-1234-.pdf:  19 txns | chase checking ...1234 | 2025-01-24 to 2025-02-23 | balanced
  20250224-statements-1234-.pdf:  17 txns | chase checking ...1234 | 2025-02-24 to 2025-03-23 | CHECK: ...1234 off by -62.50
  20250324-statements-1234-.pdf:  33 txns | chase credit_card ...1234 | 2025-03-24 to 2025-04-23 | balanced | 2 discarded
  [SKIP] May 2021 Account Statement.pdf: damaged text layer (characters missing) ...

TOTAL TRANSACTIONS: 4462
SUMMARY: 226 balanced, 0 failed, 0 not verified, 1 refused, 118 non-transaction lines discarded
```

Read the last line first. The five outcomes:

| outcome | what it means |
|---|---|
| `balanced` | the statement's own arithmetic holds — every transaction on it was captured |
| `CHECK: ... off by N` | the rows do not add up to the statement's totals. Something was dropped, duplicated, or signed wrongly. Open the PDF |
| `NOT VERIFIED` | no opening/closing balance was found, so **nothing was checked at all**. This is weaker than `balanced`, not neutral — treat it like `CHECK` |
| `[SKIP]` | the file was refused outright and contributed no rows. The reason is printed |
| `N discarded` | lines that looked like rows but deliberately were not turned into one |

`discarded` is normally boring — it counts Chase's foreign-exchange detail lines
(`06/18 EURO` / `35.00 X 1.174 (EXCHG RATE)`) and Summit status rows such as
`Qualified: Courtesy Overdraft Program - -`, none of which are transactions. It
is reported because the code path that correctly throws those away is the same
one that would throw away a real transaction whose amount failed to parse. If
that number jumps on a statement format you have seen before, look at it.

Summit statements carry several share accounts, so each one is checked and named
separately — a single combined total would let an error in one account be
cancelled out by the other.

### Exit codes

| code | meaning |
|---|---|
| 0 | ran to completion |
| 1 | nothing could be parsed at all, or `--strict` was given and some statement did not verify |
| 2 | bad arguments |

By default a failed balance check is reported but does not change the exit code,
so existing scripts keep working. Pass `--strict` to make it fail:

```bash
statement-parse --strict -i ~/statements/chase -o ~/statements/output
```

`--strict` fails on `CHECK` and on `NOT VERIFIED`. It does **not** fail on
`[SKIP]`, because a refusal is a loud, reported, deliberate outcome — the point
of `--strict` is to catch the quiet failures.

## Options

```
-i, --input FOLDER    folder of statement PDFs; repeat for multiple accounts
-o, --output FOLDER   where results go (default: ./output)
-r, --recursive       also search subfolders
-n, --name NAME       base name for the combined files (default: transactions)
    --no-xlsx         skip the Excel workbook, CSV only
    --password PW     password for locked PDFs
    --x-tolerance N   pdfplumber word-spacing tolerance (default 1.5) — bump to
                      2.5–3 if descriptions come out with words run together
    --strict          exit nonzero if any statement fails or cannot be verified
    --debug           verbose per-file output
```

Developed against a private corpus of 226 real statements (160 Chase, 66 Summit,
spanning 2018–2026 and covering checking, savings and two credit cards) — 4,462
transactions, every statement balanced against its own totals. That corpus is
not part of this repository; the test fixtures are synthetic and generated at
test time.

## Layouts it handles

### Chase

- Checking/savings with the section layout: DEPOSITS AND ADDITIONS, CHECKS PAID,
  ATM & DEBIT CARD WITHDRAWALS, ELECTRONIC WITHDRAWALS, OTHER WITHDRAWALS, FEES
- Checking with the single TRANSACTION DETAIL table (amount + running balance)
- Credit cards with PAYMENTS AND OTHER CREDITS / PURCHASE / FEES CHARGED /
  INTEREST CHARGED sections
- Credit cards with a single ACCOUNT ACTIVITY table, one or two date columns
- Descriptions and amounts that wrap onto following lines
- Statement periods crossing a year boundary (Dec 16 – Jan 15 gets the right years
  on both sides)
- Chase's hidden `*start*`/`*end*` section markers when present, with visible
  section headings as the fallback
- Sub-dollar foreign-currency amounts, which Chase prints without a leading zero
  (`.88`), and the `X 0.00627 (EXCHG RATE)` lines that follow them
- Wire transfers and book transfers whose reference detail wraps over several lines

### Summit Credit Union

- Several share accounts in one statement (PRIMARY SAVINGS, SECONDARY SAVINGS,
  FREE CHECKING), each with its own table, opening/closing balance, and its own
  reconciliation line in the report
- Tables continuing onto later pages under a "Continued from previous page." header
- Blank columns printed as a bare `-` (status rows like "Account Closed" carry no
  amount and are skipped rather than recorded as $0)
- Overdraft periods — see note 4 below

### Four things real statements do that break naive parsers

Worth knowing, because they all fail *silently* — you get a CSV that looks fine
and is quietly missing rows.

1. **The hidden markers collide with real transactions.** Chase stamps each section
   with 1-point invisible `*start*`/`*end*` text, and it often lands on the same
   baseline as a transaction — 240 of the 1,102 marker baselines in the reference
   corpus. Extracted naively you get

   ```
   *end*electro0nic withdraw5al /11 Brokerage Transfer ... 8,000.00
   ```

   and an $8,000 withdrawal disappears. The tool splits each line by font run, so
   the marker and the transaction come apart cleanly. Running the same corpus
   through a plain `extract_text()` breaks 14 statements and loses 14 transactions.

2. **Body text reports a bogus font size.** On these statements the real content is
   drawn at "0.24pt" and the hidden markers at 1pt — so you cannot separate the two
   layers by size, and the obvious "discard anything tiny" rule deletes the
   transactions while keeping the markers, which is exactly backwards. Font
   identity is the reliable cut.

3. **Wrapping goes both directions.** Sometimes the amount is at the end of the
   first line and the detail wraps below it (wires); sometimes the description is on
   the first line and the amount is pushed onto the next (long merchant names).
   Handling only one of those loses the other.

4. **Summit's trailing minus means nothing.** Every numeric column prints with a
   trailing `-` — the column headers literally read `Amount-` and `Balance-`. Read
   as a minus sign it flips the sign of every figure on the statement. But the
   balance genuinely *does* go negative during an overdraft, and the text looks
   identical either way, so the sign has to be reconstructed by running the
   amounts forward from the opening balance (each row says "Deposit" or
   "Withdrawal", which is unambiguous). The tool only trusts that rebuild when
   every rebuilt figure matches the printed one in absolute value.

## Known limits

- **Chase and Summit only.** Another bank's statements will be reported as
  `unrecognized format` rather than parsed badly. See [CONTRIBUTING.md](CONTRIBUTING.md)
  for how to add a layout.
- **Scanned statements won't work.** The tool reads the PDF's text layer. If a file
  reports `no extractable text`, it's an image — run it through OCR first
  (`ocrmypdf in.pdf out.pdf`).
- **Damaged text layers are refused, not guessed.** A few older Summit PDFs have a
  broken font encoding that silently drops characters — "Previous Balance" comes out
  as "PreviousBalnce", and digits fall out of the amounts too. Those are reported as
  `damaged text layer` and skipped, because a plausible-looking wrong number is worse
  than no number.
- **A Summit account already overdrawn when the period opens reports a false
  `CHECK`.** The running balance column has its sign rebuilt, but the balance
  check still uses the opening balance exactly as printed, and Summit prints an
  overdrawn opening with no sign. It fails loudly rather than silently, which is
  the safe direction, but the rows themselves are correct. Pinned by an xfail in
  `tests/test_summit.py`.
- A statement reformatted in a way not listed above may parse partially. The balance
  check in `parse_report.txt` will tell you; send the offending PDF and the layout
  can be added.

## Development

```bash
pip install -e ".[dev]"
pytest          # fixtures are generated into a temp dir, not committed
ruff check .
```

Layout:

```
src/statement_parser/
  models.py      output schema, Txn, and the BalanceCheck that proves a
                 statement's own arithmetic still holds
  extraction.py  PDF -> text rows, plus the money/date/noise primitives
                 both banks share
  chase.py       section state machine, record buffering, build_txn
  summit.py      share-account blocks and the decorative-minus handling
  writers.py     CSV / XLSX / parse_report.txt
  cli.py         argparse and the run loop
```

**No bank statement may ever enter this repository.** `.gitignore` blanket-ignores
`*.pdf`, `*.csv` and `*.xlsx` with no negated exceptions, test fixtures are
generated at test time rather than committed, and CI fails the build if a PDF,
CSV or spreadsheet is found anywhere in git history.

## License

MIT — see [LICENSE](LICENSE).
