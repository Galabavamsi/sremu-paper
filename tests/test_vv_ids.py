"""Track D (must-fix 12, 14): the identifier tables and the provenance summary of tools/vv_ids.py.

The identifier rows transcribe facts of the emulator; the check that keeps them honest is that
every number of a row appears in sremu's specification, oracle table or test docstrings. These
tests show that the check rejects an invented number, that cited identifiers (including ranges)
are found, that the text conventions render as intended, and that the provenance summary
groups the rows of the provenance table without losing one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import vv_ids
from conftest import PAPER_ROOT, SREMU_ROOT

pytestmark = pytest.mark.unit

needs_sremu = pytest.mark.skipif(not (SREMU_ROOT / "docs" / "DESIGN.md").is_file(),
                                 reason="sremu checkout not found next to the paper")


@needs_sremu
def test_number_check_accepts_sources_and_rejects_invented_values() -> None:
    design, oracle, by_token = vv_ids.source_texts(SREMU_ROOT)
    src = vv_ids._id_source("PH-01", {}, design, oracle, by_token, SREMU_ROOT)
    allowed = vv_ids.numbers(src)
    assert all(vv_ids._close(x, allowed) for x in vv_ids.numbers("rel. 1e-12; -63.329 dB"))
    assert [x for x in vv_ids.numbers("rel. 1e-11") if not vv_ids._close(x, allowed)] == [1e-11]
    assert [x for x in vv_ids.numbers("-63.33 dB") if not vv_ids._close(x, allowed)] == [63.33]


@needs_sremu
def test_oracle_table_is_parsed_not_imported() -> None:
    table = vv_ids._oracle_table(SREMU_ROOT)
    ph11 = table["PH11_J_max_dev_db"]  # written as 0.15 - 0.123 in the source
    assert ph11.spec_id == "PH-11"
    assert abs(ph11.tol - 0.027) < 1e-12


def test_cited_ids_expand_ranges(tmp_path: Path) -> None:
    (tmp_path / "sections").mkdir()
    (tmp_path / "generated" / "tables").mkdir(parents=True)
    (tmp_path / "sections" / "a.tex").write_text(
        "PH-02--07 and \\texttt{G1--3}; RT-01--RT-03, VA-3--VA-5 and V-CFO-4. % PH-30\n",
        encoding="utf-8")
    got = set(vv_ids.cited_ids(tmp_path))
    assert {"PH-02", "PH-07", "G1", "G3", "RT-02", "VA-4", "V-CFO-4"} <= got
    assert "PH-30" not in got  # comments do not cite


def test_tex_conventions() -> None:
    assert vv_ids.tex("40 deg; +-1e-9 rad; 10% dead") == r"40$^\circ$; $\pm10^{-9}$ rad; 10\% dead"
    assert vv_ids.tex("rel. 2e-3") == r"rel. $2\times10^{-3}$"
    assert vv_ids.tex(r"$|d|$: $-0.18$ dB") == r"$|d|$: $-0.18$ dB"  # math kept, not merged


def test_every_cited_identifier_has_a_row() -> None:
    rows = {r["id"] for r in (vv_ids.read_yaml(PAPER_ROOT / vv_ids.IDS_FILE) or {})["ids"]}
    missing = sorted(set(vv_ids.cited_ids(PAPER_ROOT)) - rows)
    assert missing == []


def test_provenance_summary_groups_every_row(tmp_path: Path) -> None:
    paper_copy = tmp_path / "paper"
    for rel in ("numbers.yaml", vv_ids.VLAB_META, vv_ids.VLAB_CHECK):
        (paper_copy / rel).parent.mkdir(parents=True, exist_ok=True)
        (paper_copy / rel).write_bytes((PAPER_ROOT / rel).read_bytes())
    rows = [
        {"result": "Test record", "commit": "aaaaaaa", "tree": "clean"},
        {"result": "E-A (aggregate)", "commit": "aaaaaaa", "tree": "clean"},
        {"result": "V-LAB", "commit": "7a63287", "tree": "clean"},
        {"result": "B1", "commit": "309c633", "tree": "flagged"},
        {"result": "B2", "commit": "309c633", "tree": "flagged"},
        {"result": "E-E throughput", "commit": "n/r", "tree": "n/r"},
    ]
    meta = paper_copy / vv_ids.VLAB_META
    meta.write_text(meta.read_text(encoding="utf-8").replace(
        "4ec4c2b6ed06f8f85fd04a350959053fbb3f6f24", "aaaaaaa" + "0" * 33), encoding="utf-8")
    v = vv_ids.provenance_values(paper_copy, rows, {"sremu_commit": "aaaaaaa" + "0" * 33,
                                                    "sremu_dirty": False, "host": "AWS x"})
    assert v["txtProvRegenCommit"] == "aaaaaaa"
    assert v["txtProvRegenResults"] == "the test record and E-A"
    assert v["txtProvOtherCommits"] == (
        "V-LAB (7a63287, clean); B1 and B2 (309c633, dirty flag recorded,"
        " file tree equal to the commit's); E-E throughput (commit not recorded)")
    assert vv_ids._short("E-B, arc geometry") == "E-B (arc geometry)"
    assert vv_ids._short("Cell-model sensitivity") == "cell-model sensitivity"
    with pytest.raises(SystemExit):
        vv_ids.provenance_values(paper_copy, rows, {"sremu_commit": "aaaaaaa", "sremu_dirty": True})
