"""WP12: check_claims.py -- DESIGN 1.6 honesty rules, number provenance and claim evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from check_claims import check_claims, flag_numbers, prepare, split_units
from conftest import PAPER_ROOT, append, junit_xml, pinned
from paper_common import CaseResult, load_junit, spec_id_pattern, spec_id_status

pytestmark = pytest.mark.unit

FUTURE = Path("sections") / "09_future_work.tex"


def _errors_after(paper_copy: Path, text: str) -> list[str]:
    append(paper_copy / FUTURE, "\n" + text + "\n")
    return check_claims(paper_copy, "draft", junit=[]).errors


# --------------------------------------------------------------------------- real paper
def test_real_paper_passes_in_draft_mode() -> None:
    rep = check_claims(PAPER_ROOT, "draft", junit=[])
    assert rep.ok, rep.render()
    assert not any("C-OPEN pending" in n for n in rep.notes)  # H-2 closed 2026-10-15


def test_final_mode_refuses_pending_claims() -> None:
    rep = check_claims(PAPER_ROOT, "final", junit=[])
    assert not rep.ok
    assert any("C-TRACE pending: gate H-4 open" in e for e in rep.errors)
    assert any("'only' mapped to pending claim C-SOFTWARE-ONLY" in e for e in rep.errors)


# --------------------------------------------------------------------------- banned words
@pytest.mark.parametrize(("sentence", "word"), [
    ("This is the first emulator of its kind.", "first"),
    ("It is the ONLY tool.", "only"),
    ("The interface is standard compliant.", "compliant"),
    ("A digital\ntwin of the lab.", "digital twin"),
    ("It accurately replicates the lab.", "accurately replicates"),
    ("With no loss of functionality.", "no loss of functionality"),
])
def test_banned_words_need_a_mapped_claim(paper_copy: Path, sentence: str, word: str) -> None:
    errs = _errors_after(paper_copy, sentence)
    assert any(f"banned word '{word}'" in e for e in errs), errs


def test_banned_word_inside_comment_or_stub_is_ignored(paper_copy: Path) -> None:
    errs = _errors_after(paper_copy, "% the first draft\n\\sremustub{the only plan}")
    assert not any("banned word" in e for e in errs)


def test_mapped_banned_word_is_a_note_in_draft(paper_copy: Path) -> None:
    claims = paper_copy / "claims.yaml"
    doc = yaml.safe_load(claims.read_text(encoding="utf-8"))
    doc["claims"]["C-TEST-ONLY"] = {
        "text": "t", "where": [], "evidence": ["test:RT-05"], "gates": [],
        "banned_word_uses": [{"word": "only", "context": "applies only to lockstep"}]}
    claims.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    append(paper_copy / FUTURE, "\nThe bound applies only to lockstep runs.\n")
    rep = check_claims(paper_copy, "draft", junit=[])
    assert rep.ok, rep.render()
    assert any("mapped to pending claim C-TEST-ONLY" in n for n in rep.notes)
    assert not check_claims(paper_copy, "final", junit=[]).ok


# --------------------------------------------------------------------------- real-time rule
@pytest.mark.parametrize(("sentence", "bad"), [
    ("A real-time emulator.", True),
    ("It runs in real time.", True),
    ("It runs in real~time.", True),
    ("We report the real-time factor (RTF).", False),
    ("It is not a hard-real-time emulator.", False),
    ("Soft real time with measured misses.", False),
])
def test_real_time_never_unqualified(paper_copy: Path, sentence: str, bad: bool) -> None:
    errs = _errors_after(paper_copy, sentence)
    assert any("unqualified 'real-time'" in e for e in errs) is bad


# --------------------------------------------------------------------------- numbers
@pytest.mark.parametrize(("unit", "flagged"), [
    ("The bias is 0.98 dB.", ["0.98"]),
    ("We ran 36 points.", ["36"]),
    ("The bias is 0.98 dB CITEMARK .", []),
    ("The bias is \\numTangMeasBiasDb~dB.", []),
    ("As of 10 Oct 2026 and since Dec 2022, in 2026.", []),
    ("Versions v0.18 and v2.2.0, PH-02, 6G, R2026a, i.i.d. Rayleigh.", []),
    ("ETSI GS RIS 008 and TR 38.867 and TSDSI STD 5003.", []),
    ("The kernel $4\\pi r_1 r_2$ holds.", []),
    ("The pitch is $0.5\\lambda$ here.", ["0.5"]),
    # numbers glued to units or in scientific notation are numbers, not identifiers
    ("The latency is 5.2ms and the loss 10dB.", ["5.2ms", "10dB"]),
    ("A 3x speed-up at 23.04MS/s.", ["3x", "23.04MS/s"]),
    ("Miss rate below 1e-3.", ["1e-3"]),
    ("Over 5G and 6G links in 2D and 3D scenes.", []),
    # math: integers after a relation (>= 2) or before a unit are quantities
    ("With $N = 1024$ elements and $L>64$ links.", ["1024", "64"]),
    ("Front side only, $\\cos\\theta > 0$, and $x = 1$.", []),
    ("A page of $20\\,\\mathrm{ms}$.", ["20"]),
    ("Gain $\\leq -60$ in band.", ["60"]),
    # a citation covers literature numbers, not sremu's own
    ("ACHEM reports 82 us end to end CITEMARK .", []),
    ("\\sremu{} is 3.5 times faster than ACHEM CITEMARK .", ["3.5"]),
    ("Our emulator needs 12 cores, unlike CITEMARK .", ["12"]),
])
def test_number_rule(unit: str, flagged: list[str]) -> None:
    assert flag_numbers(unit) == flagged


def test_number_rule_on_tex_markup() -> None:
    tex = (
        "\\begin{tabular}{@{}L{1.08in}p{0.8in}@{}}\n"
        "A & B~\\cite{X} 64 \\\\\n"
        "\\end{tabular}\n"
        "\\begin{equation}\n\\begin{split}\na={}&4\\pi\\\\\n&\\times 2\n\\end{split}\n"
        "\\label{eq:x}\n\\end{equation}\n"
        "\\fbox{\\parbox{0.94\\columnwidth}{Text.}}\n"
        "\\newcolumntype{L}[1]{>{\\raggedright\\arraybackslash}p{#1}}\n"
    )
    assert [u for u in split_units(prepare(tex)) if flag_numbers(u)] == []
    assert flag_numbers(split_units(prepare("A sentence with 12 items."))[0]) == ["12"]


def test_pipeline_result_phrases_obey_word_rules(paper_copy: Path) -> None:
    """A text macro filled by the pipeline is printed in the text (here the conclusion's V-LAB
    outcome phrase), so its value is checked too (the TeX source only shows \\txtVLabOutcome)."""
    results = paper_copy / "generated" / "results.json"
    results.write_text(json.dumps({"sremu_commit": "a" * 40, "values": {
        "txtVLabOutcome": "is, for the first time, shown in real time",
        "numTangCurveMaxDevDb": "0.014"}}), encoding="utf-8")
    errs = check_claims(paper_copy, "draft", junit=[]).errors
    assert any("numbers:txtVLabOutcome: banned word 'first'" in e for e in errs), errs
    assert any("numbers:txtVLabOutcome: unqualified 'real-time'" in e for e in errs), errs
    assert not any("numTangCurveMaxDevDb" in e for e in errs)  # numbers are pipeline output
    results.write_text(json.dumps({"sremu_commit": "a" * 40, "values": {
        "txtVLabOutcome": "shows a measurable EVM transient"}}), encoding="utf-8")
    assert check_claims(paper_copy, "draft", junit=[]).ok


def test_uncited_number_in_section_fails(paper_copy: Path) -> None:
    errs = _errors_after(paper_copy, "Our emulator is 3.5 times faster.")
    assert any("['3.5'] neither cited nor macros" in e for e in errs)


def test_unknown_claim_marker_fails(paper_copy: Path) -> None:
    errs = _errors_after(paper_copy, "\\claim{C-NOPE}Text.")
    assert any("\\claim{C-NOPE} not in claims.yaml" in e for e in errs)


# --------------------------------------------------------------------------- junit and evidence
def test_spec_id_matching_in_node_ids(tmp_path: Path) -> None:
    j = tmp_path / "j.xml"
    j.write_text(junit_xml({
        "test_ph02_point_equivalence": "passed",
        "test_ph20_fd_rows[numba_cpu]": "failed",
        "test_ph20_fd_rows[numpy_ref]": "passed",
        "test_rt05_switch_offset": "skipped",
        "test_log1p_helper": "passed",
        "test_v_tang_1_curves": "passed",
    }), encoding="utf-8")
    cases = load_junit([j])
    assert spec_id_status("PH-02", cases) == "passed"
    assert spec_id_status("PH-20", cases) == "failed"  # one backend failed
    assert spec_id_status("RT-05", cases) == "skipped"
    assert spec_id_status("PH-03", cases) == "missing"
    assert spec_id_status("G1", cases) == "missing"  # 'log1p' is not G1
    assert spec_id_status("V-TANG-1", cases) == "passed"
    assert not spec_id_pattern("PH-02").search("tests.t::test_ph20_x")
    assert spec_id_status("PH-2", [CaseResult("t::test_ph02_a", "passed")]) == "passed"
    # an alphabetic last part needs a boundary: E-B is not the 'eb' of 'ebn0'
    assert spec_id_status("E-B", [CaseResult("t::test_bpsk_ebn0_curve", "passed")]) == "missing"
    assert spec_id_status("V-LAB", [CaseResult("t::test_v_labels", "passed")]) == "missing"
    assert spec_id_status("E-B", [CaseResult("t::test_e_b_switch", "passed")]) == "passed"


def test_oracle_and_golden_harness_tests_are_not_evidence() -> None:
    """Node ids as pytest writes them for the sremu repo (seen in a real CI junit)."""
    oracle_only = [
        CaseResult("tests.oracles.test_oracle_tables::test_ph11_knife_edge", "passed"),
        CaseResult("tests.oracles.test_oracle_tables::test_rt05_switch_offset_at_2km", "passed"),
    ]
    assert spec_id_status("PH-11", oracle_only) == "missing"
    assert spec_id_status("RT-05", oracle_only) == "missing"
    harness = [
        CaseResult("tests.golden.test_goldens::test_golden_integrity[g8_two_ray]", "passed"),
        CaseResult("tests.golden.test_goldens::test_g8_oracle_two_ray_matches_two_ray_channel",
                   "passed"),
        CaseResult("tests.golden.test_goldens::test_g6_matlab_sos_statistics_meet_st02",
                   "passed"),
    ]
    assert spec_id_status("G8", harness) == "missing"
    assert spec_id_status("ST-02", harness) == "missing"
    xfailed = [*harness, CaseResult(
        "tests.golden.test_goldens::test_g8_sremu_two_ray_with_pec_like_ground", "skipped")]
    assert spec_id_status("G8", xfailed) == "skipped"
    sremu_side = [*harness, CaseResult(
        "tests.golden.test_goldens::test_g8_sremu_two_ray_with_pec_like_ground", "passed")]
    assert spec_id_status("G8", sremu_side) == "passed"
    # pytest run from inside tests/ writes classnames without the 'tests.' prefix
    assert spec_id_status("PH-11", [CaseResult("oracles.test_t::test_ph11_x", "passed")]) == \
        "missing"
    # a module that merely has 'oracle' in its name is still an implementation test
    assert spec_id_status("PH-11", [CaseResult("tests.physics.test_ph11_vs_oracle::test_ph11_x",
                                               "passed")]) == "passed"
    # one xfailed (open issue) next to passing cases is not green
    mixed = [CaseResult("tests.physics.test_ph23_energy::test_ph23_default", "passed"),
             CaseResult("tests.physics.test_ph23_energy::test_ph23_kirchhoff", "skipped")]
    assert spec_id_status("PH-23", mixed) == "skipped"


def test_spec_id_status_backend_rule() -> None:
    both = ("numpy_ref", "numba_cpu")
    one = [CaseResult("t::test_ph17_doppler[numpy_ref]", "passed")]
    assert spec_id_status("PH-17", one) == "passed"
    assert spec_id_status("PH-17", one, both) == "missing numba_cpu"
    two = [*one, CaseResult("t::test_ph17_doppler[numba_cpu]", "skipped")]
    assert spec_id_status("PH-17", two, both) == "skipped"  # a skipped backend is not green
    three = [*one, CaseResult("t::test_ph17_doppler[numba_cpu]", "passed")]
    assert spec_id_status("PH-17", three, both) == "passed"


def test_final_mode_needs_junit_of_the_pinned_commit(paper_copy: Path, tmp_path: Path) -> None:
    j = tmp_path / "j.xml"
    j.write_text(junit_xml({"test_rt05_switch": "passed"}), encoding="utf-8")
    errs = check_claims(paper_copy, "final", junit=[j]).errors
    assert any("no sremu_commit property" in e for e in errs)
    (paper_copy / "sremu.lock").write_text(f"repo: ../sremu\ncommit: {'a' * 40}\n",
                                           encoding="utf-8")
    j.write_text(junit_xml({"test_rt05_switch": "passed"}, pinned("a" * 40)), encoding="utf-8")
    errs = check_claims(paper_copy, "final", junit=[j]).errors
    assert not any("junit j.xml" in e for e in errs)
    # draft mode does not ask for provenance
    j.write_text(junit_xml({"test_rt05_switch": "passed"}), encoding="utf-8")
    assert not any("junit j.xml" in e for e in check_claims(paper_copy, "draft", junit=[j]).errors)


def test_claim_becomes_supported_with_green_evidence(paper_copy: Path, tmp_path: Path) -> None:
    j = tmp_path / "j.xml"
    ids = {"test_rt03_chunks": "passed", "test_rt08_barriers": "passed",
           "test_rt16_replay": "passed", "test_io02_two_process": "passed"}
    j.write_text(junit_xml(ids), encoding="utf-8")
    rep = check_claims(paper_copy, "draft", junit=[j])
    assert not any(n.startswith("C-LOCKSTEP-EXACT pending") for n in rep.notes)
    ids["test_rt08_barriers"] = "failed"
    j.write_text(junit_xml(ids), encoding="utf-8")
    rep = check_claims(paper_copy, "draft", junit=[j])
    assert any("C-LOCKSTEP-EXACT pending: test RT-08 failed" in n for n in rep.notes)


def test_gate_closure_supports_gate_only_claim(paper_copy: Path) -> None:
    gates = paper_copy / "human_gates.yaml"
    doc = yaml.safe_load(gates.read_text(encoding="utf-8"))
    doc["gates"]["H-4"]["status"] = "closed"
    gates.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    rep = check_claims(paper_copy, "draft", junit=[])
    assert not any(n.startswith("C-TRACE pending") for n in rep.notes)


def test_bad_evidence_is_an_error_in_any_mode(paper_copy: Path) -> None:
    claims = paper_copy / "claims.yaml"
    doc = yaml.safe_load(claims.read_text(encoding="utf-8"))
    doc["claims"]["C-BAD"] = {"text": "t", "where": ["sections/nope.tex"],
                              "evidence": ["test:PH-2", "fig:F13", "cite:NoKey", "table:tab:x",
                                           "proof:y"], "gates": ["H-9"]}
    claims.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    errs = check_claims(paper_copy, "draft", junit=[]).errors
    for needle in ("where-file sections/nope.tex", "'PH-2' is not a spec test id",
                   "figure F13 not in", "cite key NoKey", "table label tab:x",
                   "malformed evidence 'proof:y'", "unknown human gate H-9"):
        assert any(needle in e for e in errs), needle


# --------------------------------------------------------------------------- pytest evidence
def test_pytest_reference_status() -> None:
    from check_claims import pytest_status

    cases = [
        CaseResult("tests.unit.test_wp9_env::test_env_api_spaces_and_types", "passed"),
        CaseResult("tests.unit.test_wp9_env::test_env_codebook[a]", "passed"),
        CaseResult("tests.unit.test_wp9_env::test_env_codebook[b]", "skipped"),
        CaseResult("tests.unit.test_wp9_env_extra::test_other", "failed"),
        CaseResult("tests.io.test_x.TestGroup::test_in_class", "passed"),
    ]
    assert pytest_status("tests/unit/test_wp9_env.py", cases) == "skipped"
    assert pytest_status("tests/unit/test_wp9_env.py::test_env_api_spaces_and_types",
                         cases) == "passed"
    assert pytest_status("tests/unit/test_wp9_env.py::test_env_codebook", cases) == "skipped"
    assert pytest_status("tests/unit/test_wp9_env.py::test_env_api", cases) == "missing"
    assert pytest_status("tests/unit/test_wp9_env_extra.py", cases) == "failed"
    assert pytest_status("tests/io/test_x.py::test_in_class", cases) == "passed"
    assert pytest_status("tests/unit/test_nope.py", cases) == "missing"


def test_pytest_evidence_is_checked(paper_copy: Path, tmp_path: Path) -> None:
    claims = paper_copy / "claims.yaml"
    doc = yaml.safe_load(claims.read_text(encoding="utf-8"))
    doc["claims"]["C-PYT"] = {"text": "t", "where": [], "gates": [],
                              "evidence": ["pytest:tests/unit/test_wp9_env.py"]}
    doc["claims"]["C-PYT-BAD"] = {"text": "t", "where": [], "gates": [],
                                  "evidence": ["pytest:unit/test_wp9_env", "pytest:tests/x/y.py"]}
    claims.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    j = tmp_path / "j.xml"
    j.write_text(junit_xml({"test_env_ok": "passed"}).replace(
        'classname="tests.t"', 'classname="tests.unit.test_wp9_env"'), encoding="utf-8")
    rep = check_claims(paper_copy, "draft", junit=[j])
    assert not any(n.startswith("C-PYT pending") for n in rep.notes), rep.render()
    assert any("'unit/test_wp9_env' is not tests/<path>.py" in e for e in rep.errors)
    sremu_dir = (paper_copy.parent / "sremu")
    if sremu_dir.is_dir():  # only when a checkout sits next to the copy
        assert any("tests/x/y.py does not exist" in e for e in rep.errors)
    rep = check_claims(paper_copy, "draft", junit=[])
    assert any("C-PYT pending: pytest tests/unit/test_wp9_env.py no junit" in n
               for n in rep.notes)


def test_code_identifiers_in_texttt_are_not_prose(paper_copy: Path) -> None:
    errs = _errors_after(paper_copy, "The field \\texttt{first\\_sample} is set.")
    assert not any("banned word" in e for e in errs), errs
    errs = _errors_after(paper_copy, "The first sample is set.")
    assert any("banned word 'first'" in e for e in errs)
    # numbers inside \texttt are still numbers
    errs = _errors_after(paper_copy, "Port \\texttt{5600} is used.")
    assert any("['5600'] neither cited nor macros" in e for e in errs), errs
