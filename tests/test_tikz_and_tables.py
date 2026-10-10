"""WP12: TikZ diagrams F1/F2 and the tables generated from the sremu code (Table III).

F1 and F2 carry no data: build_paper.py compiles their standalone sources whenever LaTeX runs
and keys them by the sha256 of the source and its style inputs. Table III is rendered from
``sremu.api.metadata.table_iii()`` and checked against the ``sremu.compat`` signatures.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest
from build_paper import figure_state, load_registry, tikz_key, validate_registry
from conftest import PAPER_ROOT
from paper_common import Report, strip_tex_comments

pytestmark = pytest.mark.unit

HAVE_SREMU = importlib.util.find_spec("sremu") is not None


# --------------------------------------------------------------------------- TikZ
def test_tikz_entries_name_their_sources_and_style() -> None:
    reg = load_registry(PAPER_ROOT)
    for fid in ("F1", "F2"):
        e = reg[fid]
        assert e["source"] == "tikz"
        assert e["status"] == "ready"
        assert (PAPER_ROOT / e["script"]).is_file()
        assert e["inputs"] == ["figures/tikz/sremu-figstyle.tex"]
        assert e["data_classes"] == []
        src = (PAPER_ROOT / e["script"]).read_text(encoding="utf-8")
        assert "\\input{figures/tikz/sremu-figstyle}" in src
    assert reg["F1"]["width"] == "single"
    assert reg["F2"]["width"] == "double"


def test_tikz_sources_are_drawn_at_their_printed_width() -> None:
    """Drawn at \\columnwidth (252 pt) or \\textwidth (516 pt) of IEEEtran journal and
    included unscaled, so the 8 pt labels stay 8 pt (DESIGN 13.4)."""
    widths = {"F1": "252pt", "F2": "516pt"}
    style = (PAPER_ROOT / "figures" / "tikz" / "sremu-figstyle.tex").read_text(encoding="utf-8")
    assert "\\fontsize{8}" in style
    assert "Okabe-Ito" in style
    for fid, width in widths.items():
        src = strip_tex_comments(
            (PAPER_ROOT / "figures" / "tikz" / f"{fid}.tex").read_text(encoding="utf-8"))
        assert re.search(r"\\useasboundingbox \([^)]*\) rectangle \(" + width, src), fid
        # no font size command below 8 pt in the diagram itself
        assert not re.search(r"\\(tiny|scriptsize|footnotesize)\b", src), fid


def test_tikz_key_tracks_source_and_style(tmp_path: Path) -> None:
    (tmp_path / "f.tex").write_text("a", encoding="utf-8")
    (tmp_path / "s.tex").write_text("b", encoding="utf-8")
    entry = {"source": "tikz", "script": "f.tex", "inputs": ["s.tex"], "data_classes": []}
    k0 = tikz_key(tmp_path, entry)
    (tmp_path / "s.tex").write_text("c", encoding="utf-8")
    k1 = tikz_key(tmp_path, entry)
    assert k0 != k1
    (tmp_path / "f.tex").write_text("d", encoding="utf-8")
    assert tikz_key(tmp_path, entry) != k1
    assert tikz_key(tmp_path, {"script": None}) is None


def test_tikz_figure_state(tmp_path: Path) -> None:
    (tmp_path / "f.tex").write_text("a", encoding="utf-8")
    entry = {"source": "tikz", "script": "f.tex", "inputs": [], "data_classes": []}
    assert figure_state(tmp_path, "F1", entry, "x") == "pending"
    (tmp_path / "figures" / "out").mkdir(parents=True)
    (tmp_path / "figures" / "data").mkdir(parents=True)
    (tmp_path / "figures" / "out" / "F1.pdf").write_bytes(b"%PDF-1.4\n")
    assert figure_state(tmp_path, "F1", entry, "x") == "stale"  # no meta yet
    meta = tmp_path / "figures" / "data" / "F1.meta.json"
    meta.write_text(json.dumps({"content_key": tikz_key(tmp_path, entry)}), encoding="utf-8")
    assert figure_state(tmp_path, "F1", entry, "x") == "current"
    assert figure_state(tmp_path, "F1", entry, "y") == "current"  # no data: commit-free
    (tmp_path / "f.tex").write_text("changed", encoding="utf-8")
    assert figure_state(tmp_path, "F1", entry, "x") == "stale"
    hand = {"source": "tikz", "script": None, "inputs": [], "data_classes": []}
    assert figure_state(tmp_path, "F1", hand, "x") == "current"


def test_registry_tikz_rules() -> None:
    reg = {k: dict(v) for k, v in load_registry(PAPER_ROOT).items()}
    reg["F1"]["script"] = "figures/tikz/F1.py"
    reg["F2"]["data_classes"] = ["SIM-sremu"]
    rep = Report("t")
    validate_registry(reg, rep)
    assert any("F1: a TikZ figure's script must be its .tex source" in e for e in rep.errors)
    assert any("F2: a TikZ diagram carries no data classes" in e for e in rep.errors)


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk not installed")
def test_draw_tikz_writes_pdf_and_meta(paper_copy: Path) -> None:
    from build_paper import draw_tikz

    reg = load_registry(paper_copy)
    rep = Report("t")
    assert draw_tikz(paper_copy, "F1", reg["F1"], rep), rep.render()
    assert (paper_copy / "figures" / "out" / "F1.pdf").stat().st_size > 5_000
    meta = json.loads((paper_copy / "figures" / "data" / "F1.meta.json").read_text("utf-8"))
    assert meta["content_key"] == tikz_key(paper_copy, reg["F1"])
    assert meta["data_classes"] == []
    assert figure_state(paper_copy, "F1", reg["F1"], "UNPINNED") == "current"


# --------------------------------------------------------------------------- Table III
@pytest.mark.skipif(not HAVE_SREMU, reason="sremu is not importable")
def test_committed_table3_equals_generator_output() -> None:
    import gen_tables

    want = gen_tables.render_table3(gen_tables.table3_rows())
    table = PAPER_ROOT / "generated" / "tables" / "table3_api.tex"
    assert table.read_text(encoding="utf-8") == want
    assert "\\label{tab:api}" in want


@pytest.mark.skipif(not HAVE_SREMU, reason="sremu is not importable")
def test_table3_rows_follow_sremu_metadata_and_compat() -> None:
    import gen_tables
    from sremu.api.metadata import table_iii

    rows = gen_tables.table3_rows()
    assert [r[0] for r in rows] == [m.sremu for m in table_iii()]
    assert [r[1] for r in rows] == [m.uhd for m in table_iii()]
    assert rows[0][2].startswith("``Send_to_sim(")


def test_table3_v1_column_must_cover_every_row(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("sremu")
    import gen_tables

    monkeypatch.setitem(gen_tables.V1_COLUMN, "Device.extra", "none")
    with pytest.raises(ValueError, match="v1 column keys"):
        gen_tables.table3_rows()


def test_tex_escaping_of_code_spans() -> None:
    import gen_tables

    out = gen_tables.tex("``tx_streamer::send`` and ``<node>.tx0``")
    assert out == ("\\texttt{tx\\_streamer::\\allowbreak{}send} and "
                   "\\texttt{\\textless{}node\\textgreater{}.tx0}")
    with pytest.raises(ValueError):
        gen_tables.tex("``\u03bb``")
