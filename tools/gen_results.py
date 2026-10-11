"""gen_results.py -- pipeline values of the result macros and the result tables (WP12;
DESIGN 1.6 rule 7, 13.5 steps 4-5).

Reads ONLY committed result files and the paper's figure data, never typed numbers:

* the pinned test record ``generated/junit/*.xml`` (Table IV, oracle counts);
* sremu ``validation/{tang,cfo,fm,ants_fig4b}/results/<run>`` and the V-LAB protocol;
* ``sweeps/results/{E-A,E-A-IQ,E-B1,E-B2,E-C,E-F}`` (WP11 results) and
  ``sweeps/results/E-B-arc`` (E-B on the lab arc, AWS run);
* ``figures/data/F3.meta.json`` and ``F6.meta.json`` (values computed by those figure
  scripts with the installed sremu).

Writes ``generated/results.json`` = ``{"sremu_commit", "values": {macro: LaTeX}, "provenance"}``
(read by build_paper.py into generated/numbers.tex) and ``generated/tables/
table4_vv.tex``, ``table5_lab.tex``, ``table8_ablation.tex``. Every value fits the macro's
``max_chars`` budget (text macros are checked against it here).

Rounding: decimals are rounded half-even on the exact decimal value of the source
(``Decimal(repr(x))``); the Tang bias is the exact mean of the two-decimal digitized values
(197/200 = 0.985 -> 0.98); the Tang curve deviation is a bound (max rounded UP to 0.01 dB).
Section VI values and Table VII come from ``sweeps/results/bench`` through
``tools/bench_results.py``. Macros of PENDING results (F5 native-ZMQ rerun, E-F latency) and of
the V-LAB lock and deposit (human gate H-6) are deliberately absent: they stay placeholders.

Usage: python tools/gen_results.py [--paper-root DIR] [--check]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_common import (
    PAPER_ROOT,
    is_implementation_test,
    load_junit,
    load_numbers_spec,
    read_yaml,
    spec_id_pattern,
    spec_id_status,
)

TANG_RUN = "validation/tang/results/20261010T232551Z_vtang"
CFO_RUN = "validation/cfo/results/20261010T232549Z_vcfo"
FM_RUN = "validation/fm/results/20261010T232549Z_vfm"
LAB_RUN = "validation/ants_fig4b/results/20261011T003044Z_vlab_unblinded"
LAB_PROTOCOL = "validation/ants_fig4b/protocol.json"
C0 = 299_792_458.0


# --------------------------------------------------------------------------- formatting
def dec(x: float | str | Decimal, nd: int, *, sign: bool = False, mode: str = ROUND_HALF_EVEN,
        absval: bool = False) -> str:
    """Half-even rounding of the exact decimal value of ``x`` to ``nd`` places, as LaTeX."""
    d = x if isinstance(x, Decimal) else Decimal(repr(float(x)) if isinstance(x, float) else x)
    if absval:
        d = abs(d)
    q = Decimal(1).scaleb(-nd)
    r = d.quantize(q, rounding=mode)
    if r == 0:
        r = abs(r)
    s = f"{r:f}"
    if s.startswith("-"):
        return "\\ensuremath{-" + s[1:] + "}"
    return ("\\ensuremath{+" + s + "}") if sign else s


def integer(n: int) -> str:
    s = f"{int(n):d}"
    if len(s) >= 5:
        groups = []
        while s:
            groups.insert(0, s[-3:])
            s = s[:-3]
        s = "\\,".join(groups)
    return s


def sci(x: float, sig: int = 2) -> str:
    """a x 10^b with ``sig`` significant digits (LaTeX math)."""
    if x == 0:
        return "0"
    e = math.floor(math.log10(abs(x)))
    m = Decimal(repr(abs(x) / 10**e)).quantize(Decimal(1).scaleb(-(sig - 1)),
                                               rounding=ROUND_HALF_EVEN)
    if m >= 10:
        m, e = m / 10, e + 1
    neg = "-" if x < 0 else ""
    return f"\\ensuremath{{{neg}{m:f}\\times10^{{{e}}}}}"


def pct(x: float, nd: int = 0) -> str:
    return dec(100.0 * x, nd)


def prose_count(n: int) -> str:
    """A count in running text (IEEE style): zero to nine spelled out, numerals above."""
    words = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
    return words[n] if 0 <= n <= 9 else integer(n)


# --------------------------------------------------------------------------- inputs
def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def git_head(repo: Path) -> str | None:
    """HEAD of ``repo`` from its .git files (no git process)."""
    try:
        head = (repo / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head
    ref = repo / ".git" / head.split(":", 1)[1].strip()
    return ref.read_text(encoding="utf-8").strip() if ref.is_file() else None


# --------------------------------------------------------------------------- junit (Table IV)
FAMILIES: list[tuple[str, str, list[str]]] = [
    ("PH", "Physics closed forms and exact properties: Friis, area-tied kernel equals "
           "published forms, aperture refinement, image theory, focusing, ground, knife edge, "
           "alphabets and optimum, Doppler, interpolation, fractional delay, noise, linearity",
     [f"PH-{i:02d}" for i in range(1, 33) if i != 12]),
    ("RT", "Runtime: exact counts at several rates, round trips, bitwise invariance, switch "
           "samples, late commands, ties, barriers, oscillator continuity, transitions, replay, "
           "paced counters, backends", [f"RT-{i:02d}" for i in range(1, 20)]),
    ("IO", "Interfaces: malformed frames and messages, two-process bitwise equality, socket "
           "commands, caps and loopback, SigMF, status codes, wrappers, raw mode",
     [f"IO-{i:02d}" for i in range(1, 9)]),
    ("ST", "Statistical: random-waypoint law, diffuse autocorrelation and level crossings, "
           "quantization and failure Monte Carlo, BER vs theory, CFO vs CRB",
     [f"ST-{i:02d}" for i in range(1, 6)]),
    ("G", "MATLAB R2026a code-to-code: per-element kernel, beam patterns, CFO, thermal "
          "noise, fading statistics, ETSI far field, two-ray channel",
     [f"G{i}" for i in range(1, 9)]),
]


def junit_summary(paper_root: Path) -> dict[str, Any]:
    paths = sorted((paper_root / "generated" / "junit").glob("*.xml"))
    if not paths:
        raise SystemExit("gen_results: no junit in generated/junit (run the paper-junit task)")
    cases = load_junit(paths)
    gates = read_yaml(paper_root / "gates.yaml") or {}
    rule = gates.get("backend_rule") or {}
    per_id = {str(i): [str(b) for b in rule.get("backends") or []] for i in rule.get("ids") or []}
    fams = []
    for fam, desc, ids in FAMILIES:
        st = {i: spec_id_status(i, cases, per_id.get(i, ())) for i in ids}
        n_tests = sum(1 for c in cases if is_implementation_test(c.nodeid)
                      and any(spec_id_pattern(i).search(c.nodeid) for i in ids))
        fams.append({"family": fam, "desc": desc, "ids": ids, "status": st,
                     "passed": sum(1 for v in st.values() if v == "passed"), "tests": n_tests})
    all_ids = [i for _, _, ids in FAMILIES for i in ids] + ["PH-12"]
    skipped = [c for c in cases if c.status == "skipped"]
    skipped_with_id = [c.nodeid for c in skipped
                       if any(spec_id_pattern(i).search(c.nodeid) for i in all_ids)]
    meta = {}
    for p in paths:
        side = p.with_suffix(".meta.json")
        if side.is_file():
            meta[p.name] = read_json(side)
    return {"families": fams, "ph12_status": spec_id_status("PH-12", cases),
            "n_passed": sum(1 for c in cases if c.status == "passed"),
            "n_failed": sum(1 for c in cases if c.status == "failed"),
            "n_skipped": len(skipped), "skipped_with_id": skipped_with_id,
            "files": [p.name for p in paths], "meta": meta}


def _id_ranges(ids: list[str], status: dict[str, str]) -> str:
    """Compact id list ('PH-01--PH-11, PH-13--PH-32'), passed ids only, with failures named."""
    def num(i: str) -> int:
        return int("".join(ch for ch in i.split("-")[-1] if ch.isdigit()))

    ok = [i for i in ids if status[i] == "passed"]
    out, run = [], []
    for i in ok:
        if run and num(i) == num(run[-1]) + 1:
            run.append(i)
        else:
            if run:
                out.append(run)
            run = [i]
    if run:
        out.append(run)
    parts = [r[0] if len(r) == 1 else f"{r[0]}--{r[-1]}" for r in out]
    bad = [f"{i} ({status[i]})" for i in ids if status[i] != "passed"]
    return ", ".join(parts + bad)


def render_table4(js: dict[str, Any]) -> str:
    commit = next((m.get("sremu_commit") for m in js["meta"].values()), "unknown")
    lines = [
        "% GENERATED by paper/tools/gen_results.py from the test record in generated/junit/",
        "% (sidecar meta: sremu commit " + str(commit) + "). Do not edit.",
        r"\begin{table*}[t]",
        r"\caption{Verification summary from the test record of the emulator commit used for"
        r" this paper. An identifier passes when at least one implementation test that carries"
        r" it passed and none failed or was skipped; rendering-path identifiers count once per"
        r" backend. Tolerances and oracles of every identifier are fixed in the specification;"
        r" Appendix~\ref{app:oracles} describes the oracle families. PH-12, a regression value"
        r" of the experimental P.526 module that lies outside every gate, is not counted; "
        # "missing" is the expected status: the PH-12 tests (sremu tests/experimental, marker
        # `experimental`) are deselected by the paper-junit markers (CI + nightly, D21);
        # tests/test_gen_results_text.py checks that this sentence still holds in sremu.
        + {"passed": "its implementation tests passed.",
           "missing": "its implementation tests run in the experimental test tier, outside"
                      " the continuous-integration and nightly runs that this record covers."
           }.get(js["ph12_status"], f"its status is {js['ph12_status']}.") + "}",
        r"\label{tab:vv}",
        r"\centering",
        r"\footnotesize",
        r"\setlength{\tabcolsep}{3pt}",
        r"\renewcommand{\arraystretch}{1.12}",
        r"\begin{tabular}{@{}>{\raggedright\arraybackslash}p{0.35in}"
        r">{\raggedright\arraybackslash}p{3.65in}>{\raggedright\arraybackslash}p{1.95in}"
        r">{\raggedleft\arraybackslash}p{0.45in}>{\raggedleft\arraybackslash}p{0.4in}@{}}",
        r"\toprule",
        r"Tier & What is compared & Identifiers passed & Passed & Tests \\",
        r"\midrule",
    ]
    for f in js["families"]:
        lines.append(f"{f['family']} & {f['desc']} & {_id_ranges(f['ids'], f['status'])} & "
                     f"{f['passed']}/{len(f['ids'])} & {f['tests']} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table*}"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- tables V, VIII
def render_table5(tab: dict[str, Any], proto: dict[str, Any]) -> str:
    names = {"M0": "M0 (line of sight)", "M0n": "M0n (log-distance)", "M1": "M1 (mirror)",
             "M2": "M2 (lab configuration)", "M3": "M3 (M2 + floor)"}
    lines = [
        "% GENERATED by paper/tools/gen_results.py from sremu " + LAB_RUN + "/table_v.json.",
        "% Do not edit.",
        r"\begin{table}[t]",
        r"\caption{Laboratory consistency check: test-ring metrics over the prior draws of the"
        r" pre-registered protocol (median, with 5--95\,\% range for the RMSE). Error ="
        r" measured $-$ predicted. Measured: DIG; predictions: SIM-sremu; geometry and"
        r" $|\Gamma|$: ASSUMED (prior).}",
        r"\label{tab:lab}",
        r"\centering",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{2.5pt}",
        r"\begin{tabular}{@{}lrrrr@{}}",
        r"\toprule",
        r"Model & RMSE (dB) & Bias (dB) & MAE (dB) & max$|e|$ (dB) \\",
        r"\midrule",
    ]
    for r in tab["rows"]:
        rmse = (f"{dec(r['rmse_median_db'], 2)} ({dec(r['rmse_q05_db'], 2)}--"
                f"{dec(r['rmse_q95_db'], 2)})")
        lines.append(f"{names[r['model']]} & {rmse} & {dec(r['bias_median_db'], 2)} & "
                     f"{dec(r['mae_median_db'], 2)} & {dec(r['max_abs_median_db'], 1)} \\\\")
    d = tab["decisions"]
    word = {True: "holds", False: "fails"}
    lines += [
        r"\midrule",
        r"\multicolumn{5}{@{}p{3.3in}@{}}{Decisions: C1 " + word[bool(d["C1"]["holds"])]
        + "; C2 " + word[bool(d["C2"]["holds"])] + "; C3 " + word[bool(d["C3"]["holds"])]
        + r"; decoy C2 detection rate " + dec(tab["power_analysis"]["c2_detection_rate"], 2)
        + r" (underpowered).} \\",
        r"\bottomrule", r"\end{tabular}", r"\end{table}",
    ]
    _ = proto
    return "\n".join(lines) + "\n"


EC_ROWS = (
    ("ideal_1bit", "ideal 1-bit"), ("ideal_2bit", "ideal 2-bit"),
    ("lab_mag1", r"lab states, $|\Gamma_2|$ = 1"), ("lab_mag2_0p7", r"lab, $|\Gamma_2|$ = 0.7"),
    ("lab_mag2_0p5", r"lab, $|\Gamma_2|$ = 0.5"), ("tang_ris1", "Tang RIS1"),
    ("abeywickrama_b2", r"phase-dep.\ amplitude"), ("lab_dead10", r"lab, 10\,\% dead"),
    ("lab_stuck10", r"lab, 10\,\% stuck"), ("lab_eff0p5", "lab, efficiency 0.5"),
    ("ideal_designed_on_lab", "ideal design, lab states"),
)


def render_table8(tab: dict[str, dict[str, str]]) -> str:
    lines = [
        "% GENERATED by paper/tools/gen_results.py from paper/sweeps/results/E-C/table8.csv.",
        "% Do not edit.",
        r"\begin{table}[t]",
        r"\caption{Non-ideality ablation (E-C): loss of the optimized RIS term relative to ideal"
        r" continuous phase, in dB, per geometry, and the large-$N$ closed-form loss $\eta$."
        r" SIM-sremu; ideal alphabets ANALYTIC; lab amplitudes ASSUMED; Tang states REPORTED.}",
        r"\label{tab:ablation}",
        r"\centering",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{2pt}",
        r"\begin{tabular}{@{}lrrrrr@{}}",
        r"\toprule",
        r"Alphabet or defect & Lab-like & Synth.\ 5\,m & Synth.\ 500\,m & Corr. & $\eta$ \\",
        r"\midrule",
    ]
    for key, lab in EC_ROWS:
        r = tab[key]
        eta = dec(r["eta_large_n_db"], 2) if r["eta_large_n_db"] else "---"
        cells = [dec(r[f"loss_ris_db_{g}"], 2) for g in ("lab", "synth_near", "synth_far",
                                                          "corridor")]
        lines.append(f"{lab} & " + " & ".join(cells) + f" & {eta} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- values
def compute(paper_root: Path, sremu_root: Path) -> tuple[dict[str, str], dict[str, Any],
                                                         dict[str, str]]:
    v: dict[str, str] = {}
    prov: dict[str, Any] = {}
    tables: dict[str, str] = {}
    sw = paper_root / "sweeps" / "results"

    # ---------------------------------------------------------------- V-A (junit)
    js = junit_summary(paper_root)
    fam = {f["family"]: f for f in js["families"]}
    analytic = ("PH", "RT", "IO")
    v["numOracleCount"] = str(sum(fam[f]["passed"] for f in analytic))
    v["numOracleIdsTotal"] = str(sum(len(fam[f]["ids"]) for f in analytic))
    if v["numOracleIdsTotal"] != v["numOracleCount"]:
        raise SystemExit("gen_results: Section V says every analytic identifier passes; "
                         f"{v['numOracleCount']}/{v['numOracleIdsTotal']} do")
    v["numOracleTests"] = integer(sum(fam[f]["tests"] for f in analytic))
    v["numStatIds"] = str(fam["ST"]["passed"])
    v["numGoldenIds"] = str(fam["G"]["passed"])
    v["numJunitPassed"] = integer(js["n_passed"])
    v["numJunitSkipped"] = str(js["n_skipped"])
    if js["skipped_with_id"] or js["n_failed"]:
        raise SystemExit(f"gen_results: the Section V text assumes no failed test and no "
                         f"skipped test with a spec id: {js['skipped_with_id']}")
    prov["junit"] = {"files": js["files"], "meta": js["meta"]}
    tables["table4_vv.tex"] = render_table4(js)

    # ---------------------------------------------------------------- V-C (F3, Tang)
    f3 = read_json(paper_root / "figures" / "data" / "F3.meta.json")["panel_a_values"]
    n = [int(x) for x in f3["N"]]
    v["numRefineNmin"] = integer(min(n))
    v["numRefineNmax"] = integer(max(n))
    v["numRefineFarMaxDevDb"] = dec(max(abs(x) for x in f3["far_auto"]), 3, mode=ROUND_CEILING)
    v["numRefineNearMaxDevDb"] = dec(max(abs(x) for x in f3["near_po_patch"]), 2,
                                     mode=ROUND_CEILING)
    v["numRefinePointCoarseDb"] = dec(f3["near_point"][0], 0)
    first = next(i for i, p in enumerate(f3["pitch_over_lambda"]) if p <= 0.5)
    v["numRefinePointFirstAutoN"] = integer(n[first])
    v["numRefinePointFirstAutoDb"] = dec(f3["near_point"][first], 1)
    prov["F3"] = "figures/data/F3.meta.json"

    tang = read_csv(sremu_root / TANG_RUN / "vtang_points.csv")
    summ = read_json(sremu_root / TANG_RUN / "vtang_summary.json")
    mx = max(abs(float(r["engine_minus_published_db"])) for r in tang)
    v["numTangCurveMaxDevDb"] = dec(mx, 2, mode=ROUND_CEILING)  # a bound: rounded up
    diffs = [Decimal(r["measured_dbm"]) - Decimal(r["published_eq9_dbm"]) for r in tang]
    bias = sum(diffs) / Decimal(len(diffs))  # exact mean of two-decimal values
    v["numTangMeasBiasDb"] = dec(bias, 2, sign=True)
    v["numTangMeasSdDb"] = dec(summ["vtang2_measured_minus_published"]["sd"], 2)
    v["numTangMeasN"] = str(len(tang))
    prov["V-TANG"] = {"run": TANG_RUN, **{k: read_json(sremu_root / TANG_RUN / "meta.json")[k]
                                          for k in ("sremu_commit", "sremu_dirty")},
                      "bias_exact": str(bias)}

    # ---------------------------------------------------------------- V-D (lab)
    lab = sremu_root / LAB_RUN
    tab = read_json(lab / "table_v.json")
    res = read_json(lab / "results.json")
    proto = read_json(sremu_root / LAB_PROTOCOL)
    f4 = read_csv(lab / "F4.csv")
    rows = {r["model"]: r for r in tab["rows"]}
    dr = proto["decision_rules"]
    pa = tab["power_analysis"]
    prim = res["primary"]
    v["numLabCells"] = str(len(f4))
    v["numLabTrainCells"] = str(prim["n_train"])
    v["numLabTestCells"] = str(prim["n_test"])
    sig = [float(r["sigma_dig_db"]) for r in f4]
    v["numLabSigDigMinDb"] = dec(min(sig), 3)
    v["numLabSigDigMaxDb"] = dec(max(sig), 2)
    v["numLabDraws"] = integer(proto["primary_analysis"]["K"])
    v["numLabBootstrap"] = integer(proto["bootstrap"]["n_resamples_primary"])
    v["numLabDecoys"] = str(pa["n_decoys"])
    v["numLabDecoyDetections"] = str(round(pa["c2_detection_rate"] * pa["n_decoys"]))
    v["numLabDecoyCiHighPct"] = pct(pa["c2_detection_ci95"][1], 1)
    # the protocol's paper wording reports the decoy rates of C1 and C3 with the
    # underpowered statement (primary sigma_rep of the power analysis)
    rates = pa["rates"][f"M2/sigma_rep_{float(proto['power_analysis']['sigma_rep_db']):g}"]
    if rates["C2"] != pa["c2_detection_rate"]:
        raise SystemExit("gen_results: decoy C2 rate differs between rates and the summary")
    v["numLabDecoyConeDetections"] = str(round(rates["C1"] * pa["n_decoys"]))
    v["numLabDecoyCthreeDetections"] = str(round(rates["C3"] * pa["n_decoys"]))
    v["numLabUnderpoweredRate"] = dec(proto["power_analysis"]["underpowered_if_c2_rate_below"], 1)
    v["numLabConeBiasMaxDb"] = dec(dr["C1"]["median_abs_bias_max_db"], 1)
    v["numLabConeMarginDb"] = dec(dr["C1"]["rmse_margin_vs_m0n_db"], 1)
    v["numLabCtwoMinDb"] = dec(dr["C2"]["median_delta_rmse_min_db"], 1)
    v["numLabCtwoCiFracPct"] = pct(dr["C2"]["ci_excludes_zero_min_fraction"], 0)
    v["numLabCtwoRhoMin"] = dec(dr["C2"]["spearman_rho_min"], 1)
    v["numLabPriorPosCm"] = dec(100 * proto["prior"]["point_error"]["xy_half_width_m"], 0)
    q = proto["primary_analysis"]["report_quantiles"]
    v["numQuantLoPct"] = pct(min(q), 0)
    v["numQuantHiPct"] = pct(max(q), 0)
    v["numLabRmseMzero"] = dec(rows["M0"]["rmse_median_db"], 2)
    v["numLabRmseMzeroN"] = dec(rows["M0n"]["rmse_median_db"], 2)
    v["numLabRmseMtwo"] = dec(rows["M2"]["rmse_median_db"], 2)
    v["numLabRmseMtwoLo"] = dec(rows["M2"]["rmse_q05_db"], 2)
    v["numLabRmseMtwoHi"] = dec(rows["M2"]["rmse_q95_db"], 2)
    v["numLabBiasMtwo"] = dec(rows["M2"]["bias_median_db"], 2)
    d = tab["decisions"]
    v["numLabDeltaRmse"] = dec(d["C2"]["median_delta_rmse_db"], 2)
    v["numLabCiExcludesPct"] = pct(d["C2"]["frac_ci_excludes_zero"], 0)
    v["numLabSpearman"] = dec(d["C2"]["spearman_rho"], 2)
    v["numLabDeltaRmseMone"] = dec(d["C3"]["median_delta_rmse_db"], 2)
    v["numLabCthreeCiExcludesPct"] = pct(d["C3"]["frac_ci_excludes_zero"], 1)
    v["numLabLambdaMm"] = dec(1e3 * C0 / float(proto["prior"]["fc_hz"]), 0)
    resid = [float(r["residual_m2_db"]) for r in f4]
    v["numLabResidualOutlierDb"] = dec(min(resid, key=lambda x: -abs(x)), 1)
    diag = proto["diagnostic_not_part_of_protocol"]
    facts = diag["facts"]
    single = [f for f in facts.values() if f.get("single_fact")]
    if any(f["M2"]["C2_rate"] > 0 for f in single):
        raise SystemExit("gen_results: Section V assumes no single fact raises C2 above 0")
    best = facts["tiles_known_points_pm1mm"]["M2"]
    v["numLabDiagRate"] = dec(best["C2_rate"], 2)
    v["numLabDiagRateLo"] = dec(best["C2_ci95"][0], 2)
    v["numLabDiagRateHi"] = dec(best["C2_ci95"][1], 2)
    v["numLabDiagDecoys"] = str(best["n_decoys"])
    v["numLabDiagGeomRate"] = dec(facts["geometry_known"]["M2"]["C2_rate"], 2)
    v["numLabDiagPosMm"] = "1"  # the fact's tolerance, from its id points_pm1mm (checked below)
    if "+-1 mm" not in facts["tiles_known_points_pm1mm"]["question"]:
        raise SystemExit("gen_results: diagnostic fact tolerance changed")
    v["numLabDiagPosFiveMm"] = "5"
    if "+-5 mm" not in facts["tiles_known_points_pm5mm"]["question"]:
        raise SystemExit("gen_results: diagnostic fact tolerance changed")
    v["numLabDiagSingleFacts"] = str(len(single))
    meta = read_json(lab / "meta.json")
    v["txtVLabOutcome"] = ("was underpowered and did not separate the RIS model from "
                           "LOS-only") if (pa["underpowered"] and not d["C2"]["holds"]) else None
    prov["V-LAB"] = {"run": LAB_RUN, "git_commit": meta["git_commit"],
                     "git_dirty": meta["git_dirty"], "decisions": meta["decisions"],
                     "protocol_digest": meta["protocol_digest"]}
    tables["table5_lab.tex"] = render_table5(tab, proto)

    # ---------------------------------------------------------------- V-E (CFO, BER)
    cfo = sremu_root / CFO_RUN
    cmeta = read_json(cfo / "meta.json")
    nf = cmeta["numbers"]["noise_free"]
    sweep = read_csv(cfo / "vcfo_sweep_noise_free.csv")
    fvals = [float(r["actual_cfo_hz"]) for r in sweep]
    v["numCfoSweepPoints"] = str(len(sweep))
    v["numCfoSweepMaxHz"] = integer(round(max(abs(x) for x in fvals)))
    v["numCfoBurstSamples"] = str(int(sweep[0]["engine_samples"]))
    v["numCfoLsMaxErrHz"] = sci(nf["engine_max_abs_err_ls_hz"])
    v["numCfoLagMaxErrHz"] = sci(nf["engine_max_abs_err_lag1_hz"])
    scale = Fraction(nf["legacy_apparent_scale"]).limit_denominator(1000)
    spt = int(nf["legacy_samples_per_tick"])
    needed = scale * spt
    crb = read_csv(cfo / "vcfo4_rmse_vs_crb.csv")
    fs = float(crb[0]["fs_sps"])
    v["numCfoRateSps"] = str(int(fs))
    v["numCfoSamplesDelivered"] = str(spt)
    v["numCfoSamplesPerTick"] = dec(float(needed), 2)
    v["numCfoTickMs"] = dec(1e3 * float(needed) / fs, 0)
    v["numCfoTickScale"] = dec(nf["legacy_ls"]["slope"], 4)
    v["numCfoTickScalePct"] = dec(100 * (float(scale) - 1), 1)
    v["numCfoHeaderSamples"] = integer(int(float(crb[0]["n_samples"])))
    snr = [float(r["snr_db"]) for r in crb]
    v["numCfoSnrLo"], v["numCfoSnrMid"], v["numCfoSnrHi"] = (dec(s, 0) for s in snr)
    pts = read_csv(cfo / "vcfo4_points.csv")
    v["numCfoPoints"] = str(len({r["cfo_hz"] for r in pts}) if "cfo_hz" in pts[0]
                            else len(pts) // len(snr))
    v["numCfoTrials"] = str(int(float(crb[0]["n_trials_total"])) // int(v["numCfoPoints"]))
    ratios = [float(r["periodogram_rmse_over_crb_pooled"]) for r in crb]
    v["numCfoRatioZero"], v["numCfoRatioTen"], v["numCfoRatioTwenty"] = (dec(x, 2)
                                                                          for x in ratios)
    gated = [float(r["periodogram_rmse_over_crb_max_point"]) for r in crb if float(r["snr_db"])
             >= 10]
    v["numCfoRatioMaxPoint"] = dec(max(gated), 2)
    top = max(crb, key=lambda r: float(r["snr_db"]))  # distance of the lowest pooled ratio
    v["numCfoRatioTwentySe"] = dec(abs(float(top["periodogram_rmse_over_crb_pooled"]) - 1.0)
                                   / float(top["efficient_pooled_ratio_se"]), 1)
    prov["V-CFO"] = {"run": CFO_RUN, "sremu_commit": cmeta["sremu_commit"],
                     "sremu_dirty": cmeta["sremu_dirty"]}

    ber = read_csv(sw / "E-F" / "ber" / "ber.csv")
    v["numBerPoints"] = str(len(ber))
    if not all(r["genie_theory_in_ci95"] == "True" for r in ber):
        raise SystemExit("gen_results: Section V assumes theory inside every genie interval")
    out_ls = [r for r in ber if r["ls64_theory_in_ci95"] != "True"]
    v["numBerLsOutside"] = str(len(out_ls))
    v["numBerLsOutsideMaxEbnoDb"] = dec(max(float(r["ebn0_db"]) for r in out_ls), 0)
    v["numBerEbnoMax"] = dec(max(float(r["ebn0_db"]) for r in ber), 0)
    v["numBerEbnoMin"] = dec(min(float(r["ebn0_db"]) for r in ber), 0)
    prov["E-F"] = read_json(sw / "summary.json")["meta"]["experiments"].get("E-F/ber", {}).get(
        "manifest", {})

    # ---------------------------------------------------------------- V-F (F6)
    f6 = read_json(paper_root / "figures" / "data" / "F6.meta.json")
    v["numDopLosHz"] = dec(f6["f_los_closed_hz"], 3)
    v["numDopLosDevHz"] = sci(f6["max_dev_los_hz"], 1)
    v["numDopRisDevHz"] = dec(f6["max_dev_ris_hz"], 2)
    v["numDopRisMedHz"] = dec(f6["median_dev_ris_hz"], 2)
    v["numDopRisNullPct"] = pct(f6["ris_null_frac"], 0)
    v["numDopRisKeptPct"] = pct(f6["ris_samples_kept_frac"], 0)
    v["numDopTuAutoUs"] = dec(1e6 * f6["t_u_auto_s"], 0)
    v["numDopErrAuto"] = sci(f6["sweep"][0]["err_vs_exact"], 1)
    v["numDopViAuto"] = sci(f6["verify_interp_auto"], 1)
    v["numDopVms"] = dec(f6["v_mps"], 0)
    v["numDopFcGHz"] = dec(f6["fc_hz"] / 1e9, 1)
    v["numDopTuMaxMs"] = dec(1e3 * f6["sweep"][-1]["t_u_s"], 1)
    v["numDopErrMax"] = sci(f6["sweep"][-1]["err_vs_exact"], 1)
    low = [1.0 - s["verify_interp"] / s["err_vs_exact"] for s in f6["sweep"]]
    if min(low) < 0:
        raise SystemExit("gen_results: Section V says verify_interp reads low, never high")
    v["numDopViMaxLowPct"] = pct(max(low), 0)
    prov["F6"] = "figures/data/F6.meta.json"

    # ---------------------------------------------------------------- VII: E-B
    # Primary geometry: modelled on the lab arrangement (SIM, nominal geometry), the AWS run
    # in sweeps/results/E-B-arc. Supporting geometry: the lab-like SIM geometry,
    # sweeps/results/E-B1 and E-B2. The setup macros (packet, latency, guard, leads, tick,
    # Es/N0) are read from the arc run and checked equal for the lab-like run, so one set of
    # macros describes both. F9(b)/(d) show the 0.5 ms guard and the 2 ms lead.
    guard_f9, lead_f9 = "1/2000", "1/500"
    jit_all = ("0", "1/10000", "3/10000", "1/1000", "3/1000")

    def eb1_row(rows: list[dict[str, str]], alpha: str, los: str, trans: str,
                delta: str) -> dict[str, str]:
        hit = [r for r in rows if (r["alphabet"], r["los"], r["transition"], r["delta"])
               == (alpha, los, trans, delta)]
        if len(hit) != 1:
            raise SystemExit(f"gen_results: {len(hit)} E-B1 rows for "
                             f"{alpha}/{los}/{trans}/{delta}")
        return hit[0]

    def eb2_row(rows: list[dict[str, str]], sem: str, j: str, *, lead: str = "",
                los: str = "blocked", es: str = "25.0", guard: str = guard_f9,
                alpha: str = "lab") -> dict[str, str]:
        hit = [r for r in rows if r["alphabet"] == alpha and r["los"] == los
               and r["es_n0_target_db"] == es and r["guard_s"] == guard
               and r["jitter_s"] == j and r["semantics"] == sem
               and (sem != "timed" or r["lead_s"] == lead)]
        if len(hit) != 1:
            raise SystemExit(f"gen_results: {len(hit)} E-B2 rows for {alpha}/{los}/{es}/"
                             f"{sem}/{lead}/{guard}/{j}")
        return hit[0]

    # ----- arc run: provenance and scenario (hash-checked against the run's record)
    arc = sw / "E-B-arc"
    arc_meta = read_json(arc / "meta.json")
    arc_sum = read_json(arc / "eb_summary.json")
    arc_plan = read_json(arc / "eb_plan.json")
    arc_code = read_json(arc / "git_state.json")["code"]["files"]
    if not (arc_sum["ok"] and arc_sum["n_failed"] == 0):
        raise SystemExit("gen_results: the E-B arc run has failed points")
    arc_rel = "scenarios/studies/lab_arc.yaml"
    arc_path = sremu_root / arc_rel
    if hashlib.sha256(arc_path.read_bytes()).hexdigest() != arc_code[arc_rel]:
        raise SystemExit(f"gen_results: {arc_rel} differs from the file the E-B arc run used")
    for name in ("E-B1/meta.json", "E-B2/meta.json"):
        if arc_meta["geometry_label"] not in read_json(arc / name)["assumptions"]:
            raise SystemExit(f"gen_results: E-B arc {name} lacks the geometry label")
    arc_scen = read_yaml(arc_path)
    # Either the run records its commit (git_state.json and every point manifest, as a
    # regeneration job does: experiments/finalize.py --eb-arc) or, for the first AWS run whose
    # manifests carry no git commit, the commit comes from the job's export and is tied to the
    # run by the code hashes (meta.json "commit_verified": the tree of the commit, rebuilt
    # read-only from its git objects, hashes to the run's src/sremu tree hash).
    arc_gs = read_json(arc / "git_state.json")
    arc_ver = arc_meta["sremu"].get("commit_verified") or {}
    arc_shared = (read_json(arc / "run_manifests.json").get("shared") or {}) if (
        arc / "run_manifests.json").is_file() else {}
    arc_recorded = (arc_gs.get("commit") is not None and arc_gs.get("dirty") is False
                    and arc_gs["commit"] == arc_meta["sremu"]["commit"]
                    and (arc_shared.get("git_commit"), arc_shared.get("git_dirty"))
                    == (arc_gs["commit"], False))
    if not (arc_recorded or (arc_ver.get("src_tree_equals_run")
                             and arc_ver.get("src_sremu_tree_sha256_at_commit")
                             == arc_gs["code"]["src_sremu_tree_sha256"])):
        raise SystemExit("gen_results: the E-B arc commit is not tied to the run's code hashes")
    prov["E-B-arc"] = {
        "results": "sweeps/results/E-B-arc", "run_id": arc_meta["run"]["run_id"],
        "sremu_commit": arc_meta["sremu"]["commit"], "sremu_dirty": arc_meta["sremu"]["dirty"],
        "commit_source": ("recorded by the run (git_state.json and every point manifest)"
                          if arc_recorded else
                          "SREMU_GIT_COMMIT export of the AWS job (host/job_stdout.txt); the "
                          "commit's src/sremu tree hash equals the run's (meta.json "
                          "sremu.commit_verified)"),
        "aws_instance": arc_meta["host"]["instance_type"],
        "geometry_label": arc_meta["geometry_label"], "t_end_utc": arc_sum["t_end_utc"],
    }

    # ----- setup (shared; read from the arc scenario and the arc plan)
    lab_scen = read_yaml(sremu_root / "scenarios" / "lab_like_A.yaml")
    traffic = arc_scen["traffic"][0]["params"]
    tx_stream = arc_scen["nodes"][0]["streams"][0]
    lab_tx = lab_scen["nodes"][0]["streams"][0]
    for key in ("sps", "preamble_syms", "payload_syms"):
        if str(traffic[key]) != str(lab_scen["traffic"][0]["params"][key]):
            raise SystemExit(f"gen_results: E-B packet {key} differs between the geometries")
    for key in ("fc_hz", "rate_sps"):
        if str(tx_stream[key]) != str(lab_tx[key]):
            raise SystemExit(f"gen_results: E-B {key} differs between the geometries")
    rate = float(Fraction(str(tx_stream["rate_sps"])))
    payload_bits = int(traffic["payload_syms"]) * 2  # QPSK
    v["numEbFcGHz"] = dec(float(tx_stream["fc_hz"]) / 1e9, 1)
    v["numEbRateMsps"] = dec(rate / 1e6, 0)
    v["numEbSps"] = str(int(traffic["sps"]))
    v["numEbPreamble"] = str(int(traffic["preamble_syms"]))
    v["numEbPayload"] = str(int(traffic["payload_syms"]))
    v["numEbOneBits"] = integer(payload_bits)
    v["numEbPacketMs"] = dec(1e3 * (int(traffic["preamble_syms"]) + int(traffic["payload_syms"]))
                             * int(traffic["sps"]) / rate, 3)
    specs = {s["name"]: s for s in arc_plan["specs"]}
    eb1_spec = specs["eb1"]
    if read_yaml(paper_root / "sweeps" / "eb1.yaml")["grid"]["transition"] != \
            eb1_spec["grid"]["transition"]:
        raise SystemExit("gen_results: E-B1 transitions differ between the geometries")
    lin_us = sorted(int(m.group(1)) for x in eb1_spec["grid"]["transition"]
                    if (m := re.fullmatch(r"linear(\d+)us", x)))
    v["numEbTransLongUs"] = str(lin_us[-1])
    eb2_spec = specs["eb2_lab"]
    lab_eb2_spec = read_yaml(paper_root / "sweeps" / "eb2_lab.yaml")
    leads = sorted({Fraction(x) for x in eb2_spec["zip"]["lead_s"]})
    jits = sorted(Fraction(x) for x in eb2_spec["grid"]["jitter_s"])
    guards = {Fraction(x) for x in eb2_spec["grid"]["guard_s"]}
    if (leads != sorted({Fraction(x) for x in lab_eb2_spec["zip"]["lead_s"]})
            or jits != sorted(Fraction(x) for x in lab_eb2_spec["grid"]["jitter_s"])
            or guards != {Fraction(x) for x in lab_eb2_spec["grid"]["guard_s"]}):
        raise SystemExit("gen_results: E-B2 leads, jitters or guards differ between geometries")
    if Fraction(guard_f9) not in guards or Fraction(lead_f9) not in leads:
        raise SystemExit("gen_results: the F9 slice (guard, lead) is not in the sweep")
    if [Fraction(j) for j in jit_all] != jits:
        raise SystemExit("gen_results: E-B2 jitter grid changed")
    lead_ms = [dec(1e3 * float(x), 1 if x < Fraction(1, 1000) else 0) for x in leads]
    v["numEbLeadsMs"] = ", ".join(lead_ms[:-1]) + " or " + lead_ms[-1]
    v["numEbLeadMinMs"] = lead_ms[0]
    v["numEbLeadMidMs"] = dec(1e3 * float(Fraction(lead_f9)), 0)
    v["numEbLeadMaxMs"] = lead_ms[-1]
    v["numEbJitterMaxMs"] = dec(1e3 * float(jits[-1]), 0)
    v["numEbJitterOneMs"] = dec(1e3 * float(Fraction("1/1000")), 0)
    v["numEbGuardMs"] = dec(1e3 * float(Fraction(guard_f9)), 1)
    lat: set[str] = set()
    tick: set[str] = set()
    for d in (arc / "E-B2", sw / "E-B2"):
        for a in read_json(d / "meta.json")["assumptions"]:
            if m := re.search(r"\((\d+(?:\.\d+)?) ms \+ U\[0, J\]\)", a):
                lat.add(m.group(1))
            if m := re.search(r"next (\d+(?:\.\d+)?) ms tick", a):
                tick.add(m.group(1))
    if len(lat) != 1 or len(tick) != 1:
        raise SystemExit(f"gen_results: E-B2 latency {lat} or tick {tick} not unique")
    v["numEbLatencyMs"] = lat.pop()
    v["numEbTickMs"] = tick.pop()
    lat_fixed = Fraction(v["numEbLatencyMs"]) / 1000

    # ----- arc E-B1 (mechanism, VA-5)
    a1 = read_csv(arc / "E-B1" / "eb1_points.csv")
    ava5 = read_json(arc / "E-B1" / "va5.json")
    if not (ava5["pass"] and ava5["all_before_bitwise_identical"]
            and ava5["all_panels_k_applied_equal_expected"]
            and ava5["n_pass"] == ava5["n_step_switches_inside_packet"]):
        raise SystemExit("gen_results: VA-5 did not pass on the arc")
    v["numVaFiveArcTotal"] = str(ava5["n_step_switches_inside_packet"])
    n_panels = {int(r["n_panels"]) for r in a1}
    if n_panels != {len(arc_scen["ris"])}:
        raise SystemExit("gen_results: E-B arc panel count differs from the scenario")
    v["txtEbArcPanels"] = prose_count(n_panels.pop())
    grids = {(int(p["grid"]["n_u"]), int(p["grid"]["n_v"])) for p in arc_scen["ris"]}
    if len(grids) != 1:
        raise SystemExit("gen_results: E-B arc tiles differ in size")
    nu, nv = grids.pop()
    v["numEbArcCellsU"], v["numEbArcCellsV"] = str(nu), str(nv)
    nodes = {n["id"]: n["position_m"] for n in arc_scen["nodes"]}
    tx = nodes["ap"]
    radii = [math.dist(tx, p["pose"]["center_m"]) for p in arc_scen["ris"]]
    if max(radii) - min(radii) > 1e-6:
        raise SystemExit("gen_results: the E-B arc tiles are not on one radius")
    v["numEbArcRadiusM"] = dec(radii[0], 2)
    rx_d = [math.dist(tx, nodes[k]) for k in ("rx1", "rx2")]
    if abs(rx_d[0] - rx_d[1]) > 1e-6:
        raise SystemExit("gen_results: the E-B arc receivers are not equidistant")
    v["numEbArcRxDistM"] = dec(rx_d[0], 1)
    for r in a1:
        if int(r["bit_errors"]) != round(float(r["ber"]) * payload_bits):
            raise SystemExit("gen_results: E-B arc payload size differs from the scenario")
    ablk = eb1_row(a1, "lab", "blocked", "step", "1/4")
    apres = eb1_row(a1, "lab", "present", "step", "1/4")
    alin = eb1_row(a1, "lab", "blocked", "linear200us", "1/4")
    v["numEbArcBlockedChangeDb"] = dec(ablk["rx1_total_change_db"], 1)
    v["numEbArcBlockedRotDeg"] = dec(abs(float(ablk["rx1_total_rotation_deg"])), 0)
    v["numEbArcRefEvmDb"] = dec(ablk["ref_evm_db"], 1)
    v["numEbArcOneErrors"] = str(int(ablk["bit_errors"]))
    v["numEbArcLinErrors"] = str(int(alin["bit_errors"]))
    v["numEbArcLosChangeDb"] = dec(apres["rx1_total_change_db"], 1)
    v["numEbArcLosRotDeg"] = dec(abs(float(apres["rx1_total_rotation_deg"])), 0)
    v["numEbArcLosEvmDb"] = dec(apres["evm_db"], 1)
    v["numEbArcLosOneErrors"] = str(int(apres["bit_errors"]))

    # ----- arc E-B2 (timing): the two cases of F9(b) and the LOS-present slice at 25 dB
    a2 = read_csv(arc / "E-B2" / "eb2_per.csv")
    es_all = sorted({float(r["es_n0_target_db"]) for r in a2})
    if (len(es_all) != 2 or "pre_declared" not in arc_plan
            or specs["eb2_lab_es15"]["base"].get("es_n0_db") != es_all[0]):
        raise SystemExit("gen_results: the arc Es/N0 sensitivity is not the pre-declared one")
    es_lo, es_hi = repr(es_all[0]), repr(es_all[1])
    v["numEbEsnoDb"] = dec(es_all[1], 0)
    v["numEbArcEsnoLowDb"] = dec(es_all[0], 0)
    cases = (("present", es_lo), ("blocked", es_hi))  # F9(b)
    n_pk = {int(r["n_packets"]) for r in a2}
    if len(n_pk) != 1:
        raise SystemExit("gen_results: E-B2 packet counts differ between points")
    n_packets = n_pk.pop()
    v["numEbPackets"] = integer(n_packets)
    zero_hi = {float(r["per_ci_high"]) for r in a2 if int(r["packet_errors"]) == 0}
    if len(zero_hi) != 1:
        raise SystemExit("gen_results: E-B2 Clopper-Pearson limit of PER = 0 not unique")
    v["numEbPerUpperPct"] = pct(zero_hi.pop(), 2)

    def arc_per(sem: str, j: str, case: tuple[str, str], lead: str = "") -> float:
        r = eb2_row(a2, sem, j, lead=lead, los=case[0], es=case[1])
        if not float(r["per_ci_low"]) <= float(r["per"]) <= float(r["per_ci_high"]):
            raise SystemExit("gen_results: PER outside its Clopper-Pearson interval")
        return float(r["per"])

    tick_all = [arc_per("tick-based", j, c) for c in cases for j in jit_all]
    v["numEbArcTickPerMinPct"] = pct(min(tick_all), 0)
    v["numEbArcTickPerMaxPct"] = pct(max(tick_all), 0)
    lat_max = {j: lat_fixed + Fraction(j) for j in jit_all}
    for c in cases:
        # timed: no packet lost while the lead exceeds the worst latency, losses beyond it
        for ld in sorted({str(x) for x in leads}, key=Fraction):
            for j in jit_all:
                p = arc_per("timed", j, c, ld)
                if Fraction(ld) > lat_max[j] and p != 0.0:
                    raise SystemExit(f"gen_results: timed lead {ld} lost packets at J = {j}")
                if Fraction(ld) < lat_max[j] and p == 0.0:
                    raise SystemExit(f"gen_results: timed lead {ld} lost none at J = {j}")
        # asap: no packet lost while the worst latency fits in the guard
        for j in jit_all:
            p = arc_per("asap", j, c)
            if (lat_max[j] <= Fraction(guard_f9)) != (p == 0.0):
                raise SystemExit(f"gen_results: asap at J = {j} does not follow the guard rule")
    ok = [j for j in jit_all if lat_max[j] <= Fraction(guard_f9)]
    v["numEbArcAsapJitterOkMs"] = dec(1e3 * float(Fraction(ok[-1])), 1)
    los_c, blk_c = cases
    half = str(leads[0])
    v["numEbArcTimedTwoPerThreeLosPct"] = pct(arc_per("timed", "3/1000", los_c, lead_f9), 0)
    v["numEbArcTimedTwoPerThreeBlkPct"] = pct(arc_per("timed", "3/1000", blk_c, lead_f9), 0)
    v["numEbArcTimedHalfPerOneLosPct"] = pct(arc_per("timed", "1/1000", los_c, half), 0)
    v["numEbArcTimedHalfPerOneBlkPct"] = pct(arc_per("timed", "1/1000", blk_c, half), 0)
    v["numEbArcAsapPerOneLosPct"] = pct(arc_per("asap", "1/1000", los_c), 0)
    v["numEbArcAsapPerOneBlkPct"] = pct(arc_per("asap", "1/1000", blk_c), 0)
    hi_rows = [r for r in a2 if r["los"] == "present" and r["es_n0_target_db"] == es_hi
               and r["guard_s"] == guard_f9]  # both alphabets: "no semantics loses more"
    v["numEbArcLosHighPerMaxPct"] = pct(max(float(r["per"]) for r in hi_rows), 1)
    # The abstract and the conclusion state txtEBResult for the whole guard slice of the run
    # (every alphabet, LOS case and Es/N0), not only for the two F9(b) cases: check it there.
    # At the other guards a few Es/N0 = 15 dB rows lose one packet in 2000 to noise even with
    # timed commands on time, so the phrase is tied to the guard named next to it.
    slice_rows = [r for r in a2 if r["guard_s"] == guard_f9]
    for r in slice_rows:
        if (r["semantics"] == "timed" and Fraction(r["lead_s"]) > lat_fixed
                + Fraction(r["jitter_s"]) and int(r["packet_errors"]) != 0):
            raise SystemExit("gen_results: txtEBResult says timed commands whose lead exceeds the "
                             f"latency lose no packet; {r['alphabet']}/{r['los']}/"
                             f"{r['es_n0_target_db']} lead {r['lead_s']} J {r['jitter_s']} lost "
                             f"{r['packet_errors']}")
    if max(float(r["per"]) for r in slice_rows if r["semantics"] == "tick-based") \
            != max(tick_all):
        raise SystemExit("gen_results: the largest tick-based PER of the guard slice is not in "
                         "the F9(b) cases that txtEBResult quotes")
    v["txtEBResult"] = ("timed commands whose lead exceeds the latency lose no packet, "
                        f"tick-based ones up to {v['numEbArcTickPerMaxPct']}\\,\\%")

    # ----- lab-like geometry (supporting): E-B1 / VA-5 and the LOS-blocked F9(d) slice
    eb1 = read_csv(sw / "E-B1" / "eb1_points.csv")
    va5 = read_json(sw / "E-B1" / "va5.json")
    v["numVaFiveTotal"] = str(va5["n_step_switches_inside_packet"])
    if not (va5["pass"] and va5["all_before_bitwise_identical"]
            and va5["n_pass"] == va5["n_step_switches_inside_packet"]):
        raise SystemExit("gen_results: VA-5 did not pass")
    blk = eb1_row(eb1, "lab", "blocked", "step", "1/4")
    if not abs(float(blk["ber"]) - int(blk["bit_errors"]) / payload_bits) < 1e-12:
        raise SystemExit("gen_results: E-B payload size changed")
    v["numEbOneErrors"] = str(int(blk["bit_errors"]))
    v["numEbBlockedChangeDb"] = dec(blk["rx1_total_change_db"], 1)
    v["numEbBlockedRotDeg"] = dec(abs(float(blk["rx1_total_rotation_deg"])), 0)
    eb2 = read_csv(sw / "E-B2" / "eb2_per.csv")
    if {r["es_n0_target_db"] for r in eb2} != {es_hi}:
        raise SystemExit("gen_results: the lab-like E-B2 run is not at the arc's main Es/N0")
    if {int(r["n_packets"]) for r in eb2} != {n_packets}:
        raise SystemExit("gen_results: lab-like E-B2 packet count differs from the arc run")
    for j in jit_all:
        p = float(eb2_row(eb2, "timed", j, lead=lead_f9)["per"])
        if (Fraction(lead_f9) > lat_fixed + Fraction(j)) != (p == 0.0):
            raise SystemExit(f"gen_results: lab-like timed (lead {lead_f9}) at J = {j} does "
                             "not follow the lead rule")
    v["numEbTimedTwoPerThreePct"] = pct(float(eb2_row(eb2, "timed", "3/1000",
                                                      lead=lead_f9)["per"]), 0)
    v["numEbAsapPerOnePct"] = pct(float(eb2_row(eb2, "asap", "1/1000")["per"]), 0)
    v["numEbTickPerZeroPct"] = pct(float(eb2_row(eb2, "tick-based", "0")["per"]), 0)
    v["numEbTickPerMaxPct"] = pct(max(float(eb2_row(eb2, "tick-based", j)["per"])
                                      for j in jit_all), 0)
    los_rows = [r for r in eb2 if r["los"] == "present" and r["alphabet"] == "lab"]
    if sum(int(r["packet_errors"]) for r in los_rows):
        raise SystemExit("gen_results: Section VII says the lab-like LOS-present case loses "
                         "no packet")

    # ---------------------------------------------------------------- VII: E-A
    gains = read_csv(sw / "E-A" / "gains.csv")
    hyp = read_json(sw / "E-A" / "hypotheses.json")
    tau = {r["geometry"]: r for r in read_csv(sw / "E-A" / "tau_half.csv")}

    def gain(geo: str, ctrl: str, t: str = "") -> dict[str, str]:
        return next(r for r in gains if r["geometry"] == geo and r["controller"] == ctrl
                    and r["tau_sui_s"] == t)

    g1, g2, g0 = "f1.0G_h2_L20", "f2.4G_h2_L20", "f1.0G_h2_L0"
    inst = gain(g1, "instant")
    v["numEaGainOpt"] = dec(inst["gain_db"], 2)  # mean of per-batch dB gains (pre-registered)
    v["numEaGainOptLo"] = dec(inst["gain_ci_low_db"], 2)
    v["numEaGainOptHi"] = dec(inst["gain_ci_high_db"], 2)
    v["numEaGainOptTwo"] = dec(gain(g2, "instant")["gain_db"], 2)
    v["numEaGainStale"] = dec(gain(g1, "stale")["gain_db"], 2)
    v["numEaGainUrap"] = dec(gain(g1, "urap", "1")["gain_db"], 2)
    v["numEaGainBandit"] = dec(gain(g1, "bandit", "1")["gain_db"], 2)
    v["numEaGainWclFast"] = dec(gain(g1, "wcl", "0.01")["gain_db"], 2)
    v["numEaGainAzero"] = dec(gain(g0, "instant")["gain_db"], 2)
    h1 = hyp["H-A1"]
    v["numEaTauHalf"] = dec(h1["tau_half_s"], 3)
    v["numEaTauHalfLo"] = dec(h1["ci_s"][0], 3)
    v["numEaTauHalfHi"] = dec(h1["ci_s"][1], 3)
    v["numEaPeriodOverTau"] = dec(h1["movement_period_over_tau_half"], 0)
    v["numEaTauHalfTwo"] = dec(tau[g2]["tau_half_s"], 3)
    h2 = hyp["H-A2"]
    v["numEaRatio"] = dec(h2["ratio"], 3)
    v["numEaRatioLo"] = dec(h2["ci"][0], 3)
    v["numEaRatioHi"] = dec(h2["ci"][1], 3)
    v["numEaRatioPred"] = dec(h2["prediction"], 2)
    h3 = hyp["H-A3"]["per_tau"]
    deltas = [x["delta_db"] for x in h3.values()]
    v["numEaBanditMinDb"] = dec(min(deltas), 2)
    v["numEaBanditMaxDb"] = dec(max(deltas), 2)
    decisions = {k: hyp[k]["decision"] for k in ("H-A1", "H-A2", "H-A3")}
    if set(decisions.values()) != {"supported"}:
        raise SystemExit(f"gen_results: Section VII text assumes H-A1..3 supported: {decisions}")
    lam_v = C0 / 1e9 / 1.0  # lambda / v at 1 GHz and 1 m/s (s)
    v["numEaTauOverLambdaV"] = dec(h1["tau_half_s"] / lam_v, 2)
    v["txtEAResult"] = (f"loses half its benefit after {v['numEaTauHalf']}\\,s, a time set by "
                        f"$\\lambda/v$")
    # setup of E-A, read from the corridor template and the sweep specs (not typed)
    corr = read_yaml(sremu_root / "scenarios" / "studies" / "ea_corridor.yaml")
    mu = next(nd for nd in corr["nodes"] if nd["id"] == "mu")["trajectory"]
    length = float(mu["p1_m"][0]) - float(mu["p0_m"][0])
    speed = float(mu["speed_mps"])
    v["numEaLengthM"] = dec(length, 0)
    ap = next(nd for nd in corr["nodes"] if nd["id"] == "ap")["position_m"]
    v["numEaApOffsetM"] = dec(abs(float(ap[1])), 1)
    v["numEaSpeed"] = dec(speed, 0)
    ea_spec = read_yaml(paper_root / "sweeps" / "ea_tau.yaml")
    v["numEaBatches"] = str(int(ea_spec["base"]["n_batches"]))
    steps = int(ea_spec["base"]["steps_per_batch"])
    v["numEaPeriods"] = dec(steps * 0.005 / (length / (3 * speed)), 0)
    fcs = sorted({float(x) for x in ea_spec["zip"]["fc_hz"]})
    v["numEaFcOneGHz"] = dec(fcs[0] / 1e9, 0)
    v["numEaFcTwoGHz"] = dec(fcs[1] / 1e9, 1)
    cells = [math.floor(length / (C0 / f / 4) + 1e-9) for f in fcs]
    grid_n = int(corr["ris"][0]["grid"]["n_u"])
    if cells[0] != grid_n:
        raise SystemExit("gen_results: E-A cell count differs from the template")
    v["numEaCellsOne"], v["numEaCellsTwo"] = str(cells[0]), str(cells[1])
    v["numEaHeightM"] = dec(float(inst["h_m"]), 0)
    v["numEaLossDb"] = dec(float(inst["loss_db"]), 0)
    v["numEaTauBanditMinS"] = dec(min(float(k) for k in h3), 1)
    va1_meta = read_json(sw / "E-A-IQ" / "meta.json")
    tol = next(re.search(r"<= (\d+\.\d+) dB", a) for a in va1_meta["assumptions"]
               if re.search(r"<= (\d+\.\d+) dB", a))
    v["numVaOneTolDb"] = tol.group(1)
    va1 = read_csv(sw / "E-A-IQ" / "va1.csv")
    v["numVaOneRows"] = str(len(va1))
    v["numVaOneMaxDiffDb"] = dec(max(abs(float(r["diff_iq_minus_vectorized_mid_db"]))
                                     for r in va1), 3, mode=ROUND_CEILING)
    v["numVaOneDurS"] = integer(int(float(va1[0]["duration_s"])))
    if not all(r["accept"] == "True" for r in va1):
        raise SystemExit("gen_results: VA-1 row not accepted")
    prov["E-A"] = read_json(sw / "E-A" / "meta.json").get("written_utc")

    # ---------------------------------------------------------------- VII: E-C
    ec = {r["row"]: r for r in read_csv(sw / "E-C" / "table8.csv")}
    geos = ("lab", "synth_near", "synth_far", "corridor")
    one = [float(ec["ideal_1bit"][f"loss_ris_db_{g}"]) for g in geos]
    v["numEcOneBitMin"] = dec(min(abs(x) for x in one), 1)
    v["numEcOneBitMax"] = dec(max(abs(x) for x in one), 1)
    v["numEcAbsD"] = dec(ec["lab_mag1"]["md_abs_d_db"], 2)
    v["numEcAbsM"] = dec(ec["lab_mag1"]["md_abs_m_db"], 1)
    labm = [float(ec["lab_mag1"][f"loss_ris_db_{g}"]) - x for g, x in zip(geos, one,
                                                                          strict=True)]
    v["numEcLabVsOneBitMaxDb"] = dec(max(abs(x) for x in labm), 2, mode=ROUND_CEILING)
    half = [abs(float(ec["lab_mag2_0p5"][f"loss_ris_db_{g}"])) for g in geos]
    v["numEcHalfMaxDb"] = dec(max(half), 1)
    pen = [float(ec["ideal_designed_on_lab"][f"loss_ris_db_{g}"])
           - float(ec["lab_mag1"][f"loss_ris_db_{g}"]) for g in geos]
    v["numEcDesignPenaltyMinDb"] = dec(min(abs(x) for x in pen), 2)
    v["numEcDesignPenaltyMaxDb"] = dec(max(abs(x) for x in pen), 2)
    eff = [float(ec["lab_eff0p5"][f"loss_ris_db_{g}"]) - float(ec["lab_mag1"][f"loss_ris_db_{g}"])
           for g in geos]
    v["numEcEffExtraDb"] = dec(sum(abs(x) for x in eff) / len(eff), 1)
    v["numEcOneBitEta"] = dec(ec["ideal_1bit"]["eta_large_n_db"], 2, absval=True)
    tables["table8_ablation.tex"] = render_table8(ec)

    # ---------------------------------------------------------------- VII: E-D
    fm = sremu_root / FM_RUN
    emap = read_csv(fm / "vfm_ed_map.csv")
    prim = [r for r in emap if r["baseline"] == "two_ray_knife_edge"]
    p526 = [r for r in emap if r["baseline"] == "p526_plus_knife_edge_experimental"]
    prim_lat = [r for r in prim if r["geometry"] == "lateral" and r["ground_on_ris_hops"] == "true"]
    a = [float(r["a_req_equal_power_m2"]) for r in prim_lat]
    v["numFmAreqMin"] = integer(round(min(a)))
    v["numFmAreqMax"] = integer(round(max(a)))
    v["numFmPanelMtwo"] = dec(float(prim[0]["a0_m2"]), 2)
    v["numFmPrimaryEntries"] = integer(len(prim))
    v["numFmPrimarySufficient"] = str(sum(r["a0_sufficient_equal_power"] == "true" for r in prim))
    v["numFmPfiveEntries"] = integer(len(p526))
    v["numFmPfiveSufficient"] = str(sum(r["a0_sufficient_equal_power"] == "true" for r in p526))
    four = [r for r in emap if r["baseline"] == "four_ray_knife_edge_sensitivity"]
    if len(four) != len(prim):
        raise SystemExit("gen_results: E-D four-ray sensitivity rows changed")
    v["numFmFourRaySufficient"] = str(sum(r["a0_sufficient_equal_power"] == "true"
                                          for r in four))
    # grazing obstruction (nu = 0) at every RIS-to-receiver distance and receiver height of
    # Fig. 12 (lateral panel, ground on both hops)
    grazing = [r for r in prim_lat if float(r["nu"]) == 0.0]
    gap = [-float(r["ris_minus_direct_at_a0_db"]) for r in grazing]
    v["numFmGapMinDb"] = dec(min(gap), 1)
    v["numFmGapMaxDb"] = dec(max(gap), 1)
    fmeta = read_json(fm / "meta.json")
    prov["V-FM"] = {"run": FM_RUN, "sremu_commit": fmeta.get("sremu_commit"),
                    "sremu_dirty": fmeta.get("sremu_dirty")}

    # ---------------------------------------------------------------- VI: B1-B6, Table VII
    from bench_results import bench_values  # sweeps/results/bench (MEAS; F5 stays pending)

    bv, bprov, btables = bench_values(paper_root)
    clash = sorted(set(bv) & set(v))
    if clash:
        raise SystemExit(f"gen_results: benchmark macros computed twice: {clash}")
    v.update(bv)
    prov["bench"] = bprov
    tables.update(btables)
    return {k: s for k, s in v.items() if s is not None}, prov, tables


def text_budget_check(paper_root: Path, values: dict[str, str]) -> list[str]:
    """Each value must fit its macro's max_chars (as plain text) and be declared."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_arxiv import tex_to_plain

    spec = {m.macro: m for m in load_numbers_spec(paper_root / "numbers.yaml")}
    errors = []
    for k, val in values.items():
        m = spec.get(k)
        if m is None:
            errors.append(f"{k} is not declared in numbers.yaml")
            continue
        if m.value is not None or m.source_type == "code":
            errors.append(f"{k} is a definition/data/code macro and may not be in results")
        plain = tex_to_plain(val, {})
        if len(plain) > m.max_chars:
            errors.append(f"{k} = {plain!r} has {len(plain)} > {m.max_chars} characters")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paper-root", type=Path, default=PAPER_ROOT)
    ap.add_argument("--check", action="store_true", help="compare with the committed files")
    args = ap.parse_args(argv)
    root = args.paper_root
    lock = read_yaml(root / "sremu.lock") or {}
    sremu_root = (root / str(lock.get("repo", "../sremu"))).resolve()
    values, prov, tables = compute(root, sremu_root)
    errors = text_budget_check(root, values)
    if errors:
        print("gen_results: " + "; ".join(errors))
        return 2
    doc = {"sremu_commit": git_head(sremu_root) or "unknown",
           "note": "values computed by tools/gen_results.py from committed result files; the "
                   "studies ran at the commits listed in provenance (several were dirty trees); "
                   "a final build needs every study rerun at the pinned commit",
           "values": dict(sorted(values.items())), "provenance": prov}
    out = {root / "generated" / "results.json": json.dumps(doc, indent=2, ensure_ascii=True) + "\n"}
    for name, text in tables.items():
        out[root / "generated" / "tables" / name] = text
    if args.check:
        stale = [str(p.relative_to(root)) for p, t in out.items()
                 if p.name != "results.json" and (not p.is_file() or p.read_text("utf-8") != t)]
        cur = read_json(root / "generated" / "results.json") if (
            root / "generated" / "results.json").is_file() else {}
        if cur.get("values") != doc["values"]:
            stale.append("generated/results.json")
        print("gen_results: " + ("stale: " + ", ".join(stale) if stale else "up to date"))
        return 1 if stale else 0
    for p, t in out.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t, encoding="utf-8", newline="\n")
    print(f"gen_results: {len(values)} values, {len(tables)} tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
