"""Code and Data Availability: the statement is complete without a URL, licence or DOI while the
release is conditional (availability.yaml status: conditional), and no availability field can
print '??' in a draft or stop a final build as a placeholder.

With status released, filled fields become their macros and PENDING fields stay placeholders
(a final build refuses), as before.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import PAPER_ROOT
from gen_results import AVAILABILITY_MACROS, UNRELEASED_MACRO, availability_values

pytestmark = pytest.mark.unit

FIELDS = "licence: {l}\nrepo_url: {r}\narchive_doi: {a}\ndata_doi: {d}\n"


def _write(root: Path, status: str | None, **vals: str) -> None:
    text = (f"status: {status}\n" if status is not None else "") + FIELDS.format(
        l=vals.get("l", "PENDING"), r=vals.get("r", "PENDING"), a=vals.get("a", "PENDING"),
        d=vals.get("d", "PENDING"))
    (root / "availability.yaml").write_text(text, encoding="utf-8")


def test_conditional_status_gives_no_placeholder(tmp_path: Path) -> None:
    _write(tmp_path, "conditional")
    out = availability_values(tmp_path)
    assert out == dict.fromkeys(AVAILABILITY_MACROS.values(), UNRELEASED_MACRO)


def test_conditional_status_ignores_filled_fields(tmp_path: Path) -> None:
    """A licence chosen before the release must not leak into the conditional statement."""
    _write(tmp_path, "conditional", l="Apache-2.0", r="https://github.com/x/sremu")
    assert set(availability_values(tmp_path).values()) == {UNRELEASED_MACRO}


def test_released_status_fills_fields_and_keeps_pending_as_placeholders(tmp_path: Path) -> None:
    _write(tmp_path, "released", l="Apache-2.0", r="https://github.com/x/sremu",
           a="10.5281/zenodo.1")
    out = availability_values(tmp_path)
    assert out["txtLicence"] == "Apache-2.0"
    assert out["txtRepoUrl"] == r"\url{https://github.com/x/sremu}"
    assert out["txtArchiveDoi"] == "10.5281/zenodo.1"
    assert "txtDataDoi" not in out  # PENDING: stays a placeholder, a final build refuses


@pytest.mark.parametrize("status", [None, "pending", "open"])
def test_unknown_or_missing_status_is_refused(tmp_path: Path, status: str | None) -> None:
    _write(tmp_path, status)
    with pytest.raises(SystemExit):
        availability_values(tmp_path)


def test_back_matter_matches_availability_status() -> None:
    """While the status is conditional, the section uses none of the four macros (they would
    print [not released] in a draft and stop a final build) and states access on request; once
    it is released, the section names the repository and the licence through the macros."""
    doc = (PAPER_ROOT / "availability.yaml").read_text(encoding="utf-8")
    m = re.search(r"^status:\s*(\S+)\s*$", doc, re.M)
    assert m
    assert m.group(1) in ("conditional", "released")
    body = "\n".join(line for line in (PAPER_ROOT / "sections" / "back_matter.tex").read_text(
        encoding="utf-8").splitlines() if not line.lstrip().startswith("%"))
    if m.group(1) == "conditional":
        for macro in AVAILABILITY_MACROS.values():
            assert "\\" + macro not in body
        assert "on request" in body
    else:
        assert "\\txtRepoUrl" in body
        assert "\\txtLicence" in body


def test_unreleased_macro_is_defined_for_both_build_modes() -> None:
    tex = (PAPER_ROOT / "sremu-macros.tex").read_text(encoding="utf-8")
    m = re.search(r"\\newcommand\{\\sremuUnreleased\}\{(.*?)\n\s*\\fi\}", tex, re.S)
    assert m, "sremu-macros.tex lacks \\sremuUnreleased"
    assert "\\ifsremudraft" in m.group(1)
    assert "\\PackageError" in m.group(1)
