"""F12 -- E-D FM-band RIS dimensioning: model-based dimensioning, not validated.

Data: the committed V-FM / E-D run (validation/fm/results/<run>/vfm_ed_map.csv). 52 km link at
100 MHz, TX at 200 m; RIS of 30 x 30 cells of 0.25 m (56.25 m^2), focused, lateral geometry,
ground image expansion on both RIS hops. Required aperture for a RIS path as strong as the
direct path, by coherent-aperture scaling [DERIVED], against the knife-edge loss of a ridge
2 km from the receiver on the LOS ray. Direct path: flat-earth two-ray plus a coherent knife
edge (primary, sremu); the ITU-R P.526 smooth-earth path plus the same edge is sremu's
EXPERIMENTAL module, shown as a labelled sensitivity row only. Ground constants are ASSUMED.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figcommon as fc

FID = "F12"
RUN = "../sremu/validation/fm/results/20261012T000448Z_vfm"
MAP = f"{RUN}/vfm_ed_map.csv"
META = f"{RUN}/meta.json"
A0 = 56.25
D2 = ((500.0, "0.5 km", fc.OI["blue"]), (1000.0, "1 km", fc.OI["vermillion"]),
      (2000.0, "2 km", fc.OI["green"]))
HR = ((1.5, "-", "o"), (10.0, "--", "s"))


def main(argv: list[str] | None = None) -> int:
    args = fc.parse_args(FID, argv)
    fc.setup_style()
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    data = [r for r in csv.DictReader(fc.input_path(MAP).open(encoding="utf-8"))
            if r["geometry"] == "lateral" and r["ground_on_ris_hops"] == "true"]
    rows = fc.Rows()
    fig, ax = plt.subplots(figsize=(fc.SINGLE_IN, 2.55))
    for d2, dlab, col in D2:
        for hr, ls, _mk in HR:
            rr = sorted((r for r in data if float(r["d2_m"]) == d2 and float(r["h_r_m"]) == hr
                         and r["baseline"] == "two_ray_knife_edge"),
                        key=lambda r: float(r["nu"]))
            x = [float(r["knife_edge_loss_db"]) for r in rr]
            y = [float(r["a_req_equal_power_m2"]) for r in rr]
            ax.semilogy(x, y, ls, color=col, lw=1.0)
            for r in rr:
                rows.add("map", f"A_req, two-ray + knife edge, d2 {dlab}, h_r {hr:g} m",
                         "SIM-sremu", "knife_edge_loss_db", float(r["knife_edge_loss_db"]),
                         "a_req_m2", float(r["a_req_equal_power_m2"]),
                         note=f"nu={r['nu']}; direct {r['pl_direct_db']} dB; RIS(A0) "
                              f"{r['pl_ris_a0_db']} dB")
    # EXPERIMENTAL sensitivity: P.526 + knife edge, d2 = 1 km
    for hr, ls, _mk in HR:
        rr = sorted((r for r in data if float(r["d2_m"]) == 1000.0 and float(r["h_r_m"]) == hr
                     and r["baseline"] == "p526_plus_knife_edge_experimental"),
                    key=lambda r: float(r["nu"]))
        x = [float(r["knife_edge_loss_db"]) for r in rr]
        y = [float(r["a_req_equal_power_m2"]) for r in rr]
        ax.semilogy(x, y, ls, color=fc.OI["grey"], lw=1.6, alpha=0.8)
        for r in rr:
            rows.add("map", f"A_req, P.526 + knife edge (EXPERIMENTAL), d2 1 km, h_r {hr:g} m",
                     "SIM-sremu", "knife_edge_loss_db", float(r["knife_edge_loss_db"]),
                     "a_req_m2", float(r["a_req_equal_power_m2"]), note="EXPERIMENTAL module")
    ax.axhline(A0, color=fc.OI["black"], lw=0.9, ls=":")
    ax.text(26.5, A0 * 1.15, r"56.25 m$^2$ panel", ha="right", va="bottom")
    rows.add("map", "panel aperture A0", "ANALYTIC", "", "", "a_m2", A0,
             note="30 x 30 cells of 0.25 m")
    rows.add("map", "ground eps_r 15, sigma 1e-3 S/m, H polarization", "ASSUMED", "", "", "",
             float("nan"), note="P.526 module defaults")
    ax.set_xlabel("knife-edge loss of the obstruction on the LOS (dB)")
    ax.set_ylabel(r"required aperture $A_{\mathrm{req}}$ (m$^2$)")
    ax.set_xlim(5.5, 27.5)
    ax.set_xticks([10, 15, 20, 25])
    ax.set_ylim(1.0, 2e4)
    h1 = [Line2D([], [], color=c, lw=1.0, label=f"$d_2$ = {lab}") for _, lab, c in D2]
    h2 = [Line2D([], [], color="k", ls=ls, lw=1.0, label=f"$h_r$ = {hr:g} m")
          for hr, ls, _ in HR]
    h3 = [Line2D([], [], color=fc.OI["grey"], lw=1.6, label="P.526, 1 km (EXPERIMENTAL)")]
    ax.legend(handles=h1 + h2 + h3, loc="lower left", ncol=2, columnspacing=0.8,
              handlelength=1.6, labelspacing=0.15)
    inputs = [
        {"path": MAP, "data_class": "SIM-sremu",
         "note": "model-based dimensioning, not validated; P.526 rows EXPERIMENTAL",
         "provenance": fc.study_provenance(fc.input_path(META))},
        {"path": META, "data_class": "ASSUMED", "note": "ground constants ASSUMED"},
    ]
    assumptions = [
        "model-based dimensioning, not validated (DESIGN 10.12)",
        "lateral RIS placement [ENG]; ridge 2 km from the RX [ENG]; RIS legs not diffracted",
        "A_req by coherent-aperture scaling of the focused 56.25 m^2 panel [DERIVED]",
        "P.526 + knife edge: sremu's experimental module, sensitivity row only",
    ]
    fc.write_outputs(FID, fig, rows, args=args, inputs=inputs, assumptions=assumptions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
