"""WP12: check_refs.py -- every entry identified, verified, consistent with the source."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from check_refs import check_refs, parse_verification_rows
from conftest import PAPER_ROOT, append
from paper_common import parse_bib

pytestmark = pytest.mark.unit

ROW = "| `{key}` | {origin} | x | test row |\n"


def _add_entry(root: Path, entry: str, key: str, origin: str = "new") -> None:
    append(root / "refs.bib", "\n" + entry + "\n")
    append(root / "refs_verification.md", ROW.format(key=key, origin=origin))


def test_real_bibliography_passes() -> None:
    rep = check_refs(PAPER_ROOT)
    assert rep.ok, rep.render()
    entries = parse_bib((PAPER_ROOT / "refs.bib").read_text(encoding="utf-8"))
    # 101 entries of stage A plus 4 verified for Sections III-IV and VIII (stage C): Philox,
    # UCB1, Gymnasium, ZeroMQ (rows with origin "new" in refs_verification.md)
    assert len(entries) == 105
    rows = parse_verification_rows((PAPER_ROOT / "refs_verification.md").read_text("utf-8"))
    assert set(rows) == {e.key for e in entries}


def test_every_entry_has_identifier_in_real_bib() -> None:
    for e in parse_bib((PAPER_ROOT / "refs.bib").read_text(encoding="utf-8")):
        f = e.fields
        assert f.get("doi") or f.get("eprint") or f.get("url") or "\\url{" in f.get(
            "howpublished", ""), e.key


def test_design_13_6_to_verify_list_present() -> None:
    entries = {e.key: e.fields for e in parse_bib((PAPER_ROOT / "refs.bib").read_text("utf-8"))}
    expected = {
        "Tang2022mmWave": ("doi", "10.1109/TCOMM.2022.3193400"),
        "Kumar2026TRACE": ("eprint", "2608.11798"),
        "WuZhang2020Discrete": ("eprint", "1906.03165"),
        "Abeywickrama2020Practical": ("eprint", "2002.10112"),
        "Mishra2025WCL": ("doi", "10.1109/LWC.2025.3535557"),
        "Mishra2025NCC": ("doi", "10.1109/NCC63735.2025.10983019"),
        "Gurses2026ACHEM": ("eprint", "2604.04742"),
        "Ellingson2021PathLoss": ("eprint", "1912.06759"),
        "Kay1989Freq": ("doi", "10.1109/29.45547"),
        "Bettstetter2003RWP": ("doi", "10.1109/TMC.2003.1233531"),
        "Laakso1996FracDelay": ("doi", "10.1109/79.482137"),
        "Matthiesen2021Mobility": ("eprint", "2006.06991"),
        "Zhao2024OptimalDiscrete": ("doi", "10.1109/LWC.2023.3331232"),
    }
    for key, (field, value) in expected.items():
        assert entries[key][field] == value, key
    for key in ("ETSI_GR_RIS001_v121", "ETSI_GR_RIS002_v121", "ETSI_GR_RIS003_v121",
                "ETSI_GS_RIS008", "SigMF", "UHDManual"):
        assert key in entries
    assert "doi" not in entries["SigMF"]  # its CITATION.cff DOI resolves to v0.0.1 (2018)


def test_entry_without_identifier_fails(paper_copy: Path) -> None:
    _add_entry(paper_copy, "@misc{NoId2026,\n  title = {Untraceable}\n}", "NoId2026")
    rep = check_refs(paper_copy)
    assert any("NoId2026: no doi, eprint or official url" in e for e in rep.errors)


def test_non_official_host_fails(paper_copy: Path) -> None:
    _add_entry(paper_copy, "@misc{Wiki,\n  title = {X},\n  url = {https://en.wikipedia.org/wiki/X}\n}",
               "Wiki")
    assert any("non-official host" in e for e in check_refs(paper_copy).errors)


def test_bare_url_and_underscore_in_note_fail(paper_copy: Path) -> None:
    _add_entry(paper_copy, "@misc{BadNote,\n  title = {X},\n  url = {https://example.org/x},\n"
               "  note = {see https://github.com/a/b_c}\n}", "BadNote")
    errs = check_refs(paper_copy).errors
    assert any("bare URL" in e for e in errs)
    assert any("unescaped '_'" in e for e in errs)


def test_malformed_doi_and_eprint_fail(paper_copy: Path) -> None:
    _add_entry(paper_copy, "@misc{Mal,\n  title = {X},\n  doi = {doi:10.1/x},\n"
               "  eprint = {arXiv:1234.5678},\n  archivePrefix = {arXiv}\n}", "Mal")
    errs = check_refs(paper_copy).errors
    assert any("malformed doi" in e for e in errs)
    assert any("malformed arXiv eprint" in e for e in errs)


def test_source_entry_altered_fails(paper_copy: Path) -> None:
    bib = paper_copy / "refs.bib"
    text = bib.read_text(encoding="utf-8")
    assert "doi     = {10.1109/29.45547}" in text
    bib.write_text(text.replace("doi     = {10.1109/29.45547}", "doi     = {10.1109/29.45548}"),
                   encoding="utf-8")
    errs = check_refs(paper_copy).errors
    assert any("Kay1989Freq: field 'doi' differs from the verified source" in e for e in errs)


def test_source_copy_matches_recorded_sha256(paper_copy: Path) -> None:
    src = paper_copy / "refs_sources" / "refs_verified_2026-10-10.bib"
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    md = (paper_copy / "refs_verification.md").read_text(encoding="utf-8")
    assert f"sha256\n  `{digest}`" in md or f"sha256 `{digest}`" in md
    # editing the source copy (here: the Kay DOI in both files) is caught by the hash
    for path in (src, paper_copy / "refs.bib"):
        text = path.read_text(encoding="utf-8")
        assert "doi     = {10.1109/29.45547}" in text
        path.write_text(text.replace("doi     = {10.1109/29.45547}",
                                     "doi     = {10.1109/29.45548}"), encoding="utf-8")
    errs = check_refs(paper_copy).errors
    assert any("is not the one recorded in refs_verification.md" in e for e in errs), errs
    assert not any("Kay1989Freq: field 'doi' differs" in e for e in errs)


def test_missing_and_inconsistent_rows_fail(paper_copy: Path) -> None:
    md = paper_copy / "refs_verification.md"
    text = md.read_text(encoding="utf-8")
    text = re.sub(r"^\| `Bettstetter2003RWP` .*\n", "", text, flags=re.M)
    text = text.replace("| `Kay1989Freq` | source |", "| `Kay1989Freq` | new |")
    md.write_text(text, encoding="utf-8")
    errs = check_refs(paper_copy).errors
    assert any("Bettstetter2003RWP: no row" in e for e in errs)
    assert any("Kay1989Freq: marked 'new' but present in the source" in e for e in errs)


def test_cited_key_missing_from_bib_fails(paper_copy: Path) -> None:
    append(paper_copy / "sections" / "10_conclusion.tex", "\nSee~\\cite{NoSuchKey2026}.\n")
    assert any("NoSuchKey2026" in e for e in check_refs(paper_copy).errors)


def test_parse_bib_skips_commented_entries() -> None:
    text = "% @article{Hidden,\n%   title = {x}\n% }\n@misc{Shown,\n  title = {y}\n}\n"
    assert [e.key for e in parse_bib(text)] == ["Shown"]
