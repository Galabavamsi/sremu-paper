"""F4 -- laboratory consistency check under geometric uncertainty (V-LAB).

Plots the committed, unblinded V-LAB run (validation/ants_fig4b/results/<run>): the digitized
map of a laboratory measurement collected by the authors (DIG), the prior-median prediction of
the lab-configuration model M2 (SIM-sremu; geometry, Gamma magnitudes and antennas drawn from
the pre-registered prior: ASSUMED) and the residual, on identical polar axes; and the
distribution over the prior draws of Delta RMSE = RMSE(M0) - RMSE(M2) on the test rings with
the C2 threshold and the decoy C2 detection rate of the pre-registered power analysis.
Measured and predicted maps share one colour scale (viridis); the residual uses a diverging
scale clipped at +-5 dB (the clipped cell is marked). Test-ring cells are outlined.
"""

from __future__ import annotations

import csv
import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figcommon as fc

FID = "F4"
RUN = "../sremu/validation/ants_fig4b/results/20261011T003044Z_vlab_unblinded"
F4_CSV = f"{RUN}/F4.csv"
F4_META = f"{RUN}/F4.meta.json"
DELTA_CSV = f"{RUN}/F4_delta_rmse.csv"
TABLE_V = f"{RUN}/table_v.json"
PROTOCOL = "../sremu/validation/ants_fig4b/protocol.json"
RES_CLIP_DB = 5.0
MINUS = "\N{MINUS SIGN}"  # U+2212, in STIXGeneral; tick labels use it too (axes.unicode_minus)


def _num(v: float, fmt: str) -> str:
    """``v`` formatted with ``fmt`` for an annotation, with a typographic minus sign."""
    s = format(v, fmt)
    return MINUS + s[1:] if s.startswith("-") else s


def _edges(centres: list[float]) -> list[float]:
    c = sorted(centres)
    mids = [(a + b) / 2 for a, b in itertools.pairwise(c)]
    return [c[0] - (mids[0] - c[0]), *mids, c[-1] + (c[-1] - mids[-1])]


def main(argv: list[str] | None = None) -> int:
    args = fc.parse_args(FID, argv)
    fc.setup_style()
    import matplotlib.pyplot as plt
    from matplotlib import colors as mcolors

    cells = list(csv.DictReader(fc.input_path(F4_CSV).open(encoding="utf-8")))
    deltas = [float(r["delta_m0_m2_db"]) for r in
              csv.DictReader(fc.input_path(DELTA_CSV).open(encoding="utf-8"))]
    tab = json.loads(fc.input_path(TABLE_V).read_text(encoding="utf-8"))
    proto = json.loads(fc.input_path(PROTOCOL).read_text(encoding="utf-8"))
    c2_min = float(proto["decision_rules"]["C2"]["median_delta_rmse_min_db"])
    power = tab["power_analysis"]
    n_dec = int(power["n_decoys"])
    rate = float(power["c2_detection_rate"])

    th_c = sorted({float(r["theta_deg"]) for r in cells})
    r_c = sorted({float(r["r_m"]) for r in cells})
    th_e, r_e = _edges(th_c), _edges(r_c)
    th_lo = {c: (th_e[i], th_e[i + 1]) for i, c in enumerate(th_c)}
    r_lo = {c: (r_e[i], r_e[i + 1]) for i, c in enumerate(r_c)}

    meas = np.array([float(r["measured_dbm"]) for r in cells])
    pred = np.array([float(r["pred_m2_prior_median_dbm"]) for r in cells])
    res = np.array([float(r["residual_m2_db"]) for r in cells])
    vmin, vmax = math.floor(min(meas.min(), pred.min())), math.ceil(max(meas.max(), pred.max()))
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    rnorm = mcolors.Normalize(vmin=-RES_CLIP_DB, vmax=RES_CLIP_DB)
    cmap = plt.get_cmap("viridis")
    rcmap = plt.get_cmap("PuOr_r")

    fig = plt.figure(figsize=(fc.DOUBLE_IN, 2.35))
    w = 0.205
    axs = [fig.add_axes([0.005 + i * 0.235, 0.20, w, 0.74], projection="polar")
           for i in range(3)]
    ax_h = fig.add_axes([0.765, 0.20, 0.225, 0.70])
    rows = fc.Rows()

    for ax, vals, cm, nm, lab, _dc in (
        (axs[0], meas, cmap, norm, "(a) measured", "DIG"),
        (axs[1], pred, cmap, norm, "(b) predicted, M2", "SIM-sremu"),
        (axs[2], res, rcmap, rnorm, "(c) residual", "DIG"),
    ):
        for cell, v in zip(cells, vals, strict=True):
            t, r = float(cell["theta_deg"]), float(cell["r_m"])
            t0, t1 = th_lo[t]
            r0, r1 = r_lo[r]
            test = cell["split"] == "test"
            ax.bar(math.radians((t0 + t1) / 2), r1 - r0, width=math.radians(t1 - t0),
                   bottom=r0, color=cm(nm(v)), edgecolor="black" if test else "white",
                   linewidth=0.9 if test else 0.3, align="center")
            ax.plot(math.radians(t), r, ".", color="white" if nm(v) < 0.45 else "black",
                    ms=1.6)
            if lab.startswith("(c)") and abs(v) > RES_CLIP_DB:
                ax.annotate(f"{_num(v, '.1f')} dB", (math.radians(t), r), xytext=(0.02, 0.80),
                            textcoords="axes fraction", fontsize=fc.FONT_PT,
                            arrowprops={"arrowstyle": "-", "lw": 0.5})
        ax.set_thetamin(th_e[0])
        ax.set_thetamax(th_e[-1])
        ax.set_rlim(0, r_e[-1])
        ax.set_rorigin(0)
        ax.set_rticks([0.6, 1.2, 1.8])
        ax.set_yticklabels(["0.6", "1.2", "1.8 m"])
        ax.set_thetagrids([0, 15, 30, 45])
        ax.grid(True, lw=0.3, alpha=0.6)
        ax.tick_params(pad=1)
        ax.text(0.0, 1.0, lab, transform=ax.transAxes, ha="left", va="top",
                fontsize=fc.FONT_PT)
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cax = fig.add_axes([0.06, 0.10, 0.36, 0.035])
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
    cb.set_label("received power (dBm)", labelpad=1)
    rsm = plt.cm.ScalarMappable(norm=rnorm, cmap=rcmap)
    rcax = fig.add_axes([0.53, 0.10, 0.16, 0.035])
    rcb = fig.colorbar(rsm, cax=rcax, orientation="horizontal", extend="both")
    rcb.set_label("measured $-$ predicted (dB)", labelpad=1)

    for cell in cells:
        th, r, sp = cell["theta_deg"], cell["r_m"], cell["split"]
        note = f"theta_deg={th}; r_m={r}; split={sp}"
        rows.add("a", "measured (digitized)", "DIG", "cell", cell["cell"], "p_dbm",
                 float(cell["measured_dbm"]), note=note + f"; sigma_dig_db={cell['sigma_dig_db']}")
        rows.add("b", "M2 prior-median prediction", "SIM-sremu", "cell", cell["cell"],
                 "p_dbm", float(cell["pred_m2_prior_median_dbm"]),
                 note=note + f"; sigma_geom_db={cell['sigma_geom_m2_db']}")
        rows.add("c", "measured minus M2 prediction", "DIG", "cell", cell["cell"],
                 "residual_db", float(cell["residual_m2_db"]), note=note)

    d = np.array(deltas)
    bins = np.arange(math.floor(d.min() * 2) / 2, math.ceil(max(d.max(), c2_min) * 2) / 2 + 0.25,
                     0.25)
    counts, _, _ = ax_h.hist(d, bins=bins, color=fc.OI["sky"], edgecolor=fc.OI["blue"],
                             lw=0.4)
    ax_h.axvline(0.0, color=fc.OI["black"], lw=0.8)
    ax_h.axvline(c2_min, color=fc.OI["vermillion"], lw=1.0, ls="--")
    med = float(np.median(d))
    ax_h.axvline(med, color=fc.OI["blue"], lw=1.0, ls=":")
    ax_h.text(c2_min + 0.08, counts.max() * 0.97, "C2\nneeds\n$\\geq$" f"{_num(c2_min, 'g')} dB",
              va="top", fontsize=fc.FONT_PT, color=fc.OI["vermillion"])
    ax_h.text(0.03, 0.97, "(d)", transform=ax_h.transAxes, va="top")
    ax_h.text(0.03, 0.80,
              f"median {_num(med, '.2f')} dB\ndecoy C2 rate\n{round(rate * n_dec)}/{n_dec}",
              transform=ax_h.transAxes, va="top", fontsize=fc.FONT_PT)
    ax_h.set_xlim(right=c2_min + 3.6)
    ax_h.set_xlabel(r"$\Delta$RMSE = RMSE(M0) $-$ RMSE(M2) (dB)")
    ax_h.set_ylabel("prior draws")
    for lo, hi, n in zip(bins[:-1], bins[1:], counts, strict=True):
        rows.add("d", "histogram of Delta RMSE (M0 - M2) over prior draws", "SIM-sremu",
                 "bin_center_db", float((lo + hi) / 2), "draws", float(n))
    rows.add("d", "C2 threshold on the median Delta RMSE (protocol)", "ASSUMED", "", "",
             "threshold_db", c2_min, note="pre-registered decision rule")
    rows.add("d", "decoy C2 detection rate (power analysis)", "SIM-sremu", "n_decoys",
             float(n_dec), "rate", rate)

    inputs = [
        {"path": F4_CSV, "data_class": "DIG",
         "note": "measured_dbm DIG; pred_* SIM-sremu; residual DIG minus SIM-sremu",
         "provenance": fc.study_provenance(fc.input_path(f"{RUN}/meta.json"))},
        {"path": F4_META, "data_class": "SIM-sremu"},
        {"path": DELTA_CSV, "data_class": "SIM-sremu"},
        {"path": TABLE_V, "data_class": "DIG"},
        {"path": PROTOCOL, "data_class": "ASSUMED",
         "note": "pre-registered protocol (geometry prior, decision rules)"},
    ]
    assumptions = [
        "committed unblinded V-LAB run; geometry, Gamma magnitudes and antennas are prior "
        "draws (ASSUMED), not the lab's geometry",
        "cell edges drawn at the midpoints between the inferred theta and r centres",
        f"residual colour scale clipped at +-{RES_CLIP_DB:g} dB; the clipped cell is annotated",
        "label: consistency check under geometric uncertainty (naming rule of the protocol)",
    ]
    fc.write_outputs(FID, fig, rows, args=args, inputs=inputs, assumptions=assumptions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
