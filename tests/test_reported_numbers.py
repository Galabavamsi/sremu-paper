"""WP12: REPORTED numbers -- third-party values quoted from the literature (numbers.yaml source
type `reported`, DESIGN 1.6 rule 7 and 11). Each macro names the BibTeX keys that report it,
and every sentence or table row that uses it must cite them (check_claims.py rule 7)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from check_claims import check_claims, units_with_cites
from conftest import PAPER_ROOT, append
from paper_common import NUMBER_SOURCE_TYPES, load_numbers_spec

pytestmark = pytest.mark.unit

FUTURE = Path("sections") / "09_future_work.tex"
HEAD = "macros:\n"


def _spec(tmp_path: Path, source: str, value: str | None = '"64"') -> list:
    body = f"  - macro: numX\n    kind: number\n    description: d\n    source: {source}\n"
    if value is not None:
        body += f"    value: {value}\n"
    path = tmp_path / "numbers.yaml"
    path.write_text(HEAD + body, encoding="utf-8")
    return load_numbers_spec(path)


# --------------------------------------------------------------------------- schema
def test_reported_source_type_loads_with_value_and_cite(tmp_path: Path) -> None:
    assert "reported" in NUMBER_SOURCE_TYPES
    (m,) = _spec(tmp_path, "{type: reported, id: KeyA, cite: [KeyA, KeyB]}")
    assert (m.source_type, m.value, m.cite) == ("reported", "64", ("KeyA", "KeyB"))


@pytest.mark.parametrize(("source", "value", "match"), [
    ("{type: reported, id: KeyA, cite: [KeyA]}", None, "needs the quoted value"),
    ("{type: reported, id: KeyA}", '"64"', "list of BibTeX keys"),
    ("{type: reported, id: KeyA, cite: []}", '"64"', "list of BibTeX keys"),
    ("{type: reported, id: KeyA, cite: KeyA}", '"64"', "list of BibTeX keys"),
    ("{type: reported, id: KeyA, cite: [KeyB]}", '"64"', "id 'KeyA' is not in source.cite"),
    ("{type: data, id: x, cite: [KeyA]}", '"64"', "cite is allowed only for reported"),
    ("{type: study, id: x, cite: [KeyA]}", None, "cite is allowed only for reported"),
])
def test_reported_schema_errors(tmp_path: Path, source: str, value: str | None,
                                match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _spec(tmp_path, source, value)


def test_paper_reported_macros_use_the_reported_type() -> None:
    """The third-party numbers of Section II and Table I are `reported`, not `data`."""
    spec = load_numbers_spec(PAPER_ROOT / "numbers.yaml")
    rep = {m.macro for m in spec if m.source_type == "reported"}
    # numRep*/txtRep* followed by a capital letter (txtRepoUrl is an availability macro)
    named = {m.macro for m in spec if re.match(r"(num|txt)Rep[A-Z]", m.macro)}
    assert named
    assert named == rep
    assert all(m.cite and m.value is not None for m in spec if m.source_type == "reported")
    assert not any(m.source_id.startswith("REPORTED:") for m in spec)


# --------------------------------------------------------------------------- unit split
def test_units_with_cites_keeps_keys_per_sentence_and_row() -> None:
    tex = ("GoSimRIS has \\numRepGoSimRisMaxElements{} elements~\\cite{A,B}. Next one.\n"
           "% a comment~\\cite{Hidden}\n"
           "\\begin{tabular}{ll}\nX~\\cite{C} & 64 \\\\\nY & \\numRepTraceRttMs \\\\\n"
           "\\end{tabular}\n\\sremustub{planned~\\cite{Stub}}\n")
    units = units_with_cites(tex)
    by_text = {u.split()[0].rstrip("~"): keys for u, keys in units}
    assert by_text["GoSimRIS"] == {"A", "B"}
    assert by_text["Next"] == set()
    assert by_text["X"] == {"C"}
    assert by_text["Y"] == set()
    assert not any(k in {"Hidden", "Stub"} for _, keys in units for k in keys)


# --------------------------------------------------------------------------- check_claims
def test_reported_macro_needs_its_citation_in_the_same_unit(paper_copy: Path) -> None:
    assert check_claims(paper_copy, "draft", junit=[]).ok
    append(paper_copy / FUTURE, "\n"
           "GoSimRIS supports \\numRepGoSimRisMaxElements{} elements. It is cited later"
           "~\\cite{ElGhalbzouri2025GoSimRIS}.\n\n"
           "ORIX runs every \\numRepOrixLoopMinMs~ms~\\cite{Kayraklik2026ORIX}.\n\n"
           "\\begin{tabular}{ll}\nTRACE~\\cite{Gurses2026ACHEM} & \\numRepTraceRttMs~ms \\\\\n"
           "TRACE~\\cite{Kumar2026TRACE} & \\numRepTraceRttMs~ms \\\\\n\\end{tabular}\n")
    errs = check_claims(paper_copy, "draft", junit=[]).errors
    bad = [e for e in errs if "reported number" in e]
    assert len(bad) == 2, errs
    assert any("\\numRepGoSimRisMaxElements without citing ElGhalbzouri2025GoSimRIS" in e
               for e in bad)
    assert any("\\numRepTraceRttMs without citing Kumar2026TRACE" in e for e in bad)
    assert not any("numRepOrixLoopMinMs" in e for e in bad)


def test_reported_cite_keys_must_exist_in_refs_bib(paper_copy: Path) -> None:
    numbers = paper_copy / "numbers.yaml"
    text = numbers.read_text(encoding="utf-8")
    old = "cite: [Kumar2026TRACE]}"
    assert text.count(old) == 1
    numbers.write_text(text.replace(old, "cite: [Kumar2026TRACE, NoSuchKey2099]}"),
                       encoding="utf-8")
    errs = check_claims(paper_copy, "draft", junit=[]).errors
    assert any("numbers:numRepTraceRttMs: cite key NoSuchKey2099 not in refs.bib" in e
               for e in errs), errs
    # the text cites Kumar2026TRACE only, so every use now misses the second key too
    assert any("\\numRepTraceRttMs without citing NoSuchKey2099" in e for e in errs), errs
