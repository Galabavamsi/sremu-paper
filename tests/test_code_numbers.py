"""WP12: `code` numbers -- constants and defaults of the committed sremu code quoted in the
text are read from the code by the pipeline (DESIGN 1.6 rule 7), never typed into numbers.yaml.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from build_paper import resolve_code_values
from conftest import PAPER_ROOT
from paper_common import (
    Report,
    code_number_requests,
    load_code_values,
    load_numbers_spec,
    placeholder_count,
    render_numbers_tex,
)
from resolve_code_numbers import format_value, resolve

pytestmark = pytest.mark.unit

HAVE_SREMU = importlib.util.find_spec("sremu") is not None


# --------------------------------------------------------------------------- formats
@pytest.mark.parametrize(("value", "fmt", "want"), [
    (64, None, "64"),
    (2.0, None, "2"),
    (0.05, None, "0.05"),
    (-60.0, None, r"\ensuremath{-60}"),
    (-173.97506, ".3f", r"\ensuremath{-173.975}"),
    (1e-3, "sci", r"\ensuremath{10^{-3}}"),
    (2e-5, "sci", r"\ensuremath{2\times10^{-5}}"),
    (65536, ",d", "65,536"),
    ([0.0, 1.0, 5.0], None, "0, 1 and 5"),
    ((8, 16), None, "8 and 16"),
    ("tcp://127.0.0.1:5600", None, "tcp://127.0.0.1:5600"),
    ("a_b%", None, r"a\_b\%"),
])
def test_format_value(value: object, fmt: str | None, want: str) -> None:
    from fractions import Fraction

    assert format_value(value, fmt) == want
    if isinstance(value, int) and fmt is None:
        assert format_value(Fraction(value), None) == want


def test_format_value_refuses_bools_and_odd_types() -> None:
    with pytest.raises(TypeError):
        format_value(True, None)
    with pytest.raises(TypeError):
        format_value(object(), None)
    with pytest.raises(TypeError):
        format_value("x", ".3f")


def test_resolver_reports_errors_per_macro() -> None:
    out = resolve([
        {"macro": "numPi", "module": "math", "expr": "pi", "fmt": ".2f"},
        {"macro": "numBad", "module": "math", "expr": "nope", "fmt": None},
        {"macro": "numGone", "module": "no_such_module_xyz", "expr": "1", "fmt": None},
    ])
    assert out["values"] == {"numPi": "3.14"}
    assert set(out["errors"]) == {"numBad", "numGone"}


# --------------------------------------------------------------------------- numbers.yaml
def _spec(tmp_path: Path, source: dict, **extra: object) -> list:
    p = tmp_path / "numbers.yaml"
    p.write_text(yaml.safe_dump({"macros": [{"macro": "numX", "description": "d",
                                             "source": source, **extra}]}), encoding="utf-8")
    return load_numbers_spec(p)


def test_code_source_needs_an_sremu_module_and_an_expression(tmp_path: Path) -> None:
    spec = _spec(tmp_path, {"type": "code", "id": "sremu.timebase", "expr": "PAGE_MIN"})
    assert spec[0].expr == "PAGE_MIN"
    assert code_number_requests(spec) == [
        {"macro": "numX", "module": "sremu.timebase", "expr": "PAGE_MIN", "fmt": None}]
    with pytest.raises(ValueError, match="needs an expression"):
        _spec(tmp_path, {"type": "code", "id": "sremu.timebase"})
    with pytest.raises(ValueError, match="not an sremu module"):
        _spec(tmp_path, {"type": "code", "id": "os", "expr": "sep"})
    with pytest.raises(ValueError, match="only for code sources"):
        _spec(tmp_path, {"type": "study", "id": "V-TANG-1", "expr": "1"})
    with pytest.raises(ValueError, match="fixed value is allowed only"):
        _spec(tmp_path, {"type": "code", "id": "sremu.timebase", "expr": "PAGE_MIN"},
              value="64")


def test_code_macros_render_from_code_values_only() -> None:
    spec = load_numbers_spec(PAPER_ROOT / "numbers.yaml")
    code = [m for m in spec if m.source_type == "code"]
    assert code, "the paper quotes code constants through code macros"
    tex = render_numbers_tex(spec, {m.macro: "999" for m in code})  # results cannot fill them
    assert all(f"\\newcommand{{\\{m.macro}}}{{\\sremuPlaceholder" in tex for m in code)
    filled = render_numbers_tex(spec, {}, {code[0].macro: "64"})
    assert f"\\newcommand{{\\{code[0].macro}}}{{64}}%" in filled
    assert placeholder_count(filled) == placeholder_count(tex) - 1


def test_results_json_may_not_override_code_macros(paper_copy: Path) -> None:
    from build_paper import step_numbers

    spec = load_numbers_spec(paper_copy / "numbers.yaml")
    macro = next(m.macro for m in spec if m.source_type == "code")
    (paper_copy / "generated" / "results.json").write_text(
        json.dumps({"sremu_commit": "a" * 40, "values": {macro: "1"}}), encoding="utf-8")
    rep = Report("t")
    step_numbers(paper_copy, rep, mode="draft", commit="a" * 40)
    assert any(f"overrides code macros ['{macro}']" in e for e in rep.errors)


@pytest.mark.skipif(not HAVE_SREMU, reason="sremu is not importable")
def test_committed_code_values_match_the_installed_sremu() -> None:
    """generated/code_values.json (and so numbers.tex) is what the code says now; a changed
    constant in sremu makes this fail until the pipeline is rerun."""
    spec = load_numbers_spec(PAPER_ROOT / "numbers.yaml")
    fresh = resolve(code_number_requests(spec))
    assert fresh["errors"] == {}
    assert load_code_values(PAPER_ROOT) == fresh["values"]


@pytest.mark.skipif(not HAVE_SREMU, reason="sremu is not importable")
def test_pipeline_step_resolves_and_writes_code_values(paper_copy: Path) -> None:
    spec = load_numbers_spec(paper_copy / "numbers.yaml")
    rep = Report("t")
    values = resolve_code_values(paper_copy, spec, "draft", rep)
    assert rep.ok, rep.render()
    assert not rep.warnings, rep.render()
    assert values == load_code_values(paper_copy)
    assert values["numPageMin"] == "64"  # sremu.timebase.PAGE_MIN (DESIGN 4.3)


def test_unresolvable_code_number_is_a_warning_in_draft_and_an_error_in_final(
    paper_copy: Path,
) -> None:
    doc = yaml.safe_load((paper_copy / "numbers.yaml").read_text(encoding="utf-8"))
    doc["macros"].append({"macro": "numNope", "description": "d", "max_chars": 2,
                          "source": {"type": "code", "id": "sremu.timebase",
                                     "expr": "NO_SUCH_NAME"}})
    (paper_copy / "numbers.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    spec = load_numbers_spec(paper_copy / "numbers.yaml")
    draft = Report("t")
    resolve_code_values(paper_copy, spec, "draft", draft)
    assert draft.ok
    assert any("numNope unresolved" in w for w in draft.warnings)
    final = Report("t")
    resolve_code_values(paper_copy, spec, "final", final)
    assert any("numNope unresolved" in e for e in final.errors)
