"""End-to-end: the CLI contract, the output schema, and --strict."""

from __future__ import annotations

import csv
import shutil

import pytest

from statement_parser.cli import main
from statement_parser.models import COLUMNS


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """Run the CLI once over a folder of good fixtures; return the output dir."""
    def _run(sample_paths, names, extra_args=(), out_name="out"):
        base = tmp_path_factory.mktemp(out_name)
        indir, outdir = base / "statements", base / "output"
        indir.mkdir()
        for n in names:
            shutil.copy(sample_paths[n], indir / n)
        code = main(["-i", str(indir), "-o", str(outdir), *extra_args])
        return code, outdir
    return _run


GOOD = ["checking_sections_jan2025.pdf", "credit_sections_dec2024.pdf",
        "summit_multi_apr2025.pdf"]


def test_end_to_end_writes_every_expected_file(samples, run):
    code, out = run(samples, GOOD)
    assert code == 0
    assert (out / "statements.csv").exists()
    assert (out / "all_transactions.csv").exists()
    assert (out / "transactions.xlsx").exists()
    assert (out / "parse_report.txt").exists()


def test_csv_column_schema_is_exactly_the_documented_one(samples, run):
    _, out = run(samples, GOOD)
    with (out / "all_transactions.csv").open() as fh:
        assert next(csv.reader(fh)) == COLUMNS


def test_csv_amounts_are_two_decimal_places(samples, run):
    _, out = run(samples, GOOD)
    with (out / "all_transactions.csv").open() as fh:
        for row in csv.DictReader(fh):
            assert row["amount"] and float(row["amount"])
            assert row["amount"].split(".")[-1].__len__() == 2


def test_combined_csv_is_date_sorted(samples, run):
    _, out = run(samples, GOOD)
    with (out / "all_transactions.csv").open() as fh:
        dates = [r["date"] for r in csv.DictReader(fh)]
    assert dates == sorted(dates)


def test_report_records_the_balance_result(samples, run):
    _, out = run(samples, GOOD)
    report = (out / "parse_report.txt").read_text()
    assert report.count("balanced") >= len(GOOD)
    assert "SUMMARY:" in report
    assert "0 failed" in report


def test_refused_file_is_reported_and_does_not_abort_the_run(samples, run):
    """One unreadable statement must not cost you the other 200."""
    code, out = run(samples, GOOD + ["summit_damaged_may2021.pdf"], out_name="mixed")
    assert code == 0
    report = (out / "parse_report.txt").read_text()
    assert "[SKIP]" in report and "damaged text layer" in report
    assert "1 refused" in report
    # the good statements still came through
    with (out / "all_transactions.csv").open() as fh:
        assert len(list(csv.DictReader(fh))) > 20


def test_strict_passes_when_everything_verifies(samples, run):
    code, _ = run(samples, GOOD, extra_args=["--strict"], out_name="strict_ok")
    assert code == 0


def test_strict_fails_when_a_statement_cannot_be_verified(samples, tmp_path,
                                                          monkeypatch):
    """A statement that states no totals is unverified, which --strict rejects.

    This is the failure mode that used to be invisible: the file parses, the
    report says something bland, and the exit code stays 0.
    """
    import statement_parser.cli as cli

    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    shutil.copy(samples["checking_sections_jan2025.pdf"], indir / "s.pdf")

    real = cli.parse_statement

    def no_totals(*a, **kw):
        r = real(*a, **kw)
        r.checks = []           # as if the balance regexes had missed
        return r

    monkeypatch.setattr(cli, "parse_statement", no_totals)
    assert main(["-i", str(indir), "-o", str(outdir)]) == 0
    assert main(["-i", str(indir), "-o", str(outdir), "--strict"]) == 1


def test_report_marks_unverified_clearly(samples, tmp_path, monkeypatch):
    import statement_parser.cli as cli

    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    shutil.copy(samples["checking_sections_jan2025.pdf"], indir / "s.pdf")
    real = cli.parse_statement

    def no_totals(*a, **kw):
        r = real(*a, **kw)
        r.checks = []
        return r

    monkeypatch.setattr(cli, "parse_statement", no_totals)
    main(["-i", str(indir), "-o", str(outdir)])
    assert "NOT VERIFIED" in (outdir / "parse_report.txt").read_text()


# --------------------------------------------------------------------------
# Backwards compatibility -- these flags must keep working
# --------------------------------------------------------------------------

def test_no_xlsx_skips_the_workbook(samples, run):
    _, out = run(samples, GOOD, extra_args=["--no-xlsx"], out_name="noxlsx")
    assert not (out / "transactions.xlsx").exists()
    assert (out / "all_transactions.csv").exists()


def test_custom_base_name(samples, run):
    _, out = run(samples, GOOD, extra_args=["-n", "ledger"], out_name="named")
    assert (out / "all_ledger.csv").exists()
    assert (out / "ledger.xlsx").exists()


def test_recursive_finds_statements_in_subfolders(samples, tmp_path):
    indir, outdir = tmp_path / "in", tmp_path / "out"
    (indir / "2025").mkdir(parents=True)
    shutil.copy(samples["checking_sections_jan2025.pdf"], indir / "2025" / "s.pdf")
    assert main(["-i", str(indir), "-o", str(outdir)]) == 1      # no PDFs at top
    assert main(["-i", str(indir), "-o", str(outdir), "-r"]) == 0


def test_every_documented_flag_is_accepted(samples, tmp_path):
    indir, outdir = tmp_path / "in", tmp_path / "out"
    indir.mkdir()
    shutil.copy(samples["checking_sections_jan2025.pdf"], indir / "s.pdf")
    code = main(["-i", str(indir), "-o", str(outdir), "-r", "-n", "t",
                 "--no-xlsx", "--x-tolerance", "1.5", "--debug"])
    assert code == 0


def test_missing_input_folder_is_an_argument_error(tmp_path):
    assert main(["-i", str(tmp_path / "nope"), "-o", str(tmp_path / "o")]) == 2


def test_multiple_folders_get_their_own_sheets(samples, tmp_path):
    outdir = tmp_path / "out"
    a, b = tmp_path / "chase", tmp_path / "summit"
    a.mkdir()
    b.mkdir()
    shutil.copy(samples["checking_sections_jan2025.pdf"], a / "s.pdf")
    shutil.copy(samples["summit_multi_apr2025.pdf"], b / "s.pdf")
    assert main(["-i", str(a), "-i", str(b), "-o", str(outdir)]) == 0
    assert (outdir / "chase.csv").exists()
    assert (outdir / "summit.csv").exists()
    with (outdir / "chase.csv").open() as fh:
        assert {r["source_folder"] for r in csv.DictReader(fh)} == {"chase"}
