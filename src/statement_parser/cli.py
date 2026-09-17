"""Command line entry point.

    statement-parse -i ~/statements/chase -i ~/statements/summit -o ~/out

Each -i folder becomes its own CSV plus a sheet in the workbook; everything is
also merged, date-sorted, into all_<name>.csv.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import traceback
from pathlib import Path

from . import __version__, parse_statement
from .models import FileResult
from .writers import write_csv, write_report, write_xlsx

EPILOG = """
Amount sign convention
----------------------
Money LEAVING you is negative; money COMING IN is positive.
  checking:    withdrawals/checks/fees  -> negative, deposits -> positive
  credit card: purchases/fees/interest  -> negative, payments/credits -> positive

Exit codes
----------
  0  ran to completion
  1  nothing could be parsed at all, or --strict and some file did not verify
  2  bad arguments
"""


def collect_pdfs(folder: Path, recursive: bool) -> list:
    if not folder.exists():
        return []
    it = folder.rglob("*") if recursive else folder.glob("*")
    return sorted(
        p for p in it
        if p.is_file() and p.suffix.lower() == ".pdf" and not p.name.startswith(".")
    )


def label_for(folder: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_. -]", "_", folder.resolve().name) or "statements"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="statement-parse",
        description="Extract transactions from Chase and Summit Credit Union "
                    "statement PDFs into CSV/XLSX.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG)
    ap.add_argument("-i", "--input", action="append", required=True, metavar="FOLDER",
                    help="folder of statement PDFs; repeat for multiple accounts")
    ap.add_argument("-o", "--output", default=None, metavar="FOLDER",
                    help="where to write results (default: ./output)")
    ap.add_argument("-r", "--recursive", action="store_true",
                    help="also search subfolders of each input folder")
    ap.add_argument("-n", "--name", default="transactions",
                    help="base name for the combined files (default: transactions)")
    ap.add_argument("--no-xlsx", action="store_true", help="skip the Excel workbook")
    ap.add_argument("--password", default=None, help="password for locked PDFs")
    ap.add_argument("--x-tolerance", type=float, default=1.5,
                    help="pdfplumber word spacing tolerance (default 1.5)")
    ap.add_argument("--strict", action="store_true",
                    help="exit nonzero if any statement fails its balance check "
                         "or states no totals to check against")
    ap.add_argument("--debug", action="store_true", help="verbose per-file output")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    in_folders = [Path(os.path.expanduser(p)).resolve() for p in args.input]
    missing = [p for p in in_folders if not p.is_dir()]
    if missing:
        for p in missing:
            print(f"error: not a folder: {p}", file=sys.stderr)
        return 2

    out_dir = Path(os.path.expanduser(args.output)).resolve() if args.output \
        else Path.cwd() / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    groups: dict = {}
    results: list = []
    labels_used: dict = {}

    for folder in in_folders:
        label = label_for(folder)
        if label in labels_used:
            labels_used[label] += 1
            label = f"{label}_{labels_used[label]}"
        else:
            labels_used[label] = 1

        pdfs = collect_pdfs(folder, args.recursive)
        if not pdfs:
            print(f"warning: no PDFs found in {folder}", file=sys.stderr)
        res_list = []
        for pdf in pdfs:
            try:
                res = parse_statement(pdf, label, args.password,
                                      args.x_tolerance, args.debug)
            except Exception as exc:  # noqa: BLE001
                res = FileResult(path=pdf, error=f"parser crashed: {exc}")
                if args.debug:
                    traceback.print_exc()
            res_list.append(res)
            print(f"  {pdf.name}: {len(res.txns)} transactions"
                  + (f"  [{res.error}]" if res.error else ""))
        results.append((label, res_list))

        rows = [t.row() for r in res_list for t in r.txns]
        rows.sort(key=lambda r: (r["date"] or "", r["source_file"]))
        groups[label] = rows

        if rows:
            write_csv(rows, out_dir / f"{label}.csv")

    all_rows = [r for rows in groups.values() for r in rows]
    all_rows.sort(key=lambda r: (r["date"] or "", r["source_folder"], r["source_file"]))

    if not all_rows:
        print("\nNo transactions parsed. Run with --debug, and send one sample "
              "statement so the layout can be added.", file=sys.stderr)
        write_report(results, out_dir / "parse_report.txt")
        return 1

    write_csv(all_rows, out_dir / f"all_{args.name}.csv")

    xlsx_ok = False
    if not args.no_xlsx:
        xlsx_ok = write_xlsx(groups, out_dir / f"{args.name}.xlsx")
        if not xlsx_ok:
            print("note: openpyxl not installed — skipped the .xlsx workbook",
                  file=sys.stderr)

    report = write_report(results, out_dir / "parse_report.txt")
    print("\n" + report)
    print(f"\nWrote {len(all_rows)} transactions to {out_dir}")
    for label in groups:
        if groups[label]:
            print(f"  {label}.csv  ({len(groups[label])} rows)")
    print(f"  all_{args.name}.csv")
    if xlsx_ok:
        print(f"  {args.name}.xlsx")
    print("  parse_report.txt")

    unverified = [r for _, rl in results for r in rl if not r.error and not r.verified]
    if unverified:
        print(f"\n{len(unverified)} statement(s) did not verify against their own "
              f"balance totals — see parse_report.txt", file=sys.stderr)
        if args.strict:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
