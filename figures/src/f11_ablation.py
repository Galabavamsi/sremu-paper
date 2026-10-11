"""F11 -- E-C non-ideality ablation (batch path, exact optimum per alphabet).

Data: the committed E-C result (paper/sweeps/results/E-C/table8.csv). Bars: loss of the
optimized RIS term relative to ideal continuous phase (power-averaged over each geometry's
points) in four settings: the lab-like geometry (30-point polar grid), the synthetic 16 x 16
half-wavelength panel at 3.5 GHz with a near (5 m) and a far (500 m) receiver, and the blocked
corridor (E-A A1, h = 2 m, averaged over the random-waypoint stationary density). Black
ticks: the large-N closed-form loss eta of the alphabet (ANALYTIC; none for the stuck-cell and
ideal-design rows). Lab state amplitudes are ASSUMED; the Tang RIS1 alphabet is the published
table (REPORTED) applied at each geometry's carrier.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figcommon as fc

FID = "F11"
TABLE = "sweeps/results/E-C/table8.csv"
META = "sweeps/results/E-C/meta.json"
ROWS = (
    ("ideal_1bit", "ideal 1-bit", "ANALYTIC"),
    ("ideal_2bit", "ideal 2-bit", "ANALYTIC"),
    ("lab_mag1", r"lab states, $|\Gamma_2|$ = 1", "ASSUMED"),
    ("lab_mag2_0p7", r"lab states, $|\Gamma_2|$ = 0.7", "ASSUMED"),
    ("lab_mag2_0p5", r"lab states, $|\Gamma_2|$ = 0.5", "ASSUMED"),
    ("tang_ris1", "Tang RIS1 states", "REPORTED"),
    ("abeywickrama_b2", "phase-dependent amplitude", "ANALYTIC"),
    ("lab_dead10", "lab, 10 % dead cells", "ASSUMED"),
    ("lab_stuck10", "lab, 10 % stuck cells", "ASSUMED"),
    ("lab_eff0p5", "lab, efficiency 0.5", "ASSUMED"),  # eta is the large-N loss here
    ("ideal_designed_on_lab", "ideal design on lab states", "ASSUMED"),
)
GEOMS = (("lab", "lab-like", fc.OI["blue"]),
         ("synth_near", "synthetic, 5 m", fc.OI["vermillion"]),
         ("synth_far", "synthetic, 500 m", fc.OI["orange"]),
         ("corridor", "corridor", fc.OI["green"]))


def main(argv: list[str] | None = None) -> int:
    args = fc.parse_args(FID, argv)
    fc.setup_style()
    import matplotlib.pyplot as plt

    tab = {r["row"]: r for r in csv.DictReader(fc.input_path(TABLE).open(encoding="utf-8"))}
    rows = fc.Rows()
    fig, ax = plt.subplots(figsize=(fc.SINGLE_IN, 3.45))
    y = np.arange(len(ROWS))
    h = 0.19
    for gi, (gkey, glab, col) in enumerate(GEOMS):
        vals = [float(tab[k][f"loss_ris_db_{gkey}"]) for k, _, _ in ROWS]
        ax.barh(y + (gi - 1.5) * h, vals, height=h, color=col, label=glab, lw=0)
        for (k, _lab, dc), v in zip(ROWS, vals, strict=True):
            rows.add(gkey, f"RIS-term loss vs ideal continuous, {k}", "SIM-sremu", "row", k,
                     "loss_db", v, note=f"input class of the alphabet: {dc}")
    for i, (k, _, dc) in enumerate(ROWS):
        eta = tab[k]["eta_large_n_db"]
        if eta:
            ax.plot([float(eta)] * 2, [i - 2 * h, i + 2 * h], "-", color=fc.OI["black"], lw=1.2)
            rows.add("eta", f"large-N closed-form loss eta, {k}", "ANALYTIC", "row", k,
                     "eta_db", float(eta), note=f"alphabet class {dc}")
    ax.plot([], [], "-", color=fc.OI["black"], lw=1.2, label=r"large-$N$ $\eta$")
    ax.set_yticks(y)
    ax.set_yticklabels([lab for _, lab, _ in ROWS])
    ax.set_ylim(len(ROWS) - 0.5, -0.5)
    ax.set_xlabel("loss of the RIS term vs ideal continuous phase (dB)")
    ax.set_xlim(-7.5, 0)
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower left", bbox_to_anchor=(-0.02, 1.0), ncol=2, handlelength=1.2,
              labelspacing=0.15, columnspacing=0.8, borderaxespad=0.1)
    inputs = [
        {"path": TABLE, "data_class": "SIM-sremu",
         "note": "eta_large_n_db is ANALYTIC; alphabets: ideal (ANALYTIC), lab (ASSUMED "
                 "amplitudes), Tang RIS1 (REPORTED)",
         "provenance": fc.study_provenance(fc.input_path(META))},
        {"path": META, "data_class": "ASSUMED", "note": "lab |Gamma| ASSUMED"},
    ]
    rows.add("classes", "lab state amplitudes", "ASSUMED", "", "", "", float("nan"),
             note="|sigma1| = 1; |sigma2| per row")
    rows.add("classes", "Tang RIS1 published 27 GHz table values", "REPORTED", "", "", "",
             float("nan"), note="applied as an angle-independent alphabet [ENG]")
    assumptions = [
        "loss = power-averaged optimum over the geometry's points relative to ideal continuous "
        "phase, exact finite-alphabet optimum per alphabet",
        "failure rows on the lab |Gamma| = 1 alphabet; the optimizer knows the failed cells; "
        "200 keyed maps per point (lab, synthetic), 10 per position (corridor)",
        "Abeywickrama row: beta_min 0.2, alpha 1.6, phi 0, b = 2 [ENG]",
        "lab-like geometry: SIM, V-LAB prior median Tx; no measured data read",
    ]
    fc.write_outputs(FID, fig, rows, args=args, inputs=inputs, assumptions=assumptions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
