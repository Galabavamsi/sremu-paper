"""WP12: the paper skeleton follows DESIGN 13.1 and 13.3 (title, keywords, section order).

Oracle: DESIGN.md itself (sremu/docs/DESIGN.md), parsed here, so the expected titles are not
typed by hand. Lead amendment A1 removes the generative-AI disclosure from the appendix row.
"""

from __future__ import annotations

import re

import pytest
from check_claims import remove_command
from conftest import PAPER_ROOT, SREMU_ROOT
from paper_common import strip_tex_comments

pytestmark = pytest.mark.unit

DESIGN = SREMU_ROOT / "docs" / "DESIGN.md"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]+", " ", s.lower()).strip()


def _design_section(heading: str) -> str:
    text = DESIGN.read_text(encoding="utf-8")
    start = text.index(heading)
    end = text.index("\n### ", start + len(heading))
    return text[start:end]


def _main() -> str:
    return strip_tex_comments((PAPER_ROOT / "main.tex").read_text(encoding="utf-8"))


# Author decision of 2026-10-11 (title T1, research/analysis/revision_plan.md PART 9) replaces
# the DESIGN 13.1 recommended title, whose two named evaluations run on the coefficient path and
# whose "Open" waits for the release approval (human gate H-2).
TITLE_T1 = ("Timed RIS Control in the Loop: A Sample-Accurate IQ-Level Emulator with "
            "Characterized Timing")


def test_title_is_the_author_decision_t1() -> None:
    assert re.search(r"\*\*Title \(recommended\):\*\*", _design_section("### 13.1"))
    title = re.search(r"\\title\{(.*?)\}\n", _main(), re.S)
    assert title is not None
    text = title.group(1).replace("\\\\", " ")
    assert _norm(text) == _norm(TITLE_T1)
    gates = (PAPER_ROOT / "human_gates.yaml").read_text(encoding="utf-8")
    h2_open = re.search(r"H-2:.*?status: (\w+)", gates, re.S)
    assert h2_open is not None
    if h2_open.group(1) != "closed":  # "Open" only after the licence and release approval
        assert "open" not in _norm(text).split()


def test_keywords_keep_smart_radio_environment() -> None:
    kw = re.search(r"\\begin\{IEEEkeywords\}(.*?)\\end\{IEEEkeywords\}", _main(), re.S)
    assert kw is not None
    assert "smart radio environment" in " ".join(kw.group(1).split()).lower()


def test_sections_follow_design_13_3_in_order() -> None:
    rows = [ln for ln in _design_section("### 13.3").splitlines() if ln.startswith("| ")]
    expected = []
    for ln in rows[1:]:  # skip the header row
        num, title = (c.strip() for c in ln.strip("|").split("|")[:2])
        if re.fullmatch(r"[IVX]+", num):
            expected.append(title)
    assert len(expected) == 10
    inputs = re.findall(r"\\input\{(sections/\d\d_[a-z_]+)\}", _main())
    titles = []
    for rel in inputs:
        tex = strip_tex_comments((PAPER_ROOT / f"{rel}.tex").read_text(encoding="utf-8"))
        heads = re.findall(r"\\section\{([^}]*)\}", tex)
        assert len(heads) == 1, rel
        titles.append(heads[0])
    assert [_norm(t) for t in titles] == [_norm(t) for t in expected]


def test_appendix_follows_13_3_minus_ai_disclosure_a1() -> None:
    row = next(ln for ln in _design_section("### 13.3").splitlines() if ln.startswith("| App."))
    parts = [p.strip() for p in row.strip("|").split("|")[1].split(",")]
    expected = [p for p in parts if "generative-AI" not in p]  # amendment A1
    assert len(expected) == len(parts) - 1 == 3
    appendix = strip_tex_comments(
        (PAPER_ROOT / "sections" / "appendix.tex").read_text(encoding="utf-8"))
    heads = re.findall(r"\\section\{([^}]*)\}", appendix)
    assert len(heads) == 3
    for head, want in zip(heads, expected, strict=True):
        assert _norm(head).split()[0] == _norm(want).split()[0], (head, want)


def test_vv_section_has_the_six_13_3_subsections() -> None:
    tex = strip_tex_comments(
        (PAPER_ROOT / "sections" / "05_verification_validation.tex").read_text("utf-8"))
    subs = re.findall(r"\\subsection\{([^}]*)\}", tex)
    assert len(subs) == 6  # V-A oracles ... V-F Doppler
    want = ["oracle", "matlab", "path loss", "laboratory", "cfo", "doppler"]
    assert all(w in _norm(s) for w, s in zip(want, subs, strict=True)), subs


def test_author_block_order_and_corresponding_author() -> None:
    authors = re.search(r"\\author\{(.*?)\\thanks", _main(), re.S)
    assert authors is not None
    names = [n.strip() for n in re.split(r",|\band~", authors.group(1).replace("%", ""))]
    names = [n.replace("~", " ") for n in names if n]
    assert names == ["Galaba Vamsi", "Shubhika Mishra", "Anurag Jaiswal", "Smriti Kumar",
                     "Mansi Ambwani", "Arzad Alam Kherani"]
    thanks = [" ".join(t.split()) for t in re.findall(r"\\thanks\{([^{}]*)\}", _main())]
    assert "Corresponding author: Galaba Vamsi (e-mail: galabav@iitbhilai.ac.in)." in thanks
    assert any("Department of Electrical Engineering, Indian Institute of Technology Bhilai"
               in t for t in thanks)
    # the claim-checker drops \thanks, so affiliation text never trips the number rule
    assert "galabavamsi" not in remove_command(_main(), "thanks", 1)
