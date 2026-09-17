"""Shared fixtures.

Sample PDFs are generated into a temp directory once per test session rather
than committed. Two reasons: they are build output, and keeping them out of the
tree means .gitignore can blanket-ignore *.pdf with no negated exceptions --
so there is no mechanism by which a real bank statement could be committed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from make_samples import build_all  # noqa: E402


@pytest.fixture(scope="session")
def samples(tmp_path_factory) -> dict:
    """{filename: Path} for every generated fixture."""
    return build_all(tmp_path_factory.mktemp("samples"))


@pytest.fixture(scope="session")
def parsed(samples) -> dict:
    """{filename: FileResult} -- every fixture parsed exactly once."""
    from statement_parser import parse_statement
    return {name: parse_statement(p, "fixtures", None, 1.5)
            for name, p in samples.items()}
