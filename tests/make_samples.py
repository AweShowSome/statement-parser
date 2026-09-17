#!/usr/bin/env python3
"""Generate synthetic statement PDFs that mimic real layouts, for testing.

Everything here is invented: merchants, towns, people, account and card numbers
are all fictional placeholders. Card numbers use the 4111-11.. test prefix and
phone numbers use the reserved 555-01xx range, so nothing in this file can
collide with a real account or a real person. Nothing derived from a real
statement may be pasted in here, not even while debugging.

These PDFs are build output. They are generated into a temp directory by the
`samples` fixture in conftest.py and are never committed -- which is what lets
.gitignore blanket-ignore *.pdf with no exceptions, so there is no path by
which a real statement could be added to the repository.

What each fixture deliberately exercises, so it survives editing:

  checking_sections   the multi-section checking layout, a description that
                      wraps with its amount pushed onto a later line, a CHECKS
                      PAID table, and a skipped DAILY ENDING BALANCE block
  checking_detail     the single TRANSACTION DETAIL table (amount + running
                      balance), Chase's detached minus sign ("- 421.88"), and a
                      description wrapping *before* its amount
  credit_sections     credit-card section layout, an amount pushed onto the
                      line below a long merchant string, and a Dec->Jan
                      statement period that forces the year-rollover logic
  credit_activity     a single ACCOUNT ACTIVITY table with two date columns
  credit_foreign      a sub-dollar amount printed with no leading zero (".88"),
                      the foreign-exchange detail lines that must be discarded
                      rather than parsed, and -- the important one -- a real
                      transaction sharing a baseline with an `*end*` marker
  checking_lookalike  a wrapped description line that satisfies every test
                      for a skipped section heading ("Overdraft Protection
                      Transfer"), which must not be allowed to close a section
  summit_multi        two share accounts in one statement, a "Continued from
                      previous page." header, a blank "- -" status row, and a
                      sub-dollar dividend
  summit_overdraft    a balance that goes genuinely negative, which prints
                      identically to a positive one because Summit's trailing
                      minus is decorative
  summit_negative_open  an account already overdrawn when the period opens, so
                      even the opening balance's sign is missing from the text
  summit_damaged      a damaged text layer, which must be refused outright

Each fixture's transactions must sum to its own opening -> closing balance
delta; that is what the parser's reconciliation check asserts.

Chase draws its hidden section markers in Times-Roman at 1pt while body text is
a bogus 0.24pt, and often puts a marker on the same baseline as a real row. A
line prefixed with "~" below is drawn as such a marker *without advancing the
cursor*, so the following line lands on the same baseline -- reproducing the
collision that pdf_lines() exists to survive.
"""

from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

MARKER_FONT = ("Times-Roman", 1.0)
BODY_FONT = ("Helvetica", 8.5)

# A glyph's `top` in pdfplumber is measured from the top of its ascender, so
# text of two different sizes drawn on the same baseline reports two different
# tops -- 1pt marker text and 8.5pt body text come out ~6pt apart, which is
# more than LINE_TOL, and they would land on separate rows. Lifting the marker
# by this much makes the two tops coincide, which is what a real Chase
# statement looks like and what pdf_lines() has to cope with.
MARKER_WELD_DY = 6.0

CHECKING = """
CHASE
JPMorgan Chase Bank, N.A.
P O Box 182051
Columbus, OH 43218 - 2051

January 03, 2025 through February 03, 2025
Account Number: 000000987654321

CUSTOMER SERVICE INFORMATION
Web site: Chase.com
Service Center: 1-800-555-0110
Deaf and Hard of Hearing: 1-800-555-0111
Para Espanol: 1-888-555-0112

CHECKING SUMMARY
Chase Total Checking
INSTANCES AMOUNT
Beginning Balance 5,482.19
Deposits and Additions 3 6,500.00
ATM & Debit Card Withdrawals 4 -412.87
Electronic Withdrawals 3 -2,940.00
Checks Paid 2 -1,325.00
Fees 1 -12.00
Ending Balance 13 7,292.32

*start*deposits and additions
DEPOSITS AND ADDITIONS
DATE DESCRIPTION AMOUNT
01/06 Remote Online Deposit 1 2,500.00
01/17 Orig CO Name:Example Rlty Orig ID:9876543210 Desc Date:250117 CO Entry
Descr:Rent Sec:CCD Trace#:021000029876543 Eed:250117 Ind ID:UNIT204
3,000.00
01/31 Deposit 1,000.00
Total Deposits and Additions $6,500.00
*end*deposits and additions

*start*checks paid section
CHECKS PAID
CHECK NO. DESCRIPTION DATE PAID AMOUNT
1041 ^ 01/09 825.00
1042 ^ 01/23 500.00
Total Checks Paid $1,325.00
*end*checks paid section

*start*atm debit withdrawal
ATM & DEBIT CARD WITHDRAWALS
DATE DESCRIPTION AMOUNT
01/08 Card Purchase 01/06 Hardware Depot #4821 Springfield IL Card 1111 187.43
01/13 Card Purchase With Pin 01/13 Grocery Mart 6112 Fairview OH Card
1111
96.22
01/21 Card Purchase 01/19 Paint Supply Co 703121 Riverton NY Card 1111
104.10
01/28 ATM Withdrawal 01/28 100 Main St Fairview OH Card 1111 25.12
Total ATM & Debit Card Withdrawals $412.87
*end*atm debit withdrawal

*start*electronic withdrawal
ELECTRONIC WITHDRAWALS
DATE DESCRIPTION AMOUNT
01/10 City Utility Bill Pymt PPD ID: 3390000420 340.00
01/15 Chase Credit Crd Autopay PPD ID: 4760039224 1,600.00
01/27 State Dept Rev Tax Pymt Web ID: 1396006247 1,000.00
Total Electronic Withdrawals $2,940.00
*end*electronic withdrawal

*start*fees section
FEES
DATE DESCRIPTION AMOUNT
01/31 Monthly Service Fee 12.00
Total Fees $12.00
*end*fees section

*start*daily ending balance
DAILY ENDING BALANCE
DATE AMOUNT DATE AMOUNT
01/06 7,982.19 01/21 8,229.44
01/08 7,794.76 01/23 7,729.44
*end*daily ending balance

Page 1 of 4
"""

CHECKING_DETAIL = """
CHASE
January 03, 2025 through February 03, 2025
Account Number: 000000123456789

Chase Business Complete Checking
CHECKING SUMMARY
Beginning Balance 12,004.55
Ending Balance 11,116.42

*start*transaction detail
TRANSACTION DETAIL
DATE DESCRIPTION AMOUNT BALANCE
01/03 Beginning Balance 12,004.55
01/07 Card Purchase 01/05 Builders Mart Springfield IL Card 8890 - 421.88 11,582.67
01/12 Remote Online Deposit 1 2,200.00 13,782.67
01/19 Orig CO Name:Example Prop Orig ID:5551212000 Desc Date:250119
Sec:CCD Trace#:0210000255512120
- 1,150.00 12,632.67
01/24 Zelle Payment To Sample Payee 19827364512 - 400.00 12,232.67
01/30 Wire Transfer Fee - 35.00 12,197.67
02/03 Payment To Chase Card Ending IN 1111 - 1,081.25 11,116.42
02/03 Ending Balance 11,116.42
*end*transaction detail

Page 2 of 3
"""

CREDIT = """
CHASE
ACCOUNT SUMMARY
Chase Rewards Card
Account Number: 4111 11XX XXXX 1111

Opening/Closing Date 12/16/24 - 01/15/25
Previous Balance $1,081.25
Payment, Credits -$1,115.42
Purchases +$1,897.09
Cash Advances $0.00
Balance Transfers $0.00
Fees Charged $0.00
Interest Charged $0.00
New Balance $1,862.92
Minimum Payment Due $40.00
Payment Due Date 02/11/25
Credit Limit $18,000

*start*payments and other credits
PAYMENTS AND OTHER CREDITS
Date of
Transaction Merchant Name or Transaction Description $ Amount
12/28 AUTOMATIC PAYMENT - THANK YOU -1,081.25
01/04 ONLINE RETAILER*RT4Y82 shop.example WA -34.17
Total payments and other credits -$1,115.42
*end*payments and other credits

*start*purchase
PURCHASE
Date of
Transaction Merchant Name or Transaction Description $ Amount
12/17 WHOLESALE CLUB #1075 SPRINGFIELD IL 312.44
12/19 SQ *CORNER STUDIO Fairview OH 85.00
12/22 CONTINENTAL AIR 0162483920164 800-555-0142 TX
548.60
12/29 FUEL STOP 57444429108 FAIRVIEW OH 61.02
01/03 ONLINE RETAILER US*Z84KL2 shop.example WA 129.87
01/09 THE HARDWARE DEPOT #4821 SPRINGFIELD IL 743.17
01/14 MUSIC SERVICE 877-555-0166 NY 16.99
Total purchases $1,897.09
*end*purchase

*start*fees charged
FEES CHARGED
Date of
Transaction Merchant Name or Transaction Description $ Amount
Total fees charged $0.00
*end*fees charged

2025 Totals Year-to-Date
Total fees charged in 2025 $0.00
Total interest charged in 2025 $0.00

Page 1 of 2
"""

CREDIT_2DATE = """
CHASE
Chase Travel Card
Account Number: XXXX XXXX XXXX 9021
Opening/Closing Date 01/16/25 - 02/15/25
Previous Balance $0.00
New Balance $612.40
Minimum Payment Due $25.00
Payment Due Date 03/12/25

*start*account activity
ACCOUNT ACTIVITY
Trans Post
Date Date Description Amount
01/18 01/19 COASTAL AIR 0062319845213 LAKESIDE TX 388.40
01/22 01/23 TST* CORNER CAFE FAIRVIEW OH 74.55
02/01 02/02 RIDESHARE TRIP help.example CA 24.45
02/09 02/10 APP STORE BILL 866-555-0133 CA 125.00
*end*account activity

Page 1 of 2
"""

# Sub-dollar amounts, foreign-exchange detail lines that must NOT become rows,
# and a real transaction welded onto an *end* marker's baseline.
#   purchases: 41.10 + 0.88 + 0.06 + 212.55 = 254.59
CREDIT_FOREIGN = """
CHASE
Chase Travel Card
Account Number: XXXX XXXX XXXX 4444
Opening/Closing Date 06/16/25 - 07/15/25
Previous Balance $0.00
New Balance $254.59
Minimum Payment Due $25.00
Payment Due Date 08/12/25

*start*purchase
PURCHASE
Date of
Transaction Merchant Name or Transaction Description $ Amount
06/18 HARBOUR BOOKS LAKESIDE PT 41.10
06/18 EURO
35.00 X 1.174285714 (EXCHG RATE)
06/21 KIOSK NEWSSTAND FAIRVIEW PT .88
06/21 EURO
0.75 X 1.173333333 (EXCHG RATE)
06/24 TRANSIT FARE ADJUSTMENT LAKESIDE PT .06
~*end*purchase
07/02 MOUNTAIN OUTFITTERS RIVERTON NY 212.55
"""

SUMMIT_MULTI = """
Summit Credit Union
Member Statement of Account
1234 Example Parkway, Fairview OH 43000
Statement For 04/01/2025 - 04/30/2025

Member Name: A Sample Member
Insured by NCUA

PRIMARY SAVINGS ID 0025 Previous Balance $5.34-
Date Transaction Description Amount- Balance-
04/28 Deposit Dividend .12- 5.46-
Your Account Balances as of 04/30
New Balance $5.46-

FREE CHECKING ID 0071 Previous Balance $1,240.50-
Date Transaction Description Amount- Balance-
04/02 Deposit Payroll Example Employer 2,000.00- 3,240.50-
04/05 Withdrawal Card Purchase Grocery Mart 6112 145.20- 3,095.30-
04/10 Qualified: Courtesy Overdraft Program - -
Page 1 of 2

FREE CHECKING ID 0071 Continued from previous page.
Date Transaction Description Amount- Balance-
04/12 Withdrawal ACH City Utility Bill Pymt 249.04- 2,846.26-
04/20 Withdrawal Transfer To Share 0025 2,000.00- 846.26-
Your Account Balances as of 04/30
New Balance $846.26-

Page 2 of 2
"""

# 120.00 - 160.72 = -40.72 overdrawn, printed as "40.72-" exactly like a
# positive figure; then +500.00 brings it back to 459.28.
SUMMIT_OVERDRAFT = """
Summit Credit Union
Member Statement of Account
Statement For 05/01/2025 - 05/31/2025
Insured by NCUA

FREE CHECKING ID 0071 Previous Balance $120.00-
Date Transaction Description Amount- Balance-
05/03 Withdrawal Card Purchase Hardware Depot 160.72- 40.72-
05/09 Deposit Payroll Example Employer 500.00- 459.28-
Your Account Balances as of 05/31
New Balance $459.28-

Page 1 of 1
"""

# Glyphs dropped by a broken font encoding: "Previous Balance" comes through as
# "PreviousBalnce" and digits fall out of the amounts. Must be refused.
SUMMIT_DAMAGED = """
Summit Credit Union
Member Statement of Account
Statement For 05/01/2021 - 05/31/2021

FREE CHECKNG D 0071 PreviousBalnce $12.0-
Dte Trnsction Descrition Amunt- Blnce-
05/03 Withdrwl Crd Purchse 16.2- 4.2-
New Blnce $45.28-
"""

# A wrapped description line that reads exactly like a section heading Chase
# wants skipped. "Overdraft Protection Transfer" is title case, contains no
# digits, and contains a SKIP_SECTIONS term -- so it satisfies every test the
# heading branch applies. Treated as a heading it closes the section, discards
# the record being built (its amount is still on the next line) and suppresses
# the row after it too: two transactions lost, no error.
#   electronic withdrawals: 250.00 + 130.00 = 380.00
#   5,000.00 - 380.00 = 4,620.00
CHECKING_HEADING_LOOKALIKE = """
CHASE
February 03, 2025 through March 03, 2025
Account Number: 000000555000111

CHECKING SUMMARY
Chase Total Checking
Beginning Balance 5,000.00
Electronic Withdrawals 2 -380.00
Ending Balance 4,620.00

*start*electronic withdrawal
ELECTRONIC WITHDRAWALS
DATE DESCRIPTION AMOUNT
02/22 Online Transfer To Sav 1111 Transaction
Overdraft Protection Transfer
250.00
02/27 City Utility Bill Pymt PPD ID: 3390000420 130.00
Total Electronic Withdrawals $380.00
*end*electronic withdrawal

Page 1 of 2
"""

# An account that is ALREADY overdrawn when the period opens: the true opening
# is -40.72 but Summit prints it as "40.72-" like any other figure, so nothing
# in the text says it is negative. Pinned by an xfail test in test_summit.py.
SUMMIT_NEGATIVE_OPEN = """
Summit Credit Union
Member Statement of Account
Statement For 06/01/2025 - 06/30/2025
Insured by NCUA

FREE CHECKING ID 0071 Previous Balance $40.72-
Date Transaction Description Amount- Balance-
06/09 Deposit Payroll Example Employer 500.00- 459.28-
Your Account Balances as of 06/30
New Balance $459.28-

Page 1 of 1
"""

SAMPLES = {
    "checking_sections_jan2025.pdf": CHECKING,
    "checking_detail_jan2025.pdf": CHECKING_DETAIL,
    "credit_sections_dec2024.pdf": CREDIT,
    "credit_activity_jan2025.pdf": CREDIT_2DATE,
    "credit_foreign_jun2025.pdf": CREDIT_FOREIGN,
    "checking_lookalike_feb2025.pdf": CHECKING_HEADING_LOOKALIKE,
    "summit_multi_apr2025.pdf": SUMMIT_MULTI,
    "summit_overdraft_may2025.pdf": SUMMIT_OVERDRAFT,
    "summit_negative_open_jun2025.pdf": SUMMIT_NEGATIVE_OPEN,
    "summit_damaged_may2021.pdf": SUMMIT_DAMAGED,
}

RE_MARKER_LINE = re.compile(r"\*(start|end)\*", re.I)


def make(path: Path, body: str) -> Path:
    """Render one fixture.

    Chase's hidden markers are drawn in their real font (Times-Roman 1pt) so
    that pdf_lines()'s font-run split is genuinely under test. A "~" prefix
    draws the marker without advancing the cursor, welding the next line onto
    the same baseline.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    _, height = letter
    y = height - 50
    c.setFont(*BODY_FONT)
    for line in body.strip("\n").split("\n"):
        if y < 45:
            c.showPage()
            c.setFont(*BODY_FONT)
            y = height - 50

        weld = line.startswith("~")
        if weld:
            line = line[1:]

        if RE_MARKER_LINE.search(line):
            # Markers sit slightly left of the body text so the two runs do not
            # interleave, exactly as Chase lays them out.
            c.setFont(*MARKER_FONT)
            c.drawString(28, y + (MARKER_WELD_DY if weld else 0), line)
            c.setFont(*BODY_FONT)
            if not weld:
                y -= 11
            continue

        c.drawString(40, y, line)
        y -= 11
    c.save()
    return path


def build_all(outdir: Path) -> dict:
    """Render every fixture into `outdir`; returns {name: Path}."""
    return {name: make(Path(outdir) / name, body) for name, body in SAMPLES.items()}


if __name__ == "__main__":
    out = Path(__file__).parent / "samples"
    build_all(out)
    print(f"wrote {len(SAMPLES)} samples to {out}")
