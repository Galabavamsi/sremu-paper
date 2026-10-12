"""WP12: check_arxiv.py -- abstract budget, forbidden markers, file names, image size."""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

import pytest
from check_arxiv import abstract_source, check_arxiv, raster_size, tex_to_plain
from conftest import PAPER_ROOT, append
from paper_common import ABSTRACT_MAX_CHARS, NumberMacro, load_numbers_spec

pytestmark = pytest.mark.unit


def _mac(name: str, kind: str, max_chars: int, value: str | None = None) -> NumberMacro:
    return NumberMacro(name, kind, "d", "study", "X", None, value, max_chars)


def _png(path: Path, w: int, h: int) -> None:
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + chunk
                     + struct.pack(">I", zlib.crc32(chunk)))


def test_real_paper_passes_pre_build() -> None:
    rep = check_arxiv(PAPER_ROOT)
    assert rep.ok, rep.render()


def test_abstract_within_limits_with_budgets() -> None:
    spec = {m.macro: m for m in load_numbers_spec(PAPER_ROOT / "numbers.yaml")}
    plain = tex_to_plain(abstract_source(PAPER_ROOT), spec)
    assert len(plain) <= ABSTRACT_MAX_CHARS
    assert len(plain.split()) <= 200
    assert "\\" not in plain
    assert "{" not in plain


def test_placeholder_budget_is_counted() -> None:
    spec = {"txtA": _mac("txtA", "text", 84), "numB": _mac("numB", "number", 5),
            "numC": _mac("numC", "number", 5, value="\\ensuremath{10^{-3}}")}
    plain = tex_to_plain(r"x \txtA{} y \numB~dB below \numC.", spec)
    # 84-character text budget = 14 six-character words; numbers count as one word.
    assert plain == "x " + ("##### " * 15)[:84].strip() + " y ##### dB below 10^-3."
    assert len(plain.split()) == 1 + 14 + 1 + 1 + 3


def test_abstract_over_budget_fails(paper_copy: Path) -> None:
    # Blow up the budget of a text macro that the abstract prints (the first \txt... macro of
    # the abstract; since must-fix 2 it prints no result phrase, only the platform name).
    found = re.search(r"\\(txt[A-Za-z]+)", abstract_source(paper_copy))
    assert found, "the abstract prints no text macro; pick another budget to blow up"
    ny = paper_copy / "numbers.yaml"
    text = ny.read_text(encoding="utf-8")
    new, n = re.subn(rf"(- macro: {found.group(1)}\b.*?max_chars: )\d+", r"\g<1>900", text,
                     count=1, flags=re.S)
    assert n == 1
    ny.write_text(new, encoding="utf-8")
    errs = check_arxiv(paper_copy).errors
    assert any("characters" in e for e in errs)
    assert any("words" in e for e in errs)


@pytest.mark.parametrize(("snippet", "needle"), [
    ("\nWritten on \\today.\n", "\\today"),
    ("\n% TODO fix this\n", "'TODO' marker"),
    ("\nFIXME later\n", "'FIXME' marker"),
    ("\n\\usepackage{lineno}\n", "line numbering"),
])
def test_forbidden_markers_fail(paper_copy: Path, snippet: str, needle: str) -> None:
    append(paper_copy / "sections" / "09_future_work.tex", snippet)
    assert any(needle in e for e in check_arxiv(paper_copy).errors)


def test_unsafe_filename_fails(paper_copy: Path) -> None:
    out = paper_copy / "figures" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "bad name(1).pdf").write_bytes(b"%PDF-1.4\n")
    assert any("unsafe file name" in e for e in check_arxiv(paper_copy).errors)


def test_raster_pixel_limit(paper_copy: Path) -> None:
    out = paper_copy / "figures" / "out"
    out.mkdir(parents=True, exist_ok=True)
    _png(out / "ok.png", 5000, 6000)  # 30.0 MP
    assert check_arxiv(paper_copy).ok
    _png(out / "big.png", 7000, 6000)  # 42.0 MP > 34 MP
    errs = check_arxiv(paper_copy).errors
    assert any("big.png: 7000x6000 exceeds 34 MP" in e for e in errs)
    assert not any("ok.png" in e for e in errs)


def test_raster_size_reads_png_and_jpeg(tmp_path: Path) -> None:
    _png(tmp_path / "a.png", 123, 45)
    assert raster_size(tmp_path / "a.png") == (123, 45)
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, 480, 640, 1) + b"\x01\x11\x00"
    (tmp_path / "b.jpg").write_bytes(b"\xff\xd8" + app0 + sof0 + b"\xff\xd9")
    assert raster_size(tmp_path / "b.jpg") == (640, 480)


def test_post_build_requires_bbl_and_pdf(paper_copy: Path) -> None:
    errs = check_arxiv(paper_copy, post_build=True).errors
    assert any("main.bbl missing" in e for e in errs)
    assert any("main.pdf missing" in e for e in errs)


def test_a1_no_generative_ai_disclosure_section() -> None:
    # Lead amendment A1: no generative-AI disclosure section, and none is required.
    import re

    for f in sorted((PAPER_ROOT / "sections").rglob("*.tex")):
        heads = re.findall(r"\\(?:sub)*section\*?\{([^}]*)\}", f.read_text(encoding="utf-8"))
        assert not [h for h in heads if re.search(r"generative|disclosure|\bAI\b", h, re.I)], f
    appendix = (PAPER_ROOT / "sections" / "appendix.tex").read_text(encoding="utf-8")
    assert re.findall(r"\\section\{([^}]*)\}", appendix) == [
        "Oracle Details", "Reproducibility", "Data Provenance"]
