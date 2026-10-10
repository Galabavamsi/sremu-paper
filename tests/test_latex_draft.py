"""WP12 integration: the draft paper compiles with latexmk (MiKTeX) from a clean copy."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from build_paper import build
from conftest import SREMU_ROOT

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk not installed"),
]


def test_draft_pdf_builds_with_bibliography(paper_copy: Path) -> None:
    rep = build(paper_copy, mode="draft", sremu_root=SREMU_ROOT)
    assert rep.ok, rep.render()
    assert (paper_copy / "main.pdf").stat().st_size > 50_000
    bbl = (paper_copy / "main.bbl").read_text(encoding="utf-8")
    assert len(re.findall(r"\\bibitem\{", bbl)) >= 40
    log = (paper_copy / "main.log").read_text(encoding="latin-1")
    assert "undefined" not in re.sub(r"Font shape `[^']*' undefined", "", log)
    assert not re.search(r"Overfull \\hbox \((\d+\.\d+)pt", log)
