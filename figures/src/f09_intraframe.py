"""F9 -- E-B intra-frame RIS reconfiguration.

Top row (primary): the AWS run on ``scenarios/studies/lab_arc.yaml``, a geometry MODELLED ON
THE LAB ARRANGEMENT (SIM, nominal geometry): six tiles of 5 x 8 cells on an arc around the
Tx, nominal values for the uncertain dimensions, not a measured geometry
(paper/sweeps/results/E-B-arc). Bottom row (supporting): the lab-like SIM geometry
``scenarios/lab_like_A.yaml``, one panel of three tiles, not the laboratory's
(paper/sweeps/results/E-B1, E-B2). In both, the lab state amplitudes |Gamma| = 1 are ASSUMED
and the transition path is complex-linear [ENG].

(a), (c) Per-symbol EVM of one QPSK packet (64 preamble + 512 payload symbols, 4 samples per
symbol at 1 MS/s, one-tap LS equalizer from the preamble) to RX1 when the RIS switches to
RX2's optimum a quarter of the packet after its start: step and two linear transitions with
the LOS blocked, a step with the LOS present, and the packet without a switch (LOS blocked).

(b), (d) Packet error rate (>= 1 bit error) over 2000 packets alternating between RX1 and
RX2, guard 0.5 ms, command latency 0.2 ms + U[0, J]: timed commands (switch at the guard
centre, lead 2 ms, late commands applied on arrival), asap commands (applied on arrival) and
tick-based untimed semantics (next 2 ms tick after arrival, one pending slot per panel).
(b): LOS present at Es/N0 = 15 dB (the pre-declared sensitivity; open markers) and LOS
blocked at 25 dB (filled markers). (d): LOS blocked at 25 dB (the lab-like run has no 15 dB
slice). Error bars: Clopper-Pearson 95 %. Every label value is read from the data rows.
"""

from __future__ import annotations

import csv
import json
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, TypedDict

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figcommon as fc

FID = "F9"
ARC = "sweeps/results/E-B-arc"
LAB = "sweeps/results"


class Geom(TypedDict):
    traces: str
    points: str
    va5: str
    per: str
    meta1: str
    meta2: str
    label: str
    tag: str
    cases: tuple[tuple[str, str], ...]


GEOMS: dict[str, Geom] = {
    "arc": {
        "traces": f"{ARC}/E-B1/eb1_traces.csv", "points": f"{ARC}/E-B1/eb1_points.csv",
        "va5": f"{ARC}/E-B1/va5.json", "per": f"{ARC}/E-B2/eb2_per.csv",
        "meta1": f"{ARC}/E-B1/meta.json", "meta2": f"{ARC}/E-B2/meta.json",
        "label": "modelled on the lab arrangement (SIM, nominal geometry)",
        "tag": "modelled on the lab arrangement (nominal)",
        # (LOS, Es/N0 target) cases of panel (b): pre-declared sensitivity, then LOS blocked
        "cases": (("present", "15.0"), ("blocked", "25.0")),
    },
    "lab-like": {
        "traces": f"{LAB}/E-B1/eb1_traces.csv", "points": f"{LAB}/E-B1/eb1_points.csv",
        "va5": f"{LAB}/E-B1/va5.json", "per": f"{LAB}/E-B2/eb2_per.csv",
        "meta1": f"{LAB}/E-B1/meta.json", "meta2": f"{LAB}/E-B2/meta.json",
        "label": "lab-like SIM geometry (not the lab's)",
        "tag": "lab-like geometry (supporting)",
        "cases": (("blocked", "25.0"),),
    },
}
ALPHA, DELTA = "lab", "1/4"
GUARD = "1/2000"  # the 0.5 ms guard slice
LEAD = "1/500"  # the 2 ms lead shown for timed commands
JITTER = ("0", "1/10000", "3/10000", "1/1000", "3/1000")
SMOOTH = 8  # symbols of the running mean of the linear EVM power in panels (a), (c)
TRANSITIONS = (("blocked", "step", "step", fc.OI["vermillion"], "-"),
               ("blocked", "linear20us", r"linear, 20 $\mu$s", fc.OI["orange"], "--"),
               ("blocked", "linear200us", r"linear, 200 $\mu$s", fc.OI["purple"], ":"),
               ("present", "step", "step, LOS present", fc.OI["green"], "-."))
SEMANTICS = (("timed", LEAD, fc.OI["blue"], "o"),
             ("asap", "", fc.OI["orange"], "s"),
             ("tick-based", "", fc.OI["vermillion"], "^"))


def _read(rel: str) -> list[dict[str, str]]:
    return list(csv.DictReader(fc.input_path(rel).open(encoding="utf-8")))


def ms(x: str) -> str:
    """A time in seconds (exact string) as milliseconds for a label."""
    return f"{1e3 * float(Fraction(x)):g}"


def smooth_db(v_db: list[float]) -> np.ndarray:
    """Running mean (SMOOTH symbols, centred) of the linear EVM power, in dB."""
    p = 10 ** (np.asarray(v_db) / 10)
    k = np.ones(SMOOTH) / SMOOTH
    num = np.convolve(p, k, mode="same")
    den = np.convolve(np.ones_like(p), k, mode="same")
    return 10 * np.log10(num / den)


def draw_evm(ax: Any, geo: str, rows: fc.Rows, panel: str) -> float:
    g = GEOMS[geo]
    tr = _read(g["traces"])
    pts = _read(g["points"])

    def trace(los: str, trans: str) -> list[dict[str, str]]:
        sel = [r for r in tr if (r["alphabet"], r["los"], r["transition"], r["delta"])
               == (ALPHA, los, trans, DELTA)]
        if not sel:
            raise SystemExit(f"F9: no trace for {geo} {los} {trans}")
        return sorted(sel, key=lambda r: int(r["symbol"]))

    ref = trace("blocked", "step")
    sym = np.array([int(r["symbol"]) for r in ref])
    ax.plot(sym, smooth_db([float(r["evm_ref_db"]) for r in ref]), "-", color=fc.OI["blue"],
            lw=0.9, label="no switch")
    for r in ref:
        rows.add(panel, f"{geo}: per-symbol EVM without a switch", "SIM-sremu", "symbol",
                 int(r["symbol"]), "evm_db", float(r["evm_ref_db"]),
                 note=f"{ALPHA} alphabet, LOS blocked; {g['label']}")
    for los, key, lab, col, ls in TRANSITIONS:
        rr = trace(los, key)
        ax.plot([int(r["symbol"]) for r in rr], smooth_db([float(r["evm_db"]) for r in rr]),
                ls, color=col, lw=0.9, label=lab)
        for r in rr:
            rows.add(panel, f"{geo}: per-symbol EVM, switch at delta = {DELTA} T_p, {key}, "
                     f"LOS {los}", "SIM-sremu", "symbol", int(r["symbol"]), "evm_db",
                     float(r["evm_db"]), note=f"{ALPHA} alphabet, kind={r['kind']}; "
                                              f"{g['label']}")
    p0 = next(p for p in pts if (p["alphabet"], p["los"], p["transition"], p["delta"])
              == (ALPHA, "blocked", "step", DELTA))
    t_sw = float(Fraction(p0["t_sw_s"]))
    t0 = float(ref[0]["t_center_s"])
    t_sym = float(ref[1]["t_center_s"]) - t0
    s_sw = (t_sw - (t0 - t_sym / 2)) / t_sym
    n_pre = sum(1 for r in ref if r["kind"] == "preamble")
    ax.axvline(s_sw, color=fc.OI["black"], lw=0.7, ls=(0, (1, 2)))
    ax.axvspan(-0.5, n_pre - 0.5, color=fc.OI["grey"], alpha=0.15, lw=0)
    ax.text(n_pre / 2, 9.0, "preamble", ha="center", va="top")
    ax.text(s_sw + 6, 9.0, "switch", va="top")
    ax.text(0.985, 0.965, g["tag"], transform=ax.transAxes, ha="right", va="top",
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.5, "alpha": 0.8})
    ax.set_xlim(-0.5, len(ref) - 0.5)
    ax.set_ylim(-52, 10)
    ax.set_ylabel(f"EVM, {SMOOTH}-symbol mean (dB)")
    return s_sw


def draw_per(ax: Any, geo: str, rows: fc.Rows, panel: str) -> list[tuple[str, str]]:
    g = GEOMS[geo]
    per = _read(g["per"])
    x = np.arange(len(JITTER))
    cases = g["cases"]
    n_series = len(SEMANTICS) * len(cases)
    k = 0
    for los, es in cases:
        filled = los == "blocked"
        for sem, lead, col, mk in SEMANTICS:
            y, lo, hi = [], [], []
            for j in JITTER:
                hit = [r for r in per if r["alphabet"] == ALPHA and r["los"] == los
                       and r["es_n0_target_db"] == es and r["guard_s"] == GUARD
                       and r["jitter_s"] == j and r["semantics"] == sem
                       and (sem != "timed" or r["lead_s"] == lead)]
                if len(hit) != 1:
                    raise SystemExit(f"F9: {len(hit)} rows for {geo} {los} {es} {sem} J={j}")
                r = hit[0]
                y.append(float(r["per"]))
                lo.append(float(r["per_ci_low"]))
                hi.append(float(r["per_ci_high"]))
                lab = f"timed, lead {ms(lead)} ms" if sem == "timed" else sem
                rows.add(panel, f"{geo}: PER, {lab}, LOS {los}, Es/N0 {es} dB", "SIM-sremu",
                         "jitter_s", str(Fraction(j)), "per", float(r["per"]),
                         float(r["per_ci_low"]), float(r["per_ci_high"]),
                         note=f"guard {ms(GUARD)} ms; {r['packet_errors']}/{r['n_packets']} "
                              f"packets; late={r['n_late']}; {ALPHA} alphabet; {g['label']}")
            ya, loa, hia = np.array(y), np.array(lo), np.array(hi)
            dx = (k - (n_series - 1) / 2) * 0.07
            ax.errorbar(x + dx, ya, yerr=[ya - loa, hia - ya], fmt=mk + ("-" if filled else "--"),
                        color=col, ms=3.2, lw=0.8, elinewidth=0.8, capsize=1.5,
                        mfc=col if filled else "white", mec=col)
            k += 1
    ax.set_xticks(x)
    ax.set_xticklabels([ms(j) for j in JITTER])
    ax.set_ylabel("packet error rate")
    ax.set_ylim(-0.03, 1.0)
    return list(cases)


def per_legends(ax: Any, cases: list[tuple[str, str]]) -> None:
    from matplotlib.lines import Line2D

    sem_handles = [Line2D([], [], color=col, marker=mk, ms=3.2, lw=0.8,
                          label=(f"timed, lead {ms(lead)} ms" if sem == "timed" else sem))
                   for sem, lead, col, mk in SEMANTICS]
    leg = ax.legend(handles=sem_handles, loc="upper left", bbox_to_anchor=(0.0, 0.62),
                    handlelength=1.4, labelspacing=0.2)
    if len(cases) > 1:
        ax.add_artist(leg)
        case_handles = [Line2D([], [], color=fc.OI["black"], marker="o", ms=3.2, lw=0.8,
                               ls="-" if los == "blocked" else "--",
                               mfc=fc.OI["black"] if los == "blocked" else "white",
                               label=f"LOS {los}, {float(es):g} dB")
                        for los, es in cases]
        ax.legend(handles=case_handles, loc="upper left", bbox_to_anchor=(0.0, 0.33),
                  handlelength=1.8, labelspacing=0.2)


def main(argv: list[str] | None = None) -> int:
    args = fc.parse_args(FID, argv)
    fc.setup_style()
    import matplotlib.pyplot as plt

    rows = fc.Rows()
    fig, axes = plt.subplots(2, 2, figsize=(fc.DOUBLE_IN, 4.5),
                             gridspec_kw={"width_ratios": [1.35, 1.0], "wspace": 0.25,
                                          "hspace": 0.32})
    (ax_a, ax_b), (ax_c, ax_d) = axes
    s_sw = {"arc": draw_evm(ax_a, "arc", rows, "a"),
            "lab-like": draw_evm(ax_c, "lab-like", rows, "c")}
    ax_a.legend(loc="lower right", ncol=3, columnspacing=0.9, handlelength=1.8,
                labelspacing=0.2)
    ax_c.set_xlabel("symbol index in the packet")
    cases_b = draw_per(ax_b, "arc", rows, "b")
    cases_d = draw_per(ax_d, "lab-like", rows, "d")
    per_legends(ax_b, cases_b)
    per_legends(ax_d, cases_d)
    ax_d.set_xlabel("latency jitter $J$ (ms)")
    fc.panel_label(ax_a, "(a)", x=0.012, y=0.08)
    fc.panel_label(ax_b, "(b)", x=0.90, y=0.08)
    fc.panel_label(ax_c, "(c)", x=0.012, y=0.08)
    fc.panel_label(ax_d, "(d)", x=0.90, y=0.08)
    rows.add("b", "lab state amplitudes |Gamma| = 1", "ASSUMED", "", "", "", float("nan"),
             note="E-B scene assumption (both geometries)")

    va5 = {geo: json.loads(fc.input_path(g["va5"]).read_text(encoding="utf-8"))
           for geo, g in GEOMS.items()}
    inputs: list[dict[str, Any]] = []
    for g in GEOMS.values():
        inputs += [
            {"path": g["traces"], "data_class": "SIM-sremu", "geometry": g["label"],
             "provenance": fc.study_provenance(fc.input_path(g["meta1"]))},
            {"path": g["points"], "data_class": "SIM-sremu", "geometry": g["label"]},
            {"path": g["va5"], "data_class": "SIM-sremu", "geometry": g["label"]},
            {"path": g["per"], "data_class": "SIM-sremu", "geometry": g["label"],
             "provenance": fc.study_provenance(fc.input_path(g["meta2"]))},
            {"path": g["meta2"], "data_class": "ASSUMED", "note": "lab |Gamma| ASSUMED"},
        ]
    arc_meta = json.loads(fc.input_path(f"{ARC}/meta.json").read_text(encoding="utf-8"))
    inputs.append({"path": f"{ARC}/meta.json", "data_class": "SIM-sremu",
                   "provenance": {"sremu_commit": arc_meta["sremu"]["commit"],
                                  "sremu_dirty": arc_meta["sremu"]["dirty"],
                                  "run_id": arc_meta["run"]["run_id"],
                                  "aws_instance": arc_meta["host"]["instance_type"]}})
    assumptions = [
        "top row: modelled on the lab arrangement (SIM, nominal geometry; "
        "scenarios/studies/lab_arc.yaml, AWS run); not a measured geometry",
        "bottom row: lab-like SIM geometry (scenarios/lab_like_A.yaml), not the laboratory's",
        "lab state amplitudes |Gamma| = 1 ASSUMED; complex-linear transition path [ENG]",
        "LOS blocked: occluders act on the LOS only (RIS hops unattenuated) [ENG]",
        "TX power set for the stated Es/N0 at RX1 under its own optimum (physical noise, "
        "NF 7 dB); 15 dB = pre-declared sensitivity of the LOS-present slice (arc only)",
    ]
    extra = {"panel_ac_smoothing_symbols": SMOOTH,
             "va5_pass": {geo: v.get("pass") for geo, v in va5.items()},
             "panel_ac_switch_symbol": s_sw,
             "panel_bd_guard_s": GUARD, "panel_bd_timed_lead_s": LEAD}
    fc.write_outputs(FID, fig, rows, args=args, inputs=inputs, assumptions=assumptions,
                     extra=extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
