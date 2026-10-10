"""WP12: build_paper.py -- draft builds never touch git; final builds refuse while anything
is outstanding (DESIGN 10.13, 13.5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from build_paper import (
    EXPECTED_FIGURES,
    INSTALLED_SREMU_PROBE,
    GitProbe,
    build,
    check_figure_sources,
    content_key,
    default_runner,
    figure_state,
    gate_status,
    load_gates,
    load_reference_rule,
    load_registry,
    load_results,
    step_gates,
    validate_registry,
)
from conftest import PAPER_ROOT, SREMU_ROOT, append, junit_xml, pinned
from paper_common import (
    Report,
    junit_provenance,
    junit_provenance_errors,
    load_code_values,
    load_junit,
    load_numbers_spec,
    placeholder_count,
    render_numbers_tex,
)

pytestmark = pytest.mark.unit


class Recorder:
    """Runner that records commands, refuses git, and runs python helpers for real."""

    def __init__(self, git_head: str | None = None, clean: bool = True, allow_git: bool = False,
                 sremu_file: str | None = None):
        self.calls: list[list[str]] = []
        self.git_head, self.clean, self.allow_git = git_head, clean, allow_git
        self.sremu_file = sremu_file  # None: ask the real interpreter

    def __call__(self, cmd: list[str], cwd: Path) -> tuple[int, str]:
        self.calls.append(cmd)
        if cmd[0] == "git":
            if not self.allow_git:
                raise AssertionError(f"git called in a draft build: {cmd}")
            if cmd[1] == "rev-parse":
                return 0, f"{self.git_head}\n"
            return 0, "" if self.clean else " M file.py\n"
        if "latexmk" in cmd[0]:
            raise AssertionError("latexmk must not run in this test")
        if cmd[1:] == ["-c", INSTALLED_SREMU_PROBE] and self.sremu_file is not None:
            return 0, self.sremu_file
        return default_runner(cmd, cwd)


def _all_gate_ids() -> list[str]:
    gates, _ = load_gates(PAPER_ROOT)
    return [i for ids in gates.values() for i in ids]


def _green_junit(path: Path, props: dict[str, str] | None = None, drop: str = "") -> Path:
    """All gate ids passing; backend-rule ids once per backend except `drop`."""
    _, per_id = load_gates(PAPER_ROOT)
    names: dict[str, str] = {}
    for i in _all_gate_ids():
        stem = f"test_{i.lower().replace('-', '_')}_case"
        for b in per_id.get(i) or [""]:
            if not b:
                names[stem] = "passed"
            elif b != drop:
                names[f"{stem}[{b}]"] = "passed"
    path.write_text(junit_xml(names, props), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- numbers
def test_committed_numbers_tex_equals_generator_output() -> None:
    spec = load_numbers_spec(PAPER_ROOT / "numbers.yaml")
    committed = (PAPER_ROOT / "generated" / "numbers.tex").read_text(encoding="utf-8")
    want = render_numbers_tex(spec, load_results(PAPER_ROOT)[0], load_code_values(PAPER_ROOT))
    assert committed == want


def test_committed_table2_equals_sremu_model_card() -> None:
    """Table II in the paper is the generator's rendering of sremu docs/model_card.yaml."""
    import importlib.util

    gen_path = SREMU_ROOT / "docs" / "tools" / "gen_model_card.py"
    spec = importlib.util.spec_from_file_location("gen_model_card_t2", gen_path)
    assert spec is not None
    assert spec.loader is not None
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    want = gen.render_tex(gen.load(SREMU_ROOT / "docs" / "model_card.yaml"))
    table = PAPER_ROOT / "generated" / "tables" / "table2_model_card.tex"
    assert table.read_text(encoding="utf-8") == want


def test_numbers_placeholders_and_definitions() -> None:
    spec = load_numbers_spec(PAPER_ROOT / "numbers.yaml")
    tex = render_numbers_tex(spec, {})
    assert "\\newcommand{\\numSustainMissRate}{\\ensuremath{10^{-3}}}" in tex
    assert ("\\newcommand{\\numTangCurveMaxDevDb}{\\sremuPlaceholder{numTangCurveMaxDevDb}}"
            in tex)
    n_results = sum(1 for m in spec if m.value is None)
    assert placeholder_count(tex) == n_results > 0
    filled = render_numbers_tex(spec, {"numTangCurveMaxDevDb": "0.014"})
    assert "\\newcommand{\\numTangCurveMaxDevDb}{0.014}" in filled
    assert placeholder_count(filled) == n_results - 1


def test_fixed_value_is_refused_for_result_macros(tmp_path: Path) -> None:
    bad = tmp_path / "numbers.yaml"
    bad.write_text(yaml.safe_dump({"macros": [{
        "macro": "numX", "description": "d", "source": {"type": "study", "id": "V-TANG-1"},
        "value": "0.01"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="fixed value is allowed only"):
        load_numbers_spec(bad)


def _write_results(root: Path, commit: str, values: dict[str, str]) -> None:
    (root / "generated" / "results.json").write_text(
        json.dumps({"sremu_commit": commit, "values": values}), encoding="utf-8")


def test_results_json_fills_macros_only_from_the_pinned_commit(paper_copy: Path) -> None:
    sha = "a" * 40
    _write_results(paper_copy, sha, {"numTangCurveMaxDevDb": "0.014"})
    rep = build(paper_copy, mode="draft", latex=False, sremu_root=SREMU_ROOT,
                git=GitProbe(runner=Recorder()), runner=Recorder())
    assert rep.ok, rep.render()
    tex = (paper_copy / "generated" / "numbers.tex").read_text(encoding="utf-8")
    assert "\\newcommand{\\numTangCurveMaxDevDb}{0.014}%" in tex
    assert any("results.json was produced at sremu " + sha in n for n in rep.notes)  # UNPINNED
    (paper_copy / "sremu.lock").write_text(f"repo: ../sremu\ncommit: {'b' * 40}\n",
                                           encoding="utf-8")
    rec = Recorder(git_head="b" * 40, allow_git=True)
    rep = build(paper_copy, mode="final", latex=False, sremu_root=SREMU_ROOT,
                git=GitProbe(runner=rec), runner=rec)
    assert any("results.json was produced at sremu " + sha in e for e in rep.errors)
    _write_results(paper_copy, "b" * 40, {"numSustainMissRate": "0.5", "numNope": "1"})
    rep = build(paper_copy, mode="draft", latex=False, sremu_root=SREMU_ROOT,
                git=GitProbe(runner=Recorder()), runner=Recorder())
    assert any("overrides definition/data macros ['numSustainMissRate']" in e
               for e in rep.errors)
    assert any("undeclared macros ['numNope']" in e for e in rep.errors)
    (paper_copy / "generated" / "results.json").write_text('{"numX": "1"}', encoding="utf-8")
    rep = build(paper_copy, mode="draft", latex=False, sremu_root=SREMU_ROOT,
                git=GitProbe(runner=Recorder()), runner=Recorder())
    assert any("must be {sremu_commit, values" in e for e in rep.errors)


# --------------------------------------------------------------------------- draft / final
def test_draft_build_never_runs_git(paper_copy: Path) -> None:
    rec = Recorder()
    rep = build(paper_copy, mode="draft", latex=False, sremu_root=SREMU_ROOT,
                git=GitProbe(runner=rec), runner=rec)
    assert rep.ok, rep.render()
    assert not [c for c in rec.calls if c[0] == "git"]
    assert (paper_copy / "generated" / "buildmode.tex").read_text().strip().endswith(
        "\\sremudrafttrue")
    table = (paper_copy / "generated" / "tables" / "table2_model_card.tex").read_text("utf-8")
    assert "\\label{tab:modelcard}" in table


def test_final_refuses_unpinned_lock(paper_copy: Path) -> None:
    rec = Recorder(git_head="a" * 40, allow_git=True)
    rep = build(paper_copy, mode="final", latex=False, sremu_root=SREMU_ROOT,
                git=GitProbe(runner=rec), runner=rec)
    assert not rep.ok
    assert any("not a pinned 40-hex sha" in e for e in rep.errors)
    assert any("final build refused" in e for e in rep.errors)
    assert not (paper_copy / "generated" / "buildmode.tex").exists()


def test_final_refuses_head_mismatch_and_dirty_tree(paper_copy: Path) -> None:
    lock = paper_copy / "sremu.lock"
    lock.write_text(f"repo: ../sremu\ncommit: {'a' * 40}\n", encoding="utf-8")
    rec = Recorder(git_head="b" * 40, clean=False, allow_git=True)
    rep = build(paper_copy, mode="final", latex=False, sremu_root=SREMU_ROOT,
                git=GitProbe(runner=rec), runner=rec)
    assert any("sremu HEAD " + "b" * 40 in e for e in rep.errors)
    assert any("sremu working tree is dirty" in e for e in rep.errors)
    assert any("paper working tree is dirty" in e for e in rep.errors)


def test_final_refuses_placeholders_even_with_green_gates(paper_copy: Path, tmp_path: Path) -> None:
    sha = "c" * 40
    (paper_copy / "sremu.lock").write_text(f"repo: ../sremu\ncommit: {sha}\n", encoding="utf-8")
    junit = _green_junit(tmp_path / "junit.xml", props=pinned(sha))
    rec = Recorder(git_head=sha, clean=True, allow_git=True)
    rep = build(paper_copy, mode="final", latex=False, junit=[junit], sremu_root=SREMU_ROOT,
                git=GitProbe(runner=rec), runner=rec)
    errs = rep.errors
    assert not any(e.startswith("pin:") for e in errs)
    assert not any("not green" in e for e in errs)
    assert any("pipeline numbers are still placeholders" in e for e in errs)
    assert any("section stubs" in e for e in errs)
    assert any("figure F3: pending" in e for e in errs)
    assert any("final build refused" in e for e in errs)
    assert not any("latexmk" in c[0] for c in rec.calls)


# --------------------------------------------------------------------------- gates
def test_gate_lists_match_design_10_13() -> None:
    gates, per_id = load_gates(PAPER_ROOT)
    assert sorted(gates) == ["GATE-PHY", "GATE-RT"]
    phy = [f"PH-{i:02d}" for i in [*range(1, 12), *range(13, 17), *range(23, 33)]]
    assert gates["GATE-PHY"] == [*phy, "ST-02", "ST-03", "G1", "G2", "G3", "G6", "G7", "G8",
                                 "V-TANG-1"]
    assert gates["GATE-RT"] == [*(f"RT-{i:02d}" for i in range(1, 20)),
                                *(f"PH-{i}" for i in range(17, 23)), "ST-01", "G4", "G5",
                                "V-CFO-1", "V-CFO-2"]
    # "PH-17...PH-22 on both backends" (DESIGN 10.13)
    assert per_id == {f"PH-{i}": ["numpy_ref", "numba_cpu"] for i in range(17, 23)}


def test_gate_status_counts(tmp_path: Path) -> None:
    junit = _green_junit(tmp_path / "j.xml")
    st = gate_status(PAPER_ROOT, load_junit([junit]))
    assert all(s == "passed" for g in st.values() for s in g.values())
    assert all(s == "no junit" for g in gate_status(PAPER_ROOT, None).values() for s in g.values())


def test_malformed_gate_file_fails_closed(paper_copy: Path, tmp_path: Path) -> None:
    gates = paper_copy / "gates.yaml"
    old_style = yaml.safe_load(gates.read_text(encoding="utf-8"))["gates"]  # no `gates:` key
    gates.write_text(yaml.safe_dump(old_style), encoding="utf-8")
    rep = Report("t")
    assert not step_gates(paper_copy, [_green_junit(tmp_path / "j.xml")], "draft", rep)
    assert any("must define non-empty GATE-PHY and GATE-RT" in e for e in rep.errors)


def test_gate_rt_needs_both_backends(tmp_path: Path) -> None:
    junit = _green_junit(tmp_path / "j.xml", drop="numba_cpu")
    st = gate_status(PAPER_ROOT, load_junit([junit]))
    assert st["GATE-RT"]["PH-17"] == "missing numba_cpu"
    assert st["GATE-RT"]["PH-22"] == "missing numba_cpu"
    assert st["GATE-RT"]["RT-05"] == "passed"  # the backend rule covers PH-17..22 only
    assert st["GATE-PHY"]["PH-02"] == "passed"


def test_gate_phy_ph_ids_need_numpy_ref_evidence(tmp_path: Path) -> None:
    """DESIGN 10.13: GATE-PHY's PH ids count on numpy_ref; a node parametrized only with
    [numba_cpu] is not evidence, an unparametrized or [numpy_ref] node is."""
    assert load_reference_rule(PAPER_ROOT) == {
        "GATE-PHY": ("PH-", "numpy_ref", ["numba_cpu"])
    }
    junit = _green_junit(tmp_path / "j.xml")
    xml = junit.read_text(encoding="utf-8")
    for old, new in [
        ('name="test_ph_04_case"', 'name="test_ph_04_case[numba_cpu]"'),
        ('name="test_ph_09_case"', 'name="test_ph_09_case[numpy_ref]"'),
        ('name="test_g8_case"', 'name="test_g8_case[numba_cpu]"'),  # PH ids only
    ]:
        assert xml.count(old) == 1
        xml = xml.replace(old, new)
    junit.write_text(xml, encoding="utf-8")
    st = gate_status(PAPER_ROOT, load_junit([junit]))
    assert st["GATE-PHY"]["PH-04"] == "missing numpy_ref"
    assert st["GATE-PHY"]["PH-09"] == "passed"
    assert st["GATE-PHY"]["G8"] == "passed"
    assert st["GATE-PHY"]["PH-02"] == "passed"
    assert all(s == "passed" for s in st["GATE-RT"].values())


@pytest.mark.parametrize(("props", "needle"), [
    (None, "no sremu_commit property"),
    ({"sremu_commit": "d" * 40, "sremu_dirty": "false"}, "from sremu " + "d" * 40),
    ({"sremu_commit": "c" * 40, "sremu_dirty": "true"}, "clean tree required"),
])
def test_final_refuses_junit_not_of_the_pinned_commit(
    paper_copy: Path, tmp_path: Path, props: dict[str, str] | None, needle: str
) -> None:
    sha = "c" * 40
    (paper_copy / "sremu.lock").write_text(f"repo: ../sremu\ncommit: {sha}\n", encoding="utf-8")
    junit = _green_junit(tmp_path / "j.xml", props=props)
    rec = Recorder(git_head=sha, clean=True, allow_git=True)
    rep = build(paper_copy, mode="final", latex=False, junit=[junit], sremu_root=SREMU_ROOT,
                git=GitProbe(runner=rec), runner=rec)
    assert any(needle in e for e in rep.errors), rep.errors
    assert any("final build refused" in e for e in rep.errors)


def test_junit_provenance_property_and_sidecar(tmp_path: Path) -> None:
    sha = "e" * 40
    j = tmp_path / "a.xml"
    j.write_text(junit_xml({"test_x": "passed"}, pinned(sha)), encoding="utf-8")
    assert junit_provenance(j) == {"sremu_commit": sha, "sremu_dirty": "false"}
    assert junit_provenance_errors([j], sha) == []
    k = tmp_path / "b.xml"
    k.write_text(junit_xml({"test_x": "passed"}), encoding="utf-8")
    assert len(junit_provenance_errors([k], sha)) == 2  # no commit, no clean flag
    (tmp_path / "b.meta.json").write_text(
        json.dumps({"sremu_commit": sha, "sremu_dirty": False}), encoding="utf-8")
    assert junit_provenance_errors([k], sha) == []
    assert len(junit_provenance_errors([k], "f" * 40)) == 1
    # a test-case property is not suite provenance
    m = tmp_path / "c.xml"
    m.write_text("<testsuites><testsuite name='p'><testcase classname='t' name='x'>"
                 f"<properties><property name='sremu_commit' value='{sha}'/></properties>"
                 "</testcase></testsuite></testsuites>", encoding="utf-8")
    assert "sremu_commit" not in junit_provenance(m)


def test_final_refuses_foreign_installed_sremu(paper_copy: Path, tmp_path: Path) -> None:
    sha = "c" * 40
    (paper_copy / "sremu.lock").write_text(f"repo: ../sremu\ncommit: {sha}\n", encoding="utf-8")
    other = str(tmp_path / "site-packages" / "sremu" / "__init__.py")
    rec = Recorder(git_head=sha, clean=True, allow_git=True, sremu_file=other)
    rep = build(paper_copy, mode="final", latex=False, sremu_root=SREMU_ROOT,
                git=GitProbe(runner=rec), runner=rec)
    assert any("is not the pinned checkout" in e for e in rep.errors), rep.errors
    real = Recorder(git_head=sha, clean=True, allow_git=True)  # asks this interpreter
    rep = build(paper_copy, mode="final", latex=False, sremu_root=SREMU_ROOT,
                git=GitProbe(runner=real), runner=real)
    assert not any(e.startswith("pin:") for e in rep.errors), rep.errors


# --------------------------------------------------------------------------- registry
def test_registry_is_complete_and_valid() -> None:
    reg = load_registry(PAPER_ROOT)
    rep = Report("t")
    validate_registry(reg, rep)
    assert rep.ok, rep.render()
    assert list(reg) == EXPECTED_FIGURES
    assert {f for f, e in reg.items() if e["source"] == "tikz"} == {"F1", "F2"}


def test_registry_validation_teeth() -> None:
    reg = load_registry(PAPER_ROOT)
    reg = {k: dict(v) for k, v in reg.items() if k != "F12"}
    reg["F3"]["data_classes"] = ["SIM-sremu", "GUESS"]
    reg["F4"]["width"] = "wide"
    rep = Report("t")
    validate_registry(reg, rep)
    for needle in ("exactly F1..F12", "unknown data classes ['GUESS']", "F4: width"):
        assert any(needle in e for e in rep.errors), needle


def test_figure_sources_match_registry() -> None:
    rep = Report("t")
    check_figure_sources(PAPER_ROOT, load_registry(PAPER_ROOT), rep)
    assert rep.ok, rep.render()


def test_figure_sources_teeth(paper_copy: Path) -> None:
    vv = paper_copy / "sections" / "05_verification_validation.tex"
    text = vv.read_text(encoding="utf-8")
    for old, new in (("(SIM-sremu;\nANALYTIC).}{fig:F6}", "(SIM-sremu).}{fig:F6}"),
                     ("\\sremufigure{F6}{\\columnwidth}", "\\sremufigure{F6}{\\textwidth}"),
                     ("{fig:F5}", "{fig:F5x}")):
        assert old in text, old
        text = text.replace(old, new)
    vv.write_text(text, encoding="utf-8")
    append(paper_copy / "sections" / "09_future_work.tex",
           "\n\\sremufigure{F3}{\\textwidth}{Again (DIG).}{fig:F3}\n")
    rep = Report("t")
    check_figure_sources(paper_copy, load_registry(paper_copy), rep)
    rel = "sections/05_verification_validation.tex"
    for needle in (f"F6 ({rel}): caption does not name data class ANALYTIC",
                   f"F6 ({rel}): width \\textwidth, registry says single",
                   "label 'fig:F5x'", "figure F3: placed 2 times"):
        assert any(needle in e for e in rep.errors), (needle, rep.errors)


def test_content_key_tracks_commit_script_and_inputs(tmp_path: Path) -> None:
    (tmp_path / "s.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "in.csv").write_text("a,b\n", encoding="utf-8")
    entry = {"script": "s.py", "sweep": None, "inputs": ["in.csv"]}
    k0 = content_key(tmp_path, entry, "a" * 40)
    assert k0 == content_key(tmp_path, entry, "a" * 40)
    assert k0 != content_key(tmp_path, entry, "b" * 40)
    (tmp_path / "in.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    k1 = content_key(tmp_path, entry, "a" * 40)
    assert k1 != k0
    (tmp_path / "s.py").write_text("print(2)\n", encoding="utf-8")
    assert content_key(tmp_path, entry, "a" * 40) != k1
    assert content_key(tmp_path, {"script": None}, "a" * 40) is None


def test_figure_state_stale_until_meta_matches(tmp_path: Path) -> None:
    (tmp_path / "s.py").write_text("x = 1\n", encoding="utf-8")
    entry = {"source": "script", "script": "s.py", "sweep": None, "inputs": [],
             "data_classes": ["SIM-sremu", "ANALYTIC"]}
    assert figure_state(tmp_path, "F6", entry, "a" * 40) == "stale"
    (tmp_path / "figures" / "out").mkdir(parents=True)
    (tmp_path / "figures" / "data").mkdir(parents=True)
    (tmp_path / "figures" / "out" / "F6.pdf").write_bytes(b"%PDF-1.4\n")
    meta = tmp_path / "figures" / "data" / "F6.meta.json"
    key = content_key(tmp_path, entry, "a" * 40)
    meta.write_text(json.dumps({"content_key": key}))
    # DESIGN 11: every figure meta carries its data classes (and here the commit)
    assert figure_state(tmp_path, "F6", entry, "a" * 40) == "meta-mismatch"
    good = {"content_key": key, "sremu_commit": "a" * 40,
            "data_classes": ["ANALYTIC", "SIM-sremu"]}
    meta.write_text(json.dumps(good))
    assert figure_state(tmp_path, "F6", entry, "a" * 40) == "current"
    meta.write_text(json.dumps({**good, "data_classes": ["SIM-sremu"]}))
    assert figure_state(tmp_path, "F6", entry, "a" * 40) == "meta-mismatch"
    meta.write_text(json.dumps(good))
    assert figure_state(tmp_path, "F6", entry, "b" * 40) == "stale"  # new sremu commit
    assert figure_state(tmp_path, "F6", {"source": "script", "script": None}, "a" * 40) == \
        "pending"
