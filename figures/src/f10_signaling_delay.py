"""F10 -- E-A: signaling delay in closed-loop binary RIS control (blocked corridor).

Data: the committed E-A aggregation (paper/sweeps/results/E-A; batch path, 20 batches x
500 mean movement periods per point, pre-registration paper/preregistration/E-A.md) and the
committed VA-1 IQ consistency subset (paper/sweeps/results/E-A-IQ). All SIM-sremu.

(a), (b): gain over RIS absent (mean of the 20 paired per-batch dB gains, Student-t 95 %
interval) vs tau_SUI for ExactOptimum, WclHeuristic (both with delayed position reports),
Urap(0.2) and ProbeBandit, at 1 GHz and 2.4 GHz (A1: partition loss 20 dB on the LOS, h = 2 m);
horizontal references: the instantaneous optimum P(0) and the stale optimum P_stale; vertical
line: tau_half of ExactOptimum (retention 0.5, linear power).
(c): VA-1, IQ path (served engine, external controller process in barrier mode, external RX
application) minus the batch evaluator, 1000 s per row, with the 0.02 dB acceptance band.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figcommon as fc

FID = "F10"
GAINS = "sweeps/results/E-A/gains.csv"
RET = "sweeps/results/E-A/retention.csv"
TAU = "sweeps/results/E-A/tau_half.csv"
HYP = "sweeps/results/E-A/hypotheses.json"
VA1 = "sweeps/results/E-A-IQ/va1.csv"
GEOMS = (("f1.0G_h2_L20", "(a) 1 GHz"), ("f2.4G_h2_L20", "(b) 2.4 GHz"))
CTRL = (("exact", "exact optimum", fc.OI["blue"], "o"),
        ("wcl", "heuristic", fc.OI["green"], "s"),
        ("bandit", "bandit", fc.OI["orange"], "D"),  # the name the text uses
        ("urap", "URAP", fc.OI["purple"], "^"))
VA1_TOL_DB = 0.02
# text rows above the curves of (a), (b): tau_half label, then the panel label (dB)
TAU_LABEL_Y_DB = 6.05
PANEL_LABEL_Y_DB = 6.62
YMAX_DB = 6.95


def _read(rel: str) -> list[dict[str, str]]:
    return list(csv.DictReader(fc.input_path(rel).open(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    args = fc.parse_args(FID, argv)
    fc.setup_style()
    import matplotlib.pyplot as plt

    rows = fc.Rows()
    gains = _read(GAINS)
    taus = {r["geometry"]: r for r in _read(TAU)}
    va1 = _read(VA1)
    hyp = json.loads(fc.input_path(HYP).read_text(encoding="utf-8"))

    fig = plt.figure(figsize=(fc.DOUBLE_IN, 2.35))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.0, 0.72], wspace=0.30)
    axes = [fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])]
    axes[1].sharey(axes[0])
    ax_c = fig.add_subplot(gs[0, 2])
    for ax, (geo, lab) in zip(axes, GEOMS, strict=True):
        g = [r for r in gains if r["geometry"] == geo]
        ref = {r["controller"]: r for r in g if not r["tau_sui_s"]}
        for key, name, col, ls in (("instant", "$P(0)$", fc.OI["black"], "--"),
                                   ("stale", "$P_{\\mathrm{stale}}$", fc.OI["grey"], ":")):
            v = float(ref[key]["gain_db"])
            ax.axhline(v, color=col, lw=0.9, ls=ls, label=name)
            rows.add(lab[:3], f"reference {key}", "SIM-sremu", "tau_sui_s", "", "gain_db", v,
                     float(ref[key]["gain_ci_low_db"]), float(ref[key]["gain_ci_high_db"]),
                     note=geo)
        for key, name, col, mk in CTRL:
            rr = sorted((r for r in g if r["controller"] == key),
                        key=lambda r: float(r["tau_sui_s"]))
            x = np.array([float(r["tau_sui_s"]) for r in rr])
            y = np.array([float(r["gain_db"]) for r in rr])
            lo = np.array([float(r["gain_ci_low_db"]) for r in rr])
            hi = np.array([float(r["gain_ci_high_db"]) for r in rr])
            ax.errorbar(x, y, yerr=[y - lo, hi - y], fmt=mk + "-", color=col, ms=3, lw=0.9,
                        elinewidth=0.7, capsize=1.2, label=name)
            for r in rr:
                rows.add(lab[:3], f"gain over RIS absent, {key}", "SIM-sremu", "tau_sui_s",
                         float(r["tau_sui_s"]), "gain_db", float(r["gain_db"]),
                         float(r["gain_ci_low_db"]), float(r["gain_ci_high_db"]),
                         note=f"{geo}; mean of 20 per-batch dB gains; p_mean_dbm="
                              f"{r['p_mean_dbm']}")
        th = taus[geo]
        t_half = float(th["tau_half_s"])
        ax.axvline(t_half, color=fc.OI["vermillion"], lw=0.9, ls="-.")
        ax.text(t_half * 1.12, TAU_LABEL_Y_DB,r"$\tau_{1/2}$ = " f"{t_half:.3f} s",
                color=fc.OI["vermillion"], va="center")
        rows.add(lab[:3], "tau_half of the exact optimum", "SIM-sremu", "", "", "tau_half_s",
                 t_half, float(th["tau_half_ci_low_s"]), float(th["tau_half_ci_high_s"]),
                 note=geo)
        ax.set_xscale("log")
        ax.set_xlim(0.007, 14)
        ax.set_xlabel(r"signaling delay $\tau_{\mathrm{SUI}}$ (s)")
        # panel label in its own row above the tau_half label, clear of the P(0) line
        ax.text(0.97, PANEL_LABEL_Y_DB, lab, ha="right", va="center",
                transform=ax.get_yaxis_transform())
    axes[0].set_ylabel("gain over RIS absent (dB)")
    axes[0].set_ylim(-0.2, YMAX_DB)
    plt.setp(axes[1].get_yticklabels(), visible=False)
    h, lbl = axes[0].get_legend_handles_labels()
    order = [2, 3, 4, 5, 0, 1]
    axes[1].legend([h[i] for i in order], [lbl[i] for i in order], loc="center right",
                   bbox_to_anchor=(1.0, 0.52), handlelength=1.4, labelspacing=0.15)

    # (c) VA-1
    labels, diffs = [], []
    for r in va1:
        labels.append(f"{'opt.' if r['controller'] == 'exact' else 'URAP'} "
                      f"{float(r['tau_sui_s']):g} s, {r['noise']}")
        diffs.append(float(r["diff_iq_minus_vectorized_mid_db"]))
        rows.add("c", "IQ minus batch (vectorized midpoint)", "SIM-sremu", "row", labels[-1],
                 "diff_db", diffs[-1],
                 note=f"accept={r['accept']}; host={r['cpu_model']}; duration_s="
                      f"{r['duration_s']}")
    yy = np.arange(len(labels))
    ax_c.axvspan(-VA1_TOL_DB, VA1_TOL_DB, color=fc.OI["green"], alpha=0.15, lw=0)
    ax_c.axvline(0, color=fc.OI["black"], lw=0.6)
    ax_c.plot(diffs, yy, "o", color=fc.OI["blue"], ms=3)
    ax_c.set_yticks(yy)
    ax_c.set_yticklabels(labels)
    ax_c.yaxis.tick_right()
    ax_c.set_ylim(len(labels) - 0.5, -0.5)
    ax_c.set_xlim(-0.025, 0.025)
    ax_c.set_xticks([-0.02, 0, 0.02])
    ax_c.set_xlabel("IQ $-$ batch (dB)")
    ax_c.text(0.04, 0.03, "(c)", transform=ax_c.transAxes)
    ax_c.grid(axis="y", visible=False)
    rows.add("c", "VA-1 acceptance band (noise off)", "SIM-sremu", "", "", "tol_db", VA1_TOL_DB,
             note="pre-registered: |IQ - batch| <= 0.02 dB")

    inputs = [
        {"path": GAINS, "data_class": "SIM-sremu",
         "provenance": fc.study_provenance(fc.input_path("sweeps/results/E-A/meta.json"))},
        {"path": RET, "data_class": "SIM-sremu"},
        {"path": TAU, "data_class": "SIM-sremu"},
        {"path": HYP, "data_class": "SIM-sremu", "decisions": {
            k: v.get("decision") for k, v in hyp.items() if isinstance(v, dict)
            and "decision" in v}},
        {"path": VA1, "data_class": "SIM-sremu",
         "provenance": fc.study_provenance(fc.input_path("sweeps/results/E-A-IQ/meta.json"))},
    ]
    assumptions = [
        "A1 blocked corridor: partition loss 20 dB on the LOS only (RIS hops unattenuated), "
        "h = 2 m, l = 10 m, 1-D random waypoint at 1 m/s, AP offset 0.5 m [ENG]",
        "position-driven controllers use a 1 cm lookup grid of the scene model (oracle "
        "geometry) and a 1 ms command latency",
        "gain = mean of 20 paired per-batch dB gains (pre-registered), Student-t 95 % CI",
        "VA-1 rows ran on two hosts (laptop and an AWS c7i.8xlarge); powers do not depend on "
        "the host",
    ]
    fc.write_outputs(FID, fig, rows, args=args, inputs=inputs, assumptions=assumptions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
