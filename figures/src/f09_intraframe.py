"""F9 -- E-B intra-frame RIS reconfiguration (lab-like SIM geometry; lab-arc panels pending).

Data: the committed E-B1 and E-B2 results (paper/sweeps/results/E-B1, E-B2), produced on
scenarios/lab_like_A.yaml: a SIM geometry labelled "lab-like", not the laboratory's; lab state
amplitudes |Gamma| = 1 are ASSUMED; the complex-linear transition path is an ENG choice.

(a) Per-symbol EVM of one QPSK packet (64 preamble + 512 payload symbols, 4 samples per
symbol at 1 MS/s, one-tap LS equalizer from the preamble) to RX1 with the LOS blocked, when
the RIS switches to RX2's optimum a quarter of the packet after its start, for a step and two
linear transitions, and the same packet without a switch.

(b) Packet error rate (>= 1 bit error) over 2000 packets alternating between RX1 and RX2,
guard 0.5 ms, command latency 0.2 ms + U[0, J], LOS blocked: timed commands (switch at the
guard centre, lead 0.5 / 2 / 5 ms, late commands applied on arrival), asap commands (applied
on arrival) and tick-based untimed semantics (next 2 ms tick after arrival, one pending slot).
Error bars: Clopper-Pearson 95 %.

The lab-arc panels (scenarios/studies/lab_arc.yaml) are PENDING: they come from the AWS run.
"""

from __future__ import annotations

import csv
import json
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figcommon as fc

FID = "F9"
TRACES = "sweeps/results/E-B1/eb1_traces.csv"
POINTS = "sweeps/results/E-B1/eb1_points.csv"
VA5 = "sweeps/results/E-B1/va5.json"
PER = "sweeps/results/E-B2/eb2_per.csv"
EB2_META = "sweeps/results/E-B2/meta.json"
ALPHA, LOS, DELTA = "lab", "blocked", "1/4"
GUARD = "1/2000"
SMOOTH = 8  # symbols of the running mean of the linear EVM power in panel (a)
TRANSITIONS = (("step", "step", fc.OI["vermillion"], "-"),
               ("linear20us", r"linear, 20 $\mu$s", fc.OI["orange"], "--"),
               ("linear200us", r"linear, 200 $\mu$s", fc.OI["purple"], ":"))


def _read(rel: str) -> list[dict[str, str]]:
    return list(csv.DictReader(fc.input_path(rel).open(encoding="utf-8")))


def smooth_db(v_db: list[float]) -> np.ndarray:
    """Running mean (SMOOTH symbols, centred) of the linear EVM power, in dB."""
    p = 10 ** (np.asarray(v_db) / 10)
    k = np.ones(SMOOTH) / SMOOTH
    num = np.convolve(p, k, mode="same")
    den = np.convolve(np.ones_like(p), k, mode="same")
    return 10 * np.log10(num / den)


def main(argv: list[str] | None = None) -> int:
    args = fc.parse_args(FID, argv)
    fc.setup_style()
    import matplotlib.pyplot as plt

    rows = fc.Rows()
    tr = _read(TRACES)
    pts = _read(POINTS)
    per = _read(PER)

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(fc.DOUBLE_IN, 2.3),
                                     gridspec_kw={"width_ratios": [1.35, 1.0], "wspace": 0.25})
    # ------------------------------------------------------------------ (a)
    sel = [r for r in tr if (r["alphabet"], r["los"], r["delta"]) == (ALPHA, LOS, DELTA)]
    ref = sorted((r for r in sel if r["transition"] == "step"), key=lambda r: int(r["symbol"]))
    sym = np.array([int(r["symbol"]) for r in ref])
    ax_a.plot(sym, smooth_db([float(r["evm_ref_db"]) for r in ref]), "-", color=fc.OI["blue"],
              lw=0.9, label="no switch")
    for key, lab, col, ls in TRANSITIONS:
        rr = sorted((r for r in sel if r["transition"] == key), key=lambda r: int(r["symbol"]))
        ax_a.plot([int(r["symbol"]) for r in rr], smooth_db([float(r["evm_db"]) for r in rr]),
                  ls, color=col, lw=0.9, label=lab)
        for r in rr:
            rows.add("a", f"per-symbol EVM, switch at delta = {DELTA} T_p, {key}", "SIM-sremu",
                     "symbol", int(r["symbol"]), "evm_db", float(r["evm_db"]),
                     note=f"{ALPHA} alphabet, LOS {LOS}, kind={r['kind']}")
    for r in ref:
        rows.add("a", "per-symbol EVM without a switch", "SIM-sremu", "symbol",
                 int(r["symbol"]), "evm_db", float(r["evm_ref_db"]))
    p0 = next(p for p in pts if (p["alphabet"], p["los"], p["transition"], p["delta"])
              == (ALPHA, LOS, "step", DELTA))
    t_sw = float(Fraction(p0["t_sw_s"]))
    t0 = float(ref[0]["t_center_s"])
    t_sym = float(ref[1]["t_center_s"]) - t0
    s_sw = (t_sw - (t0 - t_sym / 2)) / t_sym
    n_pre = sum(1 for r in ref if r["kind"] == "preamble")
    ax_a.axvline(s_sw, color=fc.OI["black"], lw=0.7, ls="-.")
    ax_a.axvspan(-0.5, n_pre - 0.5, color=fc.OI["grey"], alpha=0.15, lw=0)
    ax_a.text(n_pre / 2, 7.0, "preamble", ha="center", va="top")
    ax_a.text(s_sw + 6, 7.0, "switch", va="top")
    ax_a.set_xlim(-0.5, len(ref) - 0.5)
    ax_a.set_ylim(-40, 8)
    ax_a.set_xlabel("symbol index in the packet")
    ax_a.set_ylabel(f"EVM, {SMOOTH}-symbol mean (dB)")
    ax_a.legend(loc="lower right", ncol=2, columnspacing=1.0)
    fc.panel_label(ax_a, "(a)", x=0.012, y=0.08)
    # ------------------------------------------------------------------ (b)
    jit = ["0", "1/10000", "3/10000", "1/1000", "3/1000"]
    xlab = ["0", "0.1", "0.3", "1", "3"]
    series = [("timed", "1/2000", "timed, lead 0.5 ms", fc.OI["blue"], "o"),
              ("timed", "1/500", "timed, lead 2 ms", fc.OI["sky"], "D"),
              ("timed", "1/200", "timed, lead 5 ms", fc.OI["green"], "v"),
              ("asap", "", "asap", fc.OI["orange"], "s"),
              ("tick-based", "", "tick-based", fc.OI["vermillion"], "^")]
    x = np.arange(len(jit))
    for i, (sem, lead, lab, col, mk) in enumerate(series):
        y, lo, hi = [], [], []
        for j in jit:
            r = next(r for r in per if r["alphabet"] == ALPHA and r["los"] == LOS
                     and r["guard_s"] == GUARD and r["jitter_s"] == j and r["semantics"] == sem
                     and (r["lead_s"] == lead or sem != "timed"))
            y.append(float(r["per"]))
            lo.append(float(r["per_ci_low"]))
            hi.append(float(r["per_ci_high"]))
            rows.add("b", f"PER, {lab}", "SIM-sremu", "jitter_s", str(Fraction(j)), "per",
                     float(r["per"]), float(r["per_ci_low"]), float(r["per_ci_high"]),
                     note=f"guard 0.5 ms; {r['packet_errors']}/{r['n_packets']} packets; "
                          f"late={r['n_late']}; {ALPHA} alphabet, LOS {LOS}")
        y, lo, hi = np.array(y), np.array(lo), np.array(hi)
        dx = (i - 2) * 0.09
        ax_b.errorbar(x + dx, y, yerr=[y - lo, hi - y], fmt=mk + "-", color=col, ms=3.2,
                      lw=0.8, elinewidth=0.8, capsize=1.5, label=lab)
    rows.add("b", "lab state amplitudes |Gamma| = 1", "ASSUMED", "", "", "", float("nan"),
             note="E-B scene assumption")
    ax_b.set_xticks(x)
    ax_b.set_xticklabels(xlab)
    ax_b.set_xlabel("latency jitter $J$ (ms)")
    ax_b.set_ylabel("packet error rate")
    ax_b.set_ylim(-0.03, 1.0)
    ax_b.legend(loc="center left", bbox_to_anchor=(0.0, 0.40), handlelength=1.4,
                labelspacing=0.2)
    fc.panel_label(ax_b, "(b)", x=0.90, y=0.08)

    va5 = json.loads(fc.input_path(VA5).read_text(encoding="utf-8"))
    inputs = [
        {"path": TRACES, "data_class": "SIM-sremu",
         "provenance": fc.study_provenance(fc.input_path("sweeps/results/E-B1/meta.json"))},
        {"path": POINTS, "data_class": "SIM-sremu"},
        {"path": VA5, "data_class": "SIM-sremu"},
        {"path": PER, "data_class": "SIM-sremu",
         "provenance": fc.study_provenance(fc.input_path(EB2_META))},
        {"path": EB2_META, "data_class": "ASSUMED", "note": "lab |Gamma| ASSUMED"},
    ]
    assumptions = [
        "lab-like SIM geometry (scenarios/lab_like_A.yaml), not the laboratory's",
        "lab state amplitudes |Gamma| = 1 ASSUMED; complex-linear transition path [ENG]",
        "TX power set for Es/N0 = 25 dB at RX1 under its own optimum (physical noise, NF 7 dB)",
        "lab-arc panels PENDING (AWS run); this figure shows the lab-like panels only",
    ]
    extra = {"panel_a_smoothing_symbols": SMOOTH, "va5_pass": va5.get("pass"),
             "panel_a_switch_symbol": s_sw,
             "pending": "lab-arc panels (scenarios/studies/lab_arc.yaml), AWS"}
    fc.write_outputs(FID, fig, rows, args=args, inputs=inputs, assumptions=assumptions,
                     extra=extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
