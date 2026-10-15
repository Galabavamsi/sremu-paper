"""gen_results.py -- pipeline values of the result macros and the result tables (WP12;
DESIGN 1.6 rule 7, 13.5 steps 4-5).

Reads ONLY committed result files and the paper's figure data, never typed numbers:

* the pinned test record ``generated/junit/*.xml`` (Table IV, oracle counts);
* sremu ``validation/{tang,cfo,fm,ants_fig4b}/results/<run>`` and the V-LAB protocol;
* ``sweeps/results/{E-A,E-A-IQ,E-B1,E-B2,E-C,E-F}`` (WP11 results) and
  ``sweeps/results/E-B-arc`` (E-B on the lab arc, AWS run);
* ``sweeps/results/E-E`` (GNU Radio through the raw REQ/REP mode; Table ``tab:ee``) and
  ``sweeps/results/E-E-laptop`` (its laptop throughput, MEAS);
* ``sweeps/results/sensitivity_cellmodel`` (point vs po_patch at the lab pitch, Section IV-C);
* ``figures/data/F3.meta.json`` and ``F6.meta.json`` (values computed by those figure
  scripts with the installed sremu) and ``F9.meta.json`` (the EVM smoothing window of F9);
* ``sweeps/results/V-LAB-provenance`` (GitHub's push log: checks the fixed V-LAB lock facts of
  numbers.yaml), ``availability.yaml`` (code and data availability) and the sremu checkout's
  ``pyproject.toml`` and ``src/sremu`` (implementation facts).

Writes ``generated/results.json`` = ``{"sremu_commit", "values": {macro: LaTeX}, "provenance"}``
(read by build_paper.py into generated/numbers.tex) and ``generated/tables/
table4_vv.tex``, ``table5_lab.tex``, ``table8_ablation.tex``, ``tableA_tang.tex`` (per-geometry
accuracy against Tang et al.), ``tableA_provenance.tex`` (producing commit, tree state,
host, date and run of every result, from the result files' own records), through
``tools/case_tables.py``, ``tableF_eb2grid.tex`` (the full E-B2 grid with the closed-form
exposure probability) and ``tableF_eageo.tex`` (tau_half over every E-A geometry) and, through
``tools/vv_ids.py``, ``tableA_ids.tex`` (every cited verification identifier, from
``verification_ids.yaml`` checked against sremu's specification and tests). Every value fits the
macro's ``max_chars`` budget (text macros are checked against it here).

Rounding: decimals are rounded half-even on the exact decimal value of the source
(``Decimal(repr(x))``); the Tang curve deviation is a bound (max rounded UP to 0.01 dB).
Section VI values and Table VII come from ``sweeps/results/bench`` and
``sweeps/results/E-F/latency`` through ``tools/bench_results.py`` (F5 from the B2-F45 rerun,
E-F latency from the regeneration job). Appendix H's statement that the installed emulator has
the package source of the regeneration commit is checked here (src/sremu hash against the hash
the regeneration results record). With availability.yaml
``status: conditional`` the availability macros expand to ``\\sremuUnreleased`` (the text
prints no licence, URL or DOI); with ``status: released`` PENDING fields stay placeholders.

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
import tomllib
import xml.etree.ElementTree as ET
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import case_tables  # E-B2 closed form, E-A per-geometry table (Section VII, Appendix F)
from paper_common import (
    PAPER_ROOT,
    is_implementation_test,
    load_junit,
    load_numbers_spec,
    read_yaml,
    spec_id_pattern,
    spec_id_status,
)

TANG_RUN = "validation/tang/results/20261012T025951Z_vtang"
CFO_RUN = "validation/cfo/results/20261012T025950Z_vcfo"
FM_RUN = "validation/fm/results/20261012T025951Z_vfm"
LAB_RUN = "validation/ants_fig4b/results/20261011T003044Z_vlab_unblinded"
LAB_PROTOCOL = "validation/ants_fig4b/protocol.json"
LAB_PUSH_LOG = "sweeps/results/V-LAB-provenance/sremu_activity_20261011T1426Z.json"
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
    # VA (DESIGN 4.11, 11 and the WP9/WP11 acceptance): the specification defines VA-1, VA-3,
    # VA-4 and VA-5; there is no VA-2.
    ("VA", "Consistency of engine paths: served IQ path vs batch evaluation (E-A), exhaustive "
           "optimum vs every controller, IQ path equals batch path, sample-exact switch",
     ["VA-1", "VA-3", "VA-4", "VA-5"]),
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
        r"\caption{Verification summary from the test record of emulator commit "
        + str(commit)[:7] + r" (Appendix~\ref{app:repro}). An identifier passes when at"
        r" least one implementation test that carries"
        r" it passed and none failed or was skipped; rendering-path identifiers count once per"
        r" backend. Tables~\ref{tab:ids-ph} and~\ref{tab:ids-rt} give the property, oracle and"
        r" tolerance of every identifier. PH-12, a regression value"
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
        r" pre-specified protocol (median, with 5--95\,\% range for the RMSE). Error ="
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
        r" SIM-sremu; ideal alphabets ANALYTIC; lab amplitudes ASSUMED; Tang states REPORTED."
        r" $|\Gamma_2|$: magnitude of the second laboratory state, the other having unit"
        r" magnitude; ---: no closed form for stuck cells, whose fixed term depends on the"
        r" random draw.}",
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


# --------------------------------------------------------------------------- Tang per geometry
def err_stats(e: list[float]) -> dict[str, float]:
    """Bias, sample SD (ddof 1), RMSE and largest |e| of residuals ``e``."""
    n = len(e)
    bias = sum(e) / n
    return {"n": n, "bias": bias, "sd": math.sqrt(sum((x - bias) ** 2 for x in e) / (n - 1)),
            "rmse": math.sqrt(sum(x * x for x in e) / n), "max_abs": max(abs(x) for x in e)}


def render_tang_table(points: list[dict[str, str]]) -> str:
    """Appendix table: measured minus emulator per geometry of V-TANG (MAJOR-9)."""
    lines = [
        "% GENERATED by paper/tools/gen_results.py from sremu " + TANG_RUN + "/vtang_points.csv.",
        "% Do not edit.",
        r"\begin{table}[t]",
        r"\caption{Emulator against the digitized chamber measurements of Tang et"
        r" al.~\cite{Tang2022mmWave} per geometry (transmitter distance $d_1$ and angle"
        r" $\theta$; nine receiver distances each): measured minus emulator in dB, and the"
        r" largest $|$emulator $-$ published model curve$|$. Measured points and curves: DIG;"
        r" emulator: SIM-sremu; link budget: REPORTED.}",
        r"\label{tab:tang}",
        r"\centering",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{3pt}",
        r"\begin{tabular}{@{}lrrrrrr@{}}",
        r"\toprule",
        r"$d_1$ (m), $\theta$ ($^\circ$) & $n$ & Bias & SD & RMSE & max$|e|$ & Curve \\",
        r"\midrule",
    ]
    groups: dict[str, list[dict[str, str]]] = {}
    for r in points:
        groups.setdefault(r["panel"], []).append(r)
    rows = [(groups[p], None) for p in sorted(groups)] + [(points, "all")]
    for i, (rs, label) in enumerate(rows):
        st = err_stats([float(r["measured_dbm"]) - float(r["engine_dbm"]) for r in rs])
        curve = max(abs(float(r["engine_minus_published_db"])) for r in rs)
        if label is None:
            geo = {(float(r["d1_m"]), float(r["theta_deg"])) for r in rs}
            if len(geo) != 1:
                raise SystemExit("gen_results: a V-TANG panel mixes geometries")
            d1, th = geo.pop()
            label = f"{dec(d1, 0)}, {dec(th, 0)}"
        if i == len(rows) - 1:
            lines.append(r"\midrule")
        lines.append(f"{label} & {st['n']} & {dec(st['bias'], 2)} & {dec(st['sd'], 2)} & "
                     f"{dec(st['rmse'], 2)} & {dec(st['max_abs'], 2)} & "
                     f"{dec(curve, 3, mode=ROUND_CEILING)} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- Clarke reference
def bessel_j0(x: float) -> float:
    """J0 from its power series (accurate to double precision for 0 <= x <= 3)."""
    term, total, k = 1.0, 1.0, 0
    while abs(term) > 1e-18:
        k += 1
        term *= -(x * x / 4.0) / (k * k)
        total += term
    return total


def bisect_root(f: Any, lo: float, hi: float) -> float:
    if f(lo) * f(hi) > 0:
        raise SystemExit("gen_results: Clarke reference root not bracketed")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------- V-LAB push log
def push_log_facts(paper_root: Path, spec: dict[str, Any], run_name: str,
                   run_commit: str) -> dict[str, Any]:
    """Check the fixed V-LAB lock facts of numbers.yaml (source data:V-LAB-provenance) against
    GitHub's push log and the order lock push < analysis-code push < unblinded run < result
    push. Returns the facts for the provenance record."""
    log = read_json(paper_root / LAB_PUSH_LOG)
    pushes = {x["after"][:7]: x["timestamp"] for x in log if x.get("activity_type") == "push"}
    fixed = {k: m.value for k, m in spec.items() if m.source_id == "V-LAB-provenance"}
    need = ("txtLabLockCommit", "txtLabLockDate", "txtLabCodeCommit", "txtLabCodePushTime",
            "txtLabResultCommit", "txtLabResultPushTime")
    if any(fixed.get(k) is None for k in need):
        raise SystemExit("gen_results: numbers.yaml lacks a fixed V-LAB-provenance fact")
    when = {k: pushes.get(fixed[k]) for k in ("txtLabLockCommit", "txtLabCodeCommit",
                                              "txtLabResultCommit")}
    if None in when.values():
        raise SystemExit(f"gen_results: a V-LAB commit has no push in {LAB_PUSH_LOG}: {when}")

    def clock(ts: str) -> str:
        return ts[11:19] + " UTC"

    lock = dt_parse(when["txtLabLockCommit"])
    if fixed["txtLabLockDate"] != f"{lock.day} {lock:%b %Y}, {clock(when['txtLabLockCommit'])}":
        raise SystemExit("gen_results: txtLabLockDate differs from the push log")
    if fixed["txtLabCodePushTime"] != clock(when["txtLabCodeCommit"]):
        raise SystemExit("gen_results: txtLabCodePushTime differs from the push log")
    if fixed["txtLabResultPushTime"] != clock(when["txtLabResultCommit"]):
        raise SystemExit("gen_results: txtLabResultPushTime differs from the push log")
    if not run_commit.startswith(fixed["txtLabCodeCommit"]):
        raise SystemExit("gen_results: the unblinded V-LAB run is not at txtLabCodeCommit")
    m = re.match(r"(\d{8}T\d{6}Z)_", run_name)
    if not m:
        raise SystemExit(f"gen_results: V-LAB run name {run_name!r} has no start time")
    run = dt_parse(m.group(1))
    order = [lock, dt_parse(when["txtLabCodeCommit"]), run, dt_parse(when["txtLabResultCommit"])]
    if order != sorted(order) or len(set(order)) != len(order):
        raise SystemExit("gen_results: Section V-D says lock push < code push < run < result "
                         f"push; the records give {order}")
    return {"run_time": f"{run:%H:%M:%S} UTC", "pushes": when, "push_log": LAB_PUSH_LOG,
            "lock_to_run_s": (run - lock).total_seconds()}


def dt_parse(s: str) -> Any:
    """UTC time from '2026-10-11T00:22:55Z' or a run-directory stamp '20261011T003044Z'."""
    from datetime import UTC, datetime

    fmt = "%Y%m%dT%H%M%SZ" if re.fullmatch(r"\d{8}T\d{6}Z", s) else "%Y-%m-%dT%H:%M:%SZ"
    return datetime.strptime(s, fmt).replace(tzinfo=UTC)


# --------------------------------------------------------------------------- availability
AVAILABILITY_MACROS = {"licence": "txtLicence", "repo_url": "txtRepoUrl",
                       "archive_doi": "txtArchiveDoi", "data_doi": "txtDataDoi"}


AVAILABILITY_STATUS = ("conditional", "released")
UNRELEASED_MACRO = r"\sremuUnreleased"  # sremu-macros.tex: stops any build that prints it


def availability_values(paper_root: Path) -> dict[str, str]:
    """Availability macros from availability.yaml.

    status conditional: the section prints the conditional statement (release upon the
    authors' approval, on request until then) and no licence, URL or DOI, so every macro
    expands to ``\\sremuUnreleased`` (not a placeholder: a final build is not blocked, but a
    build that prints one stops). status released: filled fields become their macros, and
    PENDING fields stay placeholders (a final build refuses)."""
    doc = read_yaml(paper_root / "availability.yaml") or {}
    status = str(doc.get("status", "")).strip()
    if status not in AVAILABILITY_STATUS:
        raise SystemExit(f"gen_results: availability.yaml status {status!r} is not one of "
                         f"{AVAILABILITY_STATUS}")
    out: dict[str, str] = {}
    for key, macro in AVAILABILITY_MACROS.items():
        if key not in doc:
            raise SystemExit(f"gen_results: availability.yaml lacks {key}")
        if status == "conditional":
            out[macro] = UNRELEASED_MACRO
            continue
        val = str(doc[key]).strip()
        if not val or val.upper() == "PENDING":
            continue
        if key == "repo_url":
            out[macro] = "\\url{" + val + "}"
        else:
            out[macro] = re.sub(r"([_%&#])", r"\\\1", val)
    return out


# --------------------------------------------------------------------------- provenance table
def _tt(s: str) -> str:
    """Typewriter record name that may break after '_' and '/' in a narrow column."""
    return (r"\texttt{" + s.replace("_", r"\_\allowbreak{}").replace("/", r"/\allowbreak{}")
            + "}")


def _tree(dirty: Any) -> str:
    return {True: "dirty", False: "clean"}.get(dirty, "n/r")


def _host(platform: str | None, instance: str | None = None) -> str:
    if instance:
        return f"AWS {instance}"
    if platform and platform.startswith("Windows"):
        return "laptop (Windows)"
    if platform and platform.startswith("Linux"):
        return "Linux"
    return "n/r"


def _join(values: list[str]) -> str:
    return "; ".join(dict.fromkeys(values))


def _aws_type(aws: object) -> str | None:
    """Instance type of a sysinfo ``aws_instance`` record: a dict with ``type`` (older records)
    or the string ``"type='c7i.48xlarge' id='i-...'"`` (current sysinfo); None when absent."""
    if not aws:
        return None
    if isinstance(aws, dict):
        t = aws.get("type")
        return str(t) if t else None
    m = re.search(r"type='([^']+)'", str(aws))
    return m.group(1) if m else str(aws)


def provenance_rows(paper_root: Path, sremu_root: Path) -> list[dict[str, str]]:
    """Producing commit, tree state, host, date and record of every result the paper quotes,
    read from the result files' own records (MAJOR-8). Missing fields print as n/r."""
    sw = paper_root / "sweeps" / "results"
    rows: list[dict[str, str]] = []

    def add(result: str, used: str, commit: str | None, tree: str, host: str, date: str | None,
            record: str) -> None:
        rows.append({"result": result, "used": used, "commit": (commit or "n/r")[:7],
                     "tree": tree, "host": host, "date": (date or "n/r")[:10], "record": record})

    jmeta: dict[str, Any] = {}
    for p in sorted((paper_root / "generated" / "junit").glob("*.xml")):
        side = p.with_suffix(".meta.json")
        meta = read_json(side) if side.is_file() else {}
        jmeta = jmeta or meta
        suite = next(ET.parse(p).getroot().iter("testsuite"), None)
        ts = None if suite is None else suite.get("timestamp")
        add("Test record", r"Table~\ref{tab:vv}", meta.get("sremu_commit"),
            _tree(meta.get("sremu_dirty")), str(meta.get("host") or "n/r"), ts, _tt(p.name))

    # The regeneration job's own record: the E-B arc run of that job names the AWS instance
    # (sweeps/results/E-B-arc/meta.json, run.aws_run_id and host.instance_type).
    arc_meta = read_json(sw / "E-B-arc" / "meta.json")

    def job_host(m: dict[str, Any]) -> str | None:
        """The validation records name the platform, not the instance. A record made in the
        pinned regeneration job (the job's git-state file _regen/git_state.json, the commit of
        that job's E-B arc record, AWS kernel) ran on the host that the job's E-B arc record
        names."""
        same = ("/_regen/git_state.json" in str(m.get("git_state_source") or "")
                and m.get("sremu_commit") == arc_meta["sremu"].get("commit")
                and arc_meta["run"].get("aws_run_id")
                and "-aws-" in str(m.get("platform") or ""))
        host = (arc_meta.get("host") or {}).get("instance_type")
        return f"AWS {host}" if same and host else None

    for name, used, run in (("V-TANG", r"Fig.~\ref{fig:F3}(b), Table~\ref{tab:tang}", TANG_RUN),
                            ("V-CFO", r"Fig.~\ref{fig:F5}(a, b)", CFO_RUN),
                            ("V-FM (E-D)", r"Fig.~\ref{fig:F12}", FM_RUN)):
        m = read_json(sremu_root / run / "meta.json")
        add(name, used, m.get("sremu_commit"), _tree(m.get("sremu_dirty")),
            job_host(m) or _host(m.get("platform")), m.get("t_start_utc"), _tt(Path(run).name))
        if job_host(m):
            rows[-1]["host_source"] = "job"  # named in the caption (not the record's own field)
    lab = read_json(sremu_root / LAB_RUN / "meta.json")
    labm = read_json(sremu_root / LAB_RUN / "manifest.json")
    add("V-LAB", r"Table~\ref{tab:lab}, Fig.~\ref{fig:F4}", lab.get("git_commit"),
        _tree(lab.get("git_dirty")), _host(labm.get("platform")), labm.get("t_start_utc"),
        _tt(Path(LAB_RUN).name))
    # The decoy power analysis that the unblinded run quotes ran before the lock, at its own
    # commit (MAJOR-8: "V-LAB 7a63287 clean, predictions 9dca79f dirty"); its host is not in
    # the record.
    lab_pa = read_json(sremu_root / LAB_RUN / "results.json")["power_analysis"]
    add("V-LAB power analysis", r"Section~\ref{sec:vv-lab}", lab_pa.get("sremu_commit"),
        _tree(lab_pa.get("sremu_dirty")), "n/r", lab_pa.get("computed_utc"),
        _tt(Path(str(lab_pa.get("run_directory", "n/r")).replace("\\", "/")).name))
    for name, used, key in (("F3(a) values", r"Fig.~\ref{fig:F3}(a)", "F3"),
                            ("F6 values", r"Fig.~\ref{fig:F6}", "F6")):
        m = read_json(paper_root / "figures" / "data" / f"{key}.meta.json")
        add(name, used, m.get("sremu_commit"), _tree(m.get("sremu_dirty")),
            _host(m.get("platform")), m.get("generated_utc"),
            _tt(f"figures/data/{key}.meta.json"))
    m = read_json(sw / "sensitivity_cellmodel" / "meta.json")
    add("Cell-model sensitivity", r"Section~\ref{sec:model-cells}, Appendix~\ref{app:model}",
        m.get("sremu_commit"), _tree(m.get("sremu_dirty")), _host(m.get("platform")),
        m.get("t_start_utc"), _tt("sweeps/results/sensitivity_cellmodel"))
    for name, used, rel in (("E-A", r"Fig.~\ref{fig:F10}(a, b)", "E-A"),
                            ("E-B, lab-like", r"Fig.~\ref{fig:F9}(c, d)", "E-B1"),
                            ("E-C", r"Table~\ref{tab:ablation}", "E-C"),
                            ("BER", r"Fig.~\ref{fig:F5}(c)", "E-F/ber")):
        man = read_json(sw / rel / "manifest.json")
        aws = man.get("aws_instance") or {}
        add(name + " (aggregate)", used, man.get("git_commit"), _tree(man.get("git_dirty")),
            _host(man.get("platform"), _aws_type(aws)),
            man.get("t_start_utc"), _tt("sweeps/results/" + rel))
    va1 = read_csv(sw / "E-A-IQ" / "va1.csv")
    va1m = read_json(sw / "E-A-IQ" / "meta.json")
    add("VA-1 (rows)", r"Fig.~\ref{fig:F10}(c)",
        None, "", "", va1m.get("written_utc"), _tt("sweeps/results/E-A-IQ"))
    rows[-1].update({
        "commit": _join([(r["git_commit"] or "n/r")[:7] for r in va1]),
        "tree": _join([_tree({"True": True, "False": False}.get(r["git_dirty"]))
                       for r in va1]),
        "host": _join([_host(r["platform"], r["aws_instance_type"] or None) for r in va1])})
    arc = read_json(sw / "E-B-arc" / "meta.json")
    add("E-B, arc geometry", r"Fig.~\ref{fig:F9}(a, b)", arc["sremu"].get("commit"),
        _tree(arc["sremu"].get("dirty")), _host(None, arc["host"].get("instance_type")),
        arc["run"].get("t_start_utc"), _tt(arc["run"]["run_id"]))
    efm = read_json(sw / "E-F" / "latency" / "manifest.json")
    add("E-F latency", r"Section~\ref{sec:wc-data}", efm.get("git_commit"),
        _tree(efm.get("git_dirty")),
        _host(efm.get("platform"), _aws_type(efm.get("aws_instance") or {})),
        efm.get("t_start_utc"), _tt("sweeps/results/E-F/latency"))
    bench = sw / "bench"
    # B1 (cost model, isolation reference) and B6 (cross-tool context) are quoted only in
    # Appendix E; the B4 lockstep part is quoted in Section VI-C (control loop)
    for b, used in (("B1", r"Appendix~\ref{app:bench}"),
                    ("B2", r"Fig.~\ref{fig:F7}(a--e)"),
                    ("B2-F45", r"Fig.~\ref{fig:F7}(f), Section~\ref{sec:wc-data}"),
                    ("B3", r"Fig.~\ref{fig:F8}(a, b)"),
                    ("B4", r"Fig.~\ref{fig:F8}(c, d), Section~\ref{sec:wc-loop}"),
                    ("B4-classic", r"Appendix~\ref{app:bench}"),
                    ("B6", r"Appendix~\ref{app:bench}")):
        m = read_json(bench / b / "meta.json")
        run = m.get("run") or (m.get("runs") or {}).get("paced") or {}
        host = m.get("host") or {}
        tree = _tree(m["sremu"].get("dirty_flag_recorded"))
        if tree == "dirty" and "byte-identical" in str(m["sremu"].get("dirty_note", "")):
            tree = "flagged"  # dirty flag recorded, bundle file tree = the commit's tree
        add(b, used, m["sremu"].get("commit"), tree,
            "laptop (Windows)" if host.get("kind") == "laptop"
            else _host(None, host.get("instance_type")),
            run.get("t_start_utc"), _tt(run.get("run_id", b)))
    for rel, name, used in (("E-E", "E-E (GNU Radio)", r"Table~\ref{tab:ee}"),
                            ("E-E-laptop", "E-E throughput", r"Section~\ref{sec:cases-ee}")):
        s = read_json(sw / rel / "summary.json")
        g, h = s.get("git") or {}, s.get("host") or {}
        aws = h.get("aws_instance") or {}
        plat = str(h.get("platform") or "")
        host = (f"AWS {_aws_type(aws)}" if _aws_type(aws) else
                "laptop (WSL2)" if "microsoft" in plat.lower() else _host(plat or None))
        man = next(iter(sorted((sw / rel / "runs").rglob("manifest.json"))), None)
        add(name, used, g.get("commit"), _tree(g.get("dirty")), host,
            None if man is None else read_json(man).get("t_start_utc"),
            _tt("sweeps/results/" + rel))
    return rows


def render_provenance_table(rows: list[dict[str, str]]) -> str:
    inferred = [r["result"] for r in rows if r.get("host_source") == "job"]
    host_note = ("" if not inferred else
                 " " + (", ".join(inferred[:-1]) + " and " + inferred[-1] if len(inferred) > 1
                        else inferred[0])
                 + " record the platform, not the instance; their host is that of the"
                 r" regeneration job whose commit and git-state file they record.")
    lines = [
        "% GENERATED by paper/tools/gen_results.py from the result files' own records",
        "% (meta.json, manifest.json, va1.csv, junit sidecar). Do not edit.",
        r"\begin{table*}[t]",
        r"\caption{Provenance of the results quoted in this paper, as the result files record"
        r" it: emulator commit, state of its working tree, host, date (UTC) of the run, and the"
        r" record (sremu \texttt{validation/*/results}, paper \texttt{sweeps/results} or"
        r" \texttt{figures/data}). For an aggregate the commit is the one its aggregation step"
        r" recorded; its point runs are listed in its own provenance. Flagged: the run recorded"
        r" a dirty flag, but its uploaded file tree equals the commit's. n/r: not recorded."
        + host_note +
        r" The V-LAB analysis is not rerun by design (its plan is locked); Appendix~\ref{app:repro}"
        r" explains which results come from the regeneration job and which keep their own"
        r" commit.}",
        r"\label{tab:provenance}",
        r"\centering",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{2.5pt}",
        r"\renewcommand{\arraystretch}{1.1}",
        r"\begin{tabular}{@{}>{\raggedright\arraybackslash}p{1.0in}"
        r">{\raggedright\arraybackslash}p{1.0in}>{\raggedright\arraybackslash}p{0.72in}"
        r">{\raggedright\arraybackslash}p{0.5in}>{\raggedright\arraybackslash}p{1.05in}"
        r">{\raggedright\arraybackslash}p{0.62in}>{\raggedright\arraybackslash}p{1.6in}@{}}",
        r"\toprule",
        r"Result & Used in & Commit & Tree & Host & Date & Record \\",
        r"\midrule",
    ]
    for r in rows:
        lines.append(" & ".join([r["result"], r["used"], r["commit"], r["tree"], r["host"],
                                 r["date"], r["record"]]) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table*}"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- E-E (GNU Radio)
def _ee_label(cfg: str) -> str:
    if cfg.startswith("focusq:"):
        return rf"\quad $q={dec(float(cfg.split(':', 1)[1]), 2)}$"
    return {"focus": "Focusing", "uniform": "Uniform", "random": "Random"}[cfg]


def _ee_rate(x: float) -> str:
    """An error rate: 0, a x 10^b below 0.1, two decimals above."""
    return "0" if x == 0 else (sci(x) if x < 0.1 else dec(x, 2))


def render_table_ee(rows: list[dict[str, Any]]) -> str:
    n_sym = {int(r["symbols"]) for r in rows}
    n_blk = {int(r["blocks"]) for r in rows}
    if len(n_sym) != 1 or len(n_blk) != 1:
        raise SystemExit("gen_results: E-E caption gives one symbol and block count per row")
    lines = [
        "% GENERATED by paper/tools/gen_results.py from paper/sweeps/results/E-E/summary.json.",
        "% Do not edit.",
        r"\begin{table}[t]",
        r"\caption{GNU Radio DQPSK modem of stock blocks through the raw request--reply mode"
        r" (E-E), one fresh run per RIS configuration: focusing, focusing with a seeded"
        r" fraction $q$ of its cells inverted, uniform (every cell in state 0) and random. AF:"
        r" isotropic array-factor prediction relative to focusing; gain: least-squares gain of"
        r" the IQ that GNU Radio received, relative to focusing (AF against gain is an internal"
        r" consistency check, not a measurement); SNR: estimated from the received IQ over the"
        r" sample bandwidth; SER: GNU Radio's symbol decisions,"
        rf" {integer(n_sym.pop())} scored per configuration; BLER: fraction of the"
        rf" {integer(n_blk.pop())} uncoded 200-bit blocks (100 consecutive decisions, no"
        r" coding, no CRC) with an error. Data classes: SIM-sremu (emulated IQ and the decisions"
        r" on it), ANALYTIC (AF).}",
        r"\label{tab:ee}",
        r"\centering",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{4pt}",
        r"\begin{tabular}{@{}lrrrrr@{}}",
        r"\toprule",
        r"RIS & AF & Gain & SNR & SER & BLER \\",
        r"configuration & (dB) & (dB) & (dB) & & \\",
        r"\midrule",
    ]
    for r in rows:
        lines.append(" & ".join([_ee_label(r["config"]), dec(r["af_pred_db"], 2),
                                 dec(r["gain_rel_focus_db"], 2), dec(r["ls_snr_db"], 1),
                                 _ee_rate(r["ser"]), _ee_rate(r["bler_uncoded_200bit"])])
                     + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def ee_values(paper_root: Path) -> tuple[dict[str, str], dict[str, Any], str]:
    """E-E macros, provenance and table from sweeps/results/E-E (results; SIM-sremu) and
    sweeps/results/E-E-laptop (laptop throughput; MEAS). The AWS throughput macros are set
    only when sweeps/results/E-E itself comes from an AWS host (else they stay placeholders).
    Raises SystemExit when a statement of the text does not hold for the files."""
    sw = paper_root / "sweeps" / "results"
    ee = read_json(sw / "E-E" / "summary.json")
    chk = ee["checks"]
    for k in ("all_ok", "all_complete", "all_counters_zero", "all_rx_equal_emulator_record",
              "all_commands_applied_lateness0", "bitwise_identical_across_repetitions"):
        if not chk[k]:
            raise SystemExit(f"gen_results: E-E check {k} failed: {chk.get('problems')}")
    v: dict[str, str] = {}
    d, scene = ee["design"], ee["design"]["scene"]
    v["txtEeGrVersion"] = str(ee["gnuradio_version"])
    v["numEeSps"] = str(int(d["sps"]))
    v["numEeRateMsps"] = dec(float(d["rate_sps"]) / 1e6, 0)
    ris = scene["ris"][0]
    v["numEeCells"] = str(int(ris["grid"]["n_u"]) * int(ris["grid"]["n_v"]))
    tx = next(n for n in scene["nodes"] if n["id"] == "gr")
    rx = next(n for n in scene["nodes"] if n["id"] == "grrx")
    v["numEeFcGHz"] = dec(float(tx["streams"][0]["fc_hz"]) / 1e9, 1)
    dists = [math.dist(n["position_m"], ris["pose"]["center_m"]) for n in (tx, rx)]
    if abs(dists[0] - dists[1]) > 1e-6:
        raise SystemExit("gen_results: E-E text gives one TX/RX distance; they differ")
    v["numEeDistM"] = dec(dists[0], 0)
    v["numEeWallDb"] = dec(float(scene["environment"]["occluders"][0]["transmission_loss_db"]),
                           0)
    v["numEeStaticS"] = str(d["static_duration_s"])
    v["numEeSegmentS"] = str(d["segment_s"])
    rows = ee["static"]
    v["numEeConfigs"] = str(len(rows))
    if rows[0]["config"] != "focus":
        raise SystemExit("gen_results: E-E rows must start with focusing")
    gains = [r["gain_rel_focus_db"] for r in rows]
    if gains != sorted(gains, reverse=True):
        raise SystemExit("gen_results: E-E rows are not ordered by gain")
    for key in ("ser", "bler_uncoded_200bit"):  # the text: they never fall as the gain falls
        vals = [r[key] for r in rows]
        if vals != sorted(vals):
            raise SystemExit(f"gen_results: E-E {key} is not monotone in the gain")
    if rows[0]["ser"] != 0.0 or rows[-1]["bler_uncoded_200bit"] != 1.0:
        raise SystemExit("gen_results: E-E text says focusing is error-free and random loses "
                         "every block")
    v["numEeAfMaxDiffDb"] = dec(max(abs(r["gain_rel_focus_db"] - r["af_pred_db"]) for r in rows),
                                1, mode=ROUND_CEILING)
    # implementation loss only where it is measured: >= 100 symbol errors with the link up
    # (SER below 0.1); the lost-link rows are described in words
    loss = [r["implied_loss_db"] for r in rows
            if r["implied_loss_db"] is not None and r["ser"] < 0.1]
    if len(loss) < 2:
        raise SystemExit("gen_results: E-E text quotes an implementation loss range")
    v["numEeLossMinDb"] = dec(min(loss), 2)
    v["numEeLossMaxDb"] = dec(max(loss), 2)
    lost = [r["implied_loss_db"] for r in rows if r["ser"] >= 0.1]
    if not lost or min(lost) <= max(loss):
        raise SystemExit("gen_results: E-E text says the loss grows once the link is lost")
    cps = ee["switching"]["change_points"]
    offs = {abs(int(c["offset"])) for c in cps}
    if len(cps) != 3 or offs != {0}:
        raise SystemExit(f"gen_results: E-E text says three switches at offset 0: {cps}")
    v["numEeCpOffsetMax"] = str(max(offs))
    v["numEeRuns"] = str(int(ee["runs_per_repetition"]))
    v["txtEeReps"] = prose_count(len(ee["repetitions"]))
    aws = (ee.get("host") or {}).get("aws_instance")
    tp = {float(r["rate_sps"]): r for r in ee.get("throughput") or []}
    if aws and float(d["rate_sps"]) in tp:
        v["numEeAwsRtfLo"] = dec(tp[float(d["rate_sps"])]["sremu_rtf_median"], 2)
        v["txtEeAwsInstance"] = str(_aws_type(aws))
    # laptop: every sweep (single run per rate) counts; the text quotes the range per rate.
    # While sweeps/results/E-E itself comes from that laptop, its throughput runs are one more
    # sweep: the quoted range must not leave out laptop runs that the result files hold (unless
    # E-E-laptop already holds them as a sweep of the same run root).
    lap = read_json(sw / "E-E-laptop" / "summary.json")
    sweeps = [s["throughput"] for s in lap["sweeps"]]
    ee_root = read_json(sw / "E-E" / "meta.json").get("root")
    if (not aws and tp and (ee.get("host") or {}).get("cpu_model") == lap["host"]["cpu_model"]
            and ee_root not in {s.get("root") for s in lap["sweeps"]}):
        sweeps.append(list(tp.values()))
    per_rate: dict[float, list[float]] = {}
    for sweep in lap["sweeps"]:
        if sweep["host"]["cpu_model"] != lap["host"]["cpu_model"]:
            raise SystemExit("gen_results: E-E laptop sweeps come from different hosts")
    for rows_tp in sweeps:
        for r in rows_tp:
            if not r["ok"]:
                raise SystemExit("gen_results: E-E laptop throughput run not ok")
            per_rate.setdefault(float(r["rate_sps"]), []).append(float(r["sremu_rtf_median"]))
    rates = sorted(per_rate)
    if (not lap["checks"]["all_ok"] or rates[0] != float(d["rate_sps"])
            or any(len(per_rate[x]) != len(sweeps) for x in rates)):
        raise SystemExit("gen_results: E-E laptop throughput incomplete")
    v["txtEeLaptopCpu"] = str(lap["host"]["cpu_model"]).split(" w/ ")[0]
    v["txtEeLaptopSweeps"] = prose_count(len(sweeps))
    v["numEeLaptopRtfLoMin"] = dec(min(per_rate[rates[0]]), 2)
    v["numEeLaptopRtfLoMax"] = dec(max(per_rate[rates[0]]), 2)
    v["numEeLaptopRtfHiMin"] = dec(min(per_rate[rates[-1]]), 2)
    v["numEeLaptopRtfHiMax"] = dec(max(per_rate[rates[-1]]), 2)
    v["numEeLaptopRateHiMsps"] = dec(rates[-1] / 1e6, 2)
    prov = {"git": ee.get("git", {}).get("commit"), "git_reason": ee.get("git", {}).get("reason"),
            "note": ee.get("note"), "host": (ee.get("host") or {}).get("platform"),
            "laptop_host": (lap.get("host") or {}).get("cpu_model"),
            "laptop_sweeps": len(sweeps), "laptop_sweeps_include_ee_run": len(sweeps) > len(
                lap["sweeps"])}
    return v, prov, render_table_ee(rows)


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
    # Appendix G: every cited identifier with property, oracle and tolerance (tools/vv_ids.py),
    # and the VA-4 tolerance of Section III-B, read from its test
    from vv_ids import ids_tables, provenance_values

    jcommit = str(next((m.get("sremu_commit") for m in js["meta"].values()), "unknown"))
    idv, idt = ids_tables(paper_root, sremu_root, load_junit(
        sorted((paper_root / "generated" / "junit").glob("*.xml"))), jcommit)
    v.update(idv)
    tables.update(idt)

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
    # Accuracy against the chamber measurements (author decision D-12): measured - emulator.
    st = err_stats([float(r["measured_dbm"]) - float(r["engine_dbm"]) for r in tang])
    ref = summ["vtang2_measured_minus_engine"]
    if any(abs(st[k] - float(ref[k])) > 1e-9 for k in ("bias", "sd", "rmse", "max_abs")) or \
            st["n"] != int(ref["n"]):
        raise SystemExit("gen_results: V-TANG statistics differ from vtang_summary.json")
    if min(float(r["measured_dbm"]) - float(r["engine_dbm"]) for r in tang) <= 0:
        raise SystemExit("gen_results: Section V says every measured point lies above the "
                         "emulator (bias printed without sign)")
    v["numTangMeasBiasDb"] = dec(st["bias"], 2)
    v["numTangMeasSdDb"] = dec(st["sd"], 2)
    v["numTangMeasRmseDb"] = dec(st["rmse"], 2)
    v["numTangMeasMaxDb"] = dec(st["max_abs"], 2)
    v["numTangMeasN"] = str(st["n"])
    panel_bias = [err_stats([float(r["measured_dbm"]) - float(r["engine_dbm"]) for r in tang
                             if r["panel"] == p])["bias"]
                  for p in sorted({r["panel"] for r in tang})]
    v["numTangPanelBiasMinDb"] = dec(min(panel_bias), 2)
    v["numTangPanelBiasMaxDb"] = dec(max(panel_bias), 2)
    tscen = read_yaml(sremu_root / TANG_RUN / "scenario.resolved.yaml")
    tfc = {float(s["fc_hz"]) for n in tscen["nodes"] for s in n.get("streams") or []}
    if len(tfc) != 1:
        raise SystemExit(f"gen_results: V-TANG carriers {tfc} not unique")
    v["numTangFcGHz"] = dec(tfc.pop() / 1e9, 0)
    # Tang et al.'s own residual: measured minus their published Eq. 9 curve, the exact mean
    # of the two-decimal digitized values (half-even, as vtang_summary.json). K1 equals that
    # model for one point per cell, so the emulator's offset is essentially this one.
    pub = [Decimal(r["measured_dbm"]) - Decimal(r["published_eq9_dbm"]) for r in tang]
    pub_bias = sum(pub, Decimal(0)) / len(pub)
    if abs(float(pub_bias) - float(summ["vtang2_measured_minus_published"]["bias"])) > 1e-9:
        raise SystemExit("gen_results: measured - published bias differs from vtang_summary.json")
    if min(pub) <= 0:
        raise SystemExit("gen_results: Section V says the published model lies below every point")
    v["numTangPubMeasBiasDb"] = dec(pub_bias, 2)
    # the reported reflection amplitude of the coding state used (Tang Table III, REPORTED)
    tpar = read_json(sremu_root / "data" / "validation" / "tang2022" / "tang2022_parameters.json")
    if str(tpar["ris"]["coding_state_used"]) != "0":
        raise SystemExit("gen_results: V-TANG coding state is not state 0")
    tg0 = float(tpar["ris"]["gamma_state_0"]["mag"])
    v["numTangGammaMag"] = dec(tg0, 1)
    v["numTangGammaLossDb"] = dec(-20.0 * math.log10(tg0), 2)
    tables["tableA_tang.tex"] = render_tang_table(tang)
    prov["V-TANG"] = {"run": TANG_RUN, **{k: read_json(sremu_root / TANG_RUN / "meta.json")[k]
                                          for k in ("sremu_commit", "sremu_dirty")},
                      "measured_minus_emulator": st}

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
    sp = res["primary"]["spearman"]
    if abs(sp["rho"] - d["C2"]["spearman_rho"]) > 1e-12:
        raise SystemExit("gen_results: V-LAB Spearman rho differs between records")
    v["numLabSpearmanLo"] = dec(sp["ci_low"], 2)
    v["numLabSpearmanHi"] = dec(sp["ci_high"], 2)
    sig_key = f"sigma_rep_{float(proto['power_analysis']['sigma_rep_db']):g}"
    v["numLabDecoyDeltaMtwoDb"] = dec(pa["median_over_decoys"][f"M2/{sig_key}"]
                                      ["c2_median_delta_db"], 2)
    v["numLabDecoyDeltaMzeroDb"] = dec(pa["median_over_decoys"][f"M0/{sig_key}"]
                                       ["c2_median_delta_db"], 2)
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
    if meta["git_dirty"] is not False:
        raise SystemExit("gen_results: Section V-D says the unblinded V-LAB run had a clean tree")
    spec = {m.macro: m for m in load_numbers_spec(paper_root / "numbers.yaml")}
    lock = push_log_facts(paper_root, spec, Path(LAB_RUN).name, meta["git_commit"])
    v["txtLabRunTime"] = lock["run_time"]
    prov["V-LAB"] = {"run": LAB_RUN, "git_commit": meta["git_commit"],
                     "git_dirty": meta["git_dirty"], "decisions": meta["decisions"],
                     "protocol_digest": meta["protocol_digest"], "lock": lock}
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
    # running-mean window of the EVM traces of F9(a), (c), as the figure script recorded it
    f9 = read_json(paper_root / "figures" / "data" / "F9.meta.json")
    v["numEbEvmSmoothSymbols"] = prose_count(int(f9["panel_ac_smoothing_symbols"]))
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
    # The LOS blocker acts on the LOS only (occluders never attenuate a RIS hop), although it
    # crosses most Tx -> cell segments; the driver records the crossings per blocked point.
    blk_rec = {(r["result.design.blocker.n_cells"],
                r["result.design.blocker.tx_cell_segments_crossed"],
                r["result.design.blocker.cell_rx_segments_crossed.rx1.rx0"],
                r["result.design.blocker.cell_rx_segments_crossed.rx2.rx0"],
                r["result.design.blocker.los_crossed.rx1.rx0"],
                r["result.design.blocker.los_crossed.rx2.rx0"])
               for r in read_csv(arc / "sweeps" / "eb1" / "index.csv")
               if r["param.los"] == "blocked"}
    if len(blk_rec) != 1:
        raise SystemExit("gen_results: E-B arc blocker crossings differ between points")
    b_cells, b_tx, b_rx1, b_rx2, b_los1, b_los2 = blk_rec.pop()
    if (b_los1, b_los2) != ("True", "True") or int(b_cells) != nu * nv * len(arc_scen["ris"]):
        raise SystemExit("gen_results: the E-B arc blocker record does not match the scenario")
    if (int(b_rx1), int(b_rx2)) != (0, 0) or not 0 < int(b_tx) < int(b_cells):
        raise SystemExit("gen_results: Section VII says the blocker crosses part of the "
                         "Tx -> cell segments and no cell -> RX segment")
    v["numEbArcBlockerCrossed"] = str(int(b_tx))
    v["numEbArcBlockerCells"] = str(int(b_cells))

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
    # Timed commands whose lead exceeds the worst latency lose no packet: checked on the whole
    # guard slice of F9(b) (every alphabet, LOS case and Es/N0), not only on its two cases.
    # At the other guards a few Es/N0 = 15 dB rows lose one packet in 2000 to noise even with
    # timed commands on time, so the rule is checked at the guard that F9(b) shows. (The
    # abstract prints no phrase macro of E-B or E-A since mock-review must-fix 2.)
    slice_rows = [r for r in a2 if r["guard_s"] == guard_f9]
    for r in slice_rows:
        if (r["semantics"] == "timed" and Fraction(r["lead_s"]) > lat_fixed
                + Fraction(r["jitter_s"]) and int(r["packet_errors"]) != 0):
            raise SystemExit("gen_results: timed commands whose lead exceeds the latency must "
                             f"lose no packet; {r['alphabet']}/{r['los']}/"
                             f"{r['es_n0_target_db']} lead {r['lead_s']} J {r['jitter_s']} lost "
                             f"{r['packet_errors']}")
    if max(float(r["per"]) for r in slice_rows if r["semantics"] == "tick-based") \
            != max(tick_all):
        raise SystemExit("gen_results: the largest tick-based PER of the guard slice is not in "
                         "the F9(b) cases that Section VII-A quotes")
    # Timed vs asap in the two F9(b) cases (MAJOR-3), where a mistimed switch costs bits; the
    # asap guard rule and the timed lead rule were checked on every row above.
    asap_one = sorted(arc_per("asap", "1/1000", c) for c in cases)
    if asap_one[0] <= 0.0:
        raise SystemExit("gen_results: Section VII-A says asap commands lose packets at J = 1 ms")
    # closed form of the E-B2 timing and the full-grid table (tools/case_tables.py)
    case_tables.eb2_values(v, tables, a2, traffic=traffic, rate_sps=tx_stream["rate_sps"],
                           lat_fixed=lat_fixed, guards=sorted(guards), jits=jits,
                           guard_f9=guard_f9, lead_f9=lead_f9, es=(es_lo, es_hi),
                           n_packets=n_packets, dec=dec)

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
    v["numEaGainOpt"] = dec(inst["gain_db"], 2)  # mean of per-batch dB gains (pre-specified)
    v["numEaGainOptLo"] = dec(inst["gain_ci_low_db"], 2)
    v["numEaGainOptHi"] = dec(inst["gain_ci_high_db"], 2)
    v["numEaGainOptTwo"] = dec(gain(g2, "instant")["gain_db"], 2)
    v["numEaGainStale"] = dec(gain(g1, "stale")["gain_db"], 2)
    v["numEaGainUrap"] = dec(gain(g1, "urap", "1")["gain_db"], 2)
    v["numEaGainBandit"] = dec(gain(g1, "bandit", "1")["gain_db"], 2)
    v["numEaGainWclFast"] = dec(gain(g1, "wcl", "0.01")["gain_db"], 2)
    v["numEaGainAzero"] = dec(gain(g0, "instant")["gain_db"], 2)
    # A0 (no partition) is what a partition that also attenuated the AP -> RIS hop (which the
    # A1 partition crosses for every cell) would give: both paths would scale alike.
    a0 = gain(g0, "instant")
    if (a0["fc_hz"], a0["h_m"]) != (inst["fc_hz"], inst["h_m"]) or float(a0["loss_db"]) != 0.0:
        raise SystemExit("gen_results: E-A A0 is not A1 without the partition")
    v["numEaTauHalfAzero"] = dec(float(tau[g0]["tau_half_s"]), 3)
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
    plan = paper_root / "preregistration" / "E-A.md"  # the plan committed before the sweep
    plan_text = plan.read_text(encoding="utf-8")
    r1 = re.search(r"\*\*H-A1\.\*\*.*?lies in \[([\d.]+), ([\d.]+)\] s", plan_text, re.S)
    r2 = re.search(r"\*\*H-A2\.\*\*.*?in \[([\d.]+), ([\d.]+)\]", plan_text, re.S)
    if not (r1 and r2):
        raise SystemExit("gen_results: cannot read the H-A1/H-A2 ranges from E-A.md")
    if not (float(r1.group(1)) <= h1["ci_s"][0] <= h1["ci_s"][1] <= float(r1.group(2))
            and float(r2.group(1)) <= h2["ci"][0] <= h2["ci"][1] <= float(r2.group(2))):
        raise SystemExit("gen_results: an E-A interval is not inside its pre-set range")
    v["numEaHaOneLoS"], v["numEaHaOneHiS"] = r1.group(1), r1.group(2)
    v["numEaHaTwoLo"], v["numEaHaTwoHi"] = r2.group(1), r2.group(2)
    # over LF line ends: the blob committed in 71fa3f3 stores CRLF, the working copy LF; the
    # text is otherwise identical, and the digest must not depend on the checkout
    v["txtEaPlanSha"] = hashlib.sha256(plan.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:8]
    v["numEaRatioPred"] = dec(h2["prediction"], 3)
    v["numEaRatioOffsetPct"] = dec(100.0 * (h2["ratio"] / h2["prediction"] - 1.0), 0)
    h3 = hyp["H-A3"]["per_tau"]
    deltas = [x["delta_db"] for x in h3.values()]
    v["numEaBanditMinDb"] = dec(min(deltas), 2)
    v["numEaBanditMaxDb"] = dec(max(deltas), 2)
    decisions = {k: hyp[k]["decision"] for k in ("H-A1", "H-A2", "H-A3")}
    if set(decisions.values()) != {"supported"}:
        raise SystemExit(f"gen_results: Section VII text assumes H-A1..3 supported: {decisions}")
    # setup of E-A, read from the corridor template and the sweep specs (not typed)
    corr = read_yaml(sremu_root / "scenarios" / "studies" / "ea_corridor.yaml")
    mu = next(nd for nd in corr["nodes"] if nd["id"] == "mu")["trajectory"]
    length = float(mu["p1_m"][0]) - float(mu["p0_m"][0])
    speed = float(mu["speed_mps"])
    # tau_half in units of lambda/v at both carriers (MAJOR-2), and Clarke's isotropic
    # reference: field correlation J0(2 pi x) and envelope correlation ~J0^2 fall to one half
    # at x = tau v / lambda; the rule-of-thumb coherence time sqrt(9/(16 pi))/f_D.
    fc_ea = sorted({float(x) for x in read_yaml(paper_root / "sweeps" / "ea_tau.yaml")["zip"]
                    ["fc_hz"]})
    if abs(h2["prediction"] - fc_ea[0] / fc_ea[1]) > 1e-12:
        raise SystemExit("gen_results: the H-A2 prediction is not the wavelength ratio")
    v["numEaTauOverLambdaV"] = dec(h1["tau_half_s"] / (C0 / fc_ea[0] / speed), 3)
    v["numEaTauOverLambdaVTwo"] = dec(float(tau[g2]["tau_half_s"]) / (C0 / fc_ea[1] / speed), 3)
    x_field = bisect_root(lambda x: bessel_j0(x) - 0.5, 0.0, 2.4)
    x_env = bisect_root(lambda x: bessel_j0(x) ** 2 - 0.5, 0.0, 2.4)
    v["numClarkeFieldHalf"] = dec(x_field / (2 * math.pi), 2)
    v["numClarkeEnvHalf"] = dec(x_env / (2 * math.pi), 2)
    v["numCoherenceRuleLambdaV"] = dec(math.sqrt(9.0 / (16.0 * math.pi)), 2)
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
    # Section VII-B: the LOS partition crosses EVERY AP -> cell segment of every partitioned
    # E-A geometry (RIS line at y = h, cells at x = (n + 1/2) k, z = 0; D17), although sremu
    # applies it to the LOS only. Checked here on the template's partition polygon.
    occ = corr["environment"]["occluders"]
    if len(occ) != 1:
        raise SystemExit("gen_results: E-A template has not exactly one partition")
    poly = [tuple(float(c) for c in p) for p in occ[0]["vertices_m"]]

    def _sub(a: tuple, b: tuple) -> tuple:
        return tuple(x - y for x, y in zip(a, b, strict=True))

    def _cross(a: tuple, b: tuple) -> tuple:
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

    def _dot(a: tuple, b: tuple) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    nrm = _cross(_sub(poly[1], poly[0]), _sub(poly[2], poly[0]))

    def _hits(p: tuple, q: tuple) -> bool:  # segment p -> q through the convex polygon
        dp, dq = _dot(_sub(p, poly[0]), nrm), _dot(_sub(q, poly[0]), nrm)
        if dp * dq >= 0:
            return False
        x = tuple(a + dp / (dp - dq) * (b - a) for a, b in zip(p, q, strict=True))
        sides = [_dot(_cross(_sub(poly[(i + 1) % len(poly)], poly[i]), _sub(x, poly[i])), nrm)
                 for i in range(len(poly))]
        return all(s > 0 for s in sides) or all(s < 0 for s in sides)

    ap_pos = tuple(float(c) for c in ap)
    for fc_h in sorted({(float(r["fc_hz"]), float(r["h_m"])) for r in gains
                        if float(r["loss_db"]) > 0}):
        k_m = C0 / fc_h[0] / 4
        n_c = math.floor(length / k_m + 1e-9)
        hit = sum(_hits(ap_pos, ((n + 0.5) * k_m, fc_h[1], 0.0)) for n in range(n_c))
        if hit != n_c:
            raise SystemExit(f"gen_results: Section VII-B says the E-A partition crosses every "
                             f"AP -> cell segment; at {fc_h} it crosses {hit}/{n_c}")
    v["numEaTauBanditMinS"] = dec(min(float(k) for k in h3), 1)
    va1_meta = read_json(sw / "E-A-IQ" / "meta.json")
    tol = next(re.search(r"<= (\d+\.\d+) dB", a) for a in va1_meta["assumptions"]
               if re.search(r"<= (\d+\.\d+) dB", a))
    v["numVaOneTolDb"] = tol.group(1)
    va1 = read_csv(sw / "E-A-IQ" / "va1.csv")
    v["numVaOneRows"] = str(len(va1))
    v["numVaOneMaxDiffDb"] = dec(max(abs(float(r["diff_iq_minus_vectorized_mid_db"]))
                                     for r in va1), 3, mode=ROUND_CEILING)
    # the engine's batch path (start-of-step rule) against the same IQ runs: its bias is the
    # reason for Amendment 1 of the E-A plan (midpoint rule); Section VII-B states it
    left = [float(r["diff_iq_minus_run_batch_db"]) for r in va1]
    if max(left) > 0.0:
        raise SystemExit("gen_results: Section VII-B says the start-of-step batch path reads high")
    v["numVaOneLeftMaxDb"] = dec(max(abs(x) for x in left), 2)
    v["numVaOneDurS"] = integer(int(float(va1[0]["duration_s"])))
    if not all(r["accept"] == "True" for r in va1):
        raise SystemExit("gen_results: VA-1 row not accepted")
    # E-A evaluator, VA-1 coverage and the per-geometry table (tools/case_tables.py)
    case_tables.ea_values(v, tables, sw=sw, sremu_root=sremu_root, plan_text=plan_text,
                          va1=va1, tol_db=float(v["numVaOneTolDb"]), speed=speed,
                          primary=(g1, g2), c0=C0, dec=dec)
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

    # ---------------------------------------------------------------- VII: E-E (GNU Radio)
    eev, prov["E-E"], tables["table_ee_gnuradio.tex"] = ee_values(paper_root)
    v.update(eev)

    # ---------------------------------------------------------------- availability, implementation
    v.update(availability_values(paper_root))
    pyproj = tomllib.loads((sremu_root / "pyproject.toml").read_text(encoding="utf-8"))
    req = re.fullmatch(r">=\s*(\d+\.\d+)", str(pyproj["project"]["requires-python"]).strip())
    if not req:
        raise SystemExit("gen_results: cannot read sremu's requires-python")
    v["txtPythonReq"] = req.group(1)
    src = sorted((sremu_root / "src" / "sremu").rglob("*.py"))
    n_lines = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in src)
    v["numSrcKloc"] = str(round(n_lines / 1000))
    v["numSrcModules"] = str(len(src))
    prov["implementation"] = {"src_py_files": len(src), "src_lines": n_lines}

    # ---------------------------------------------------------------- IV-C: cell-model sensitivity
    # sweeps/results/sensitivity_cellmodel (point vs po_patch at the lab pitch, coefficient
    # level; written by the script stored there) and the V-LAB paired point-vs-patch record.
    cm_dir = sw / "sensitivity_cellmodel"
    cms = read_json(cm_dir / "results.json")
    thr = cms["threshold"]
    if thr["auto_resolves_to"] != "point" or not 0 < thr["margin_m"] < 1e-4:
        raise SystemExit("gen_results: Section IV-C says the lab pitch lies just below the "
                         "auto threshold")
    v["numLabPitchMm"] = dec(1e3 * thr["pitch_m"], 1)
    v["numLabPitchMarginMm"] = dec(1e3 * thr["margin_m"], 2)
    v["numLabPitchMarginPct"] = dec(100.0 * thr["margin_rel"], 2)
    cf = {(r["kind"], r["angle_deg"]): r for r in cms["cell_factor"]["rows"]}
    if any(abs(r["patch_minus_point_db"]) > 1e-3 or abs(r["patch_fine_minus_point_db"]) > 1e-3
           for (k, _), r in cf.items() if k == "specular"):
        raise SystemExit("gen_results: Section IV-C says point and po_patch agree at specular")
    if any(abs(r["patch_fine_minus_point_db"] - r["continuous_patch_factor_db"]) > 0.1
           for (k, _), r in cf.items() if k == "retro"):
        raise SystemExit("gen_results: the refined patch factor misses its continuous limit")
    ang_a, ang_b = 30.0, 45.0  # the two retro angles quoted in Section IV-C
    ra, rb = cf[("retro", ang_a)], cf[("retro", ang_b)]
    if ra["patch_minus_point_db"] is None or abs(ra["patch_minus_point_db"]
                                                - ra["patch_fine_minus_point_db"]) > 0.05:
        raise SystemExit("gen_results: default and refined patch factors differ at 30 deg")
    v["numCellRetroAngleA"], v["numCellRetroAngleB"] = dec(ang_a, 0), dec(ang_b, 0)
    v["numCellRetroADb"] = dec(ra["patch_fine_minus_point_db"], 1)
    v["numCellRetroBDb"] = dec(rb["patch_fine_minus_point_db"], 1)
    # consistency: the point branch reproduces the committed study values
    ebs = cms["eb"]
    for case, row in (("arc_blocked", ablk), ("arc_present", apres)):
        if abs(ebs[case]["point"]["rx1_total_change_db"] - float(row["rx1_total_change_db"])) \
                > 1e-9:
            raise SystemExit(f"gen_results: cell-model sensitivity {case} (point) differs from "
                             "the E-B arc run")
    eclab = cms["ec_lab"]
    for row_name in ("ideal_1bit", "ideal_2bit", "lab_mag1"):
        if abs(eclab["point"][f"loss_ris_db_{row_name}"]
               - float(ec[row_name]["loss_ris_db_lab"])) > 1e-9:
            raise SystemExit(f"gen_results: cell-model sensitivity E-C {row_name} (point) "
                             "differs from Table VIII")
    drop = [d["delta"]["ris_term_s1_patch_minus_point_db"] for d in ebs.values()] + [
        eclab["delta"]["cont_ris_power_patch_minus_point_db_min"],
        eclab["delta"]["cont_ris_power_patch_minus_point_db_max"]]
    if max(drop) >= 0:
        raise SystemExit("gen_results: Section IV-C says po_patch lowers the RIS power")
    v["numCellSensRisMinDb"] = dec(-max(drop), 1)
    v["numCellSensRisMaxDb"] = dec(-min(drop), 1)
    ec_d = [abs(eclab["po_patch"][k] - eclab["point"][k]) for k in eclab["point"]
            if k.startswith("loss_ris_db_")]
    v["numCellSensEcMaxDb"] = dec(max(ec_d), 1, mode=ROUND_CEILING)
    v["numCellSensSwitchMaxDb"] = dec(max(abs(d["delta"]["total_change_patch_minus_point_db"])
                                          for d in ebs.values()), 1)
    v["numCellSensCfgPct"] = dec(max(100.0 * max(d["delta"]["rx1_optimum_cells_differing"],
                                                 d["delta"]["rx2_optimum_cells_differing"])
                                     / d["delta"]["n_cells"] for d in ebs.values()), 0)
    v["numCellSensDeployLossDb"] = dec(max(abs(d["delta"]["point_optimum_under_patch_loss_db"])
                                           for d in ebs.values()), 2, mode=ROUND_CEILING)
    # V-LAB draws the cell model (prior point/po_patch, equal weights) and records a paired
    # point-vs-patch M2 comparison per ring (sensitivity S-NF)
    lab_proto = read_json(sremu_root / LAB_PROTOCOL)
    cm_prior = lab_proto["prior"]["cell_model"]
    if set(cm_prior["values"]) != {"point", "po_patch"} or len(set(cm_prior["weights"])) != 1:
        raise SystemExit("gen_results: Section IV-C says V-LAB draws both cell models equally")
    pvp = read_json(sremu_root / LAB_RUN / "results.json")["sensitivity"]["S-NF"]["point_vs_patch"]
    if pvp["n_alternative_not_converged"]:
        raise SystemExit("gen_results: a V-LAB point-vs-patch alternative did not converge")
    rings = pvp["rings_m"].values()
    v["numLabCmMedianMaxDb"] = dec(max(r["median_abs_raw_db"] for r in rings), 2)
    v["numLabCmQninetyfiveMaxDb"] = dec(max(r["q95_abs_raw_db"] for r in rings), 2)
    cm_meta = read_json(cm_dir / "meta.json")
    prov["sensitivity_cellmodel"] = {k: cm_meta.get(k) for k in
                                     ("sremu_commit", "sremu_dirty", "script_sha256",
                                      "t_start_utc")}

    # ---------------------------------------------------------------- appendix: provenance table
    prow = provenance_rows(paper_root, sremu_root)
    tables["tableA_provenance.tex"] = render_provenance_table(prow)
    # Appendix H says the locked (installed) checkout has the emulator source of the
    # regeneration commit: the combined hash of src/sremu/**/*.py (experiments/common.py
    # code_hashes) recorded by the regeneration results must equal the installed checkout's.
    src = hashlib.sha256()
    for p in sorted((sremu_root / "src" / "sremu").rglob("*.py")):
        src.update(p.relative_to(sremu_root).as_posix().encode())
        src.update(bytes(1))
        src.update(p.read_bytes())
    recorded = {(read_json(sw / rel).get("code") or {}).get("src_sremu_tree_sha256")
                for rel in ("meta.json", "E-F/latency/meta.json", "E-C/meta.json")
                if (sw / rel).is_file()}
    if len(recorded) != 1 or src.hexdigest() not in recorded:
        raise SystemExit("gen_results: Appendix H says the installed emulator checkout has the "
                         "source (src/sremu) of the regeneration commit; the hash recorded by "
                         f"the regeneration results {sorted(map(str, recorded))} differs from "
                         f"the checkout's {src.hexdigest()}")
    prov["src_sremu_tree_sha256"] = src.hexdigest()
    # Appendix H: the commit summary, grouped from the same rows (tools/vv_ids.py)
    v.update(provenance_values(paper_root, prow,
                               next(iter(js["meta"].values()), {})))

    # ---------------------------------------------------------------- VI: B1-B6, Table VII
    from bench_results import bench_values  # sweeps/results/bench and E-F/latency (MEAS)

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
           "note": "values computed by tools/gen_results.py from committed result files; each "
                   "result ran at the commit its own record names (Table tab:provenance, "
                   "summarized in Appendix H by txtProvRegenCommit / txtProvOtherCommits); "
                   "V-LAB keeps its locked analysis commit by design",
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
