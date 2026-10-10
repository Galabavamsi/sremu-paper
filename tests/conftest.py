"""Shared fixtures for the paper-tool tests (WP12)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

PAPER_ROOT = Path(__file__).resolve().parents[1]
TOOLS = PAPER_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

SREMU_ROOT = (PAPER_ROOT.parent / "sremu").resolve()

_IGNORE = shutil.ignore_patterns(
    "*.aux", "*.log", "*.pdf", "*.bbl", "*.blg", "*.fls", "*.fdb_latexmk", "*.out",
    "*.synctex.gz", "__pycache__", ".pytest_cache", ".ruff_cache", "tests", "buildmode.tex",
)


@pytest.fixture
def paper_copy(tmp_path: Path) -> Path:
    """An isolated copy of the paper sources that a test may modify."""
    dst = tmp_path / "paper"
    shutil.copytree(PAPER_ROOT, dst, ignore=_IGNORE)
    return dst


def append(path: Path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def junit_xml(cases: dict[str, str], props: dict[str, str] | None = None) -> str:
    """Minimal pytest-style junit: {nodeid-ish name: passed|failed|skipped}.

    ``props`` become testsuite-level properties, as written by pytest's
    ``record_testsuite_property`` (e.g. sremu_commit / sremu_dirty)."""
    rows = []
    for name, status in cases.items():
        inner = {"passed": "", "failed": "<failure message='x'/>",
                 "skipped": "<skipped message='x'/>"}[status]
        rows.append(f'<testcase classname="tests.t" name="{name}">{inner}</testcase>')
    head = ""
    if props:
        head = "<properties>" + "".join(
            f'<property name="{k}" value="{v}"/>' for k, v in props.items()) + "</properties>"
    return ("<?xml version='1.0'?><testsuites><testsuite name='pytest'>"
            + head + "".join(rows) + "</testsuite></testsuites>")


def pinned(commit: str) -> dict[str, str]:
    """Provenance properties of a junit produced at `commit` on a clean tree."""
    return {"sremu_commit": commit, "sremu_dirty": "false"}
