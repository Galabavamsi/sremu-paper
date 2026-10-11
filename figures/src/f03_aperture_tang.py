"""F3 -- (a) fixed-aperture refinement of K1 (PH-04); (b) Tang et al. model curves, sremu and
digitized measurements (V-TANG-1, V-TANG-2).

(a) is computed here by calling sremu (geometry, quadrature, Richardson check, K1 terms) for
the PH-04 geometry: a 1 m^2 plate at 3.5 GHz, Gamma = 1, Tx at 30 deg incidence, subdivided
into N = n x n cells. Far specular (200 m): the shipped ``auto`` cell model vs the far-field
plate closed form A cos(theta)/(4 pi d1 d2). Near anomalous (5 m, Rx at -10 deg): the
Richardson-converged ``po_patch`` model and a forced ``point`` model vs the continuous
physical-optics plate integral, evaluated here independently of sremu with 200 x 200
Gauss-Legendre nodes (ANALYTIC).

(b) plots the committed V-TANG run (validation/tang/results/<run>/vtang_points.csv): the
published Eq. 9 curves and the chamber measurements of Tang et al. are vector-extracted from
the paper's Fig. 10 (DIG), the link-budget parameters are the paper's tables (REPORTED), and
the engine values are SIM-sremu.
"""

from __future__ import annotations

import csv
import math
import sys
import warnings
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figcommon as fc

FID = "F3"
TANG_RUN = "../sremu/validation/tang/results/20261010T232551Z_vtang"
TANG_POINTS = f"{TANG_RUN}/vtang_points.csv"
TANG_META = f"{TANG_RUN}/meta.json"

FC_HZ = 3.5e9
SIDES = (4, 8, 16, 32, 64, 128)
INC_DEG = 30.0


def _db20(h: complex) -> float:
    return 20.0 * math.log10(abs(h))


def refinement() -> dict[str, list[float]]:
    from sremu.physics.geometry import PanelGrid, Pose
    from sremu.physics.quadrature import (
        LinkSample,
        build_quadrature,
        resolve_cell_model,
        richardson_check,
    )
    from sremu.physics.ris_kernel import k1_terms
    from sremu.units import C0

    lam = C0 / FC_HZ
    inc = math.radians(INC_DEG)
    pose = Pose([0, 0, 0], [0, 0, 1], [1, 0, 0])

    def geom(d1: float, d2: float, out_deg: float) -> tuple[np.ndarray, np.ndarray]:
        o = math.radians(out_deg)
        return (np.array([-d1 * math.sin(inc), 0.0, d1 * math.cos(inc)]),
                np.array([d2 * math.sin(o), 0.0, d2 * math.cos(o)]))

    def level_db(n: int, tx: np.ndarray, rx: np.ndarray, model: str) -> float:
        grid = PanelGrid(pose, n, n, 1.0 / n, 1.0 / n)
        level = 0
        if model == "po_patch":
            level = richardson_check(grid, lam, [LinkSample("l", tx, rx, FC_HZ)]).level
        quad = build_quadrature(grid, model, lam, level=level)
        return _db20(np.sum(k1_terms(tx, rx, quad.points, quad.areas, pose.normal, FC_HZ)))

    out: dict[str, list[float]] = {"N": [float(n * n) for n in SIDES]}
    # far specular, auto policy, vs the far-field closed form
    tx, rx = geom(200.0, 200.0, INC_DEG)
    far_ref = _db20(math.cos(inc) / (4 * math.pi * 200.0 * 200.0))
    out["far_auto"] = [level_db(n, tx, rx, resolve_cell_model("auto", 1 / n, 1 / n, lam))
                       - far_ref for n in SIDES]
    out["far_auto_model"] = [resolve_cell_model("auto", 1 / n, 1 / n, lam) for n in SIDES]
    out["far_ref_db"] = [far_ref]
    # near anomalous: continuous PO integral, independent numpy (ANALYTIC)
    tx, rx = geom(5.0, 5.0, -10.0)
    x, w = np.polynomial.legendre.leggauss(200)
    gx, gy = np.meshgrid(0.5 * x, 0.5 * x, indexing="ij")
    pts = np.column_stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)])
    area = np.outer(0.5 * w, 0.5 * w).ravel()
    r1 = np.linalg.norm(pts - tx, axis=1)
    r2 = np.linalg.norm(pts - rx, axis=1)
    obl = np.sqrt((tx[2] / r1) * (rx[2] / r2))
    k = 2 * math.pi / lam
    near_ref = _db20(np.sum(area * obl / (4 * math.pi * r1 * r2) * 1j
                            * np.exp(-1j * k * (r1 + r2))))
    out["near_ref_db"] = [near_ref]
    out["near_po_patch"] = [level_db(n, tx, rx, "po_patch") - near_ref for n in SIDES]
    with warnings.catch_warnings():  # forced point on cells > lambda/2 warns by design
        warnings.simplefilter("ignore")
        out["near_point"] = [level_db(n, tx, rx, "point") - near_ref for n in SIDES]
    out["pitch_over_lambda"] = [(1.0 / n) / lam for n in SIDES]
    return out


def main(argv: list[str] | None = None) -> int:
    args = fc.parse_args(FID, argv)
    fc.setup_style()
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec
    from matplotlib.lines import Line2D

    rows = fc.Rows()
    ref = refinement()
    tang = list(csv.DictReader(fc.input_path(TANG_POINTS).open(encoding="utf-8")))

    fig = plt.figure(figsize=(fc.DOUBLE_IN, 2.45))
    gs = GridSpec(2, 2, figure=fig, width_ratios=[1.0, 1.75], wspace=0.32, hspace=0.12)
    ax_far = fig.add_subplot(gs[0, 0])
    ax_near = fig.add_subplot(gs[1, 0], sharex=ax_far)
    ax_t = fig.add_subplot(gs[:, 1])

    n = ref["N"]
    ax_far.axhline(0.0, color=fc.OI["black"], lw=0.8, label="closed form")
    ax_far.plot(n, ref["far_auto"], "o-", color=fc.OI["blue"], label="K1, auto")
    ax_far.set_ylim(-0.05, 0.05)
    ax_far.set_ylabel(r"$\Delta P$ (dB)")
    ax_far.legend(loc="lower right", ncol=2, title="far specular, 200 m", alignment="left")
    plt.setp(ax_far.get_xticklabels(), visible=False)
    fc.panel_label(ax_far, "(a)")

    ax_near.axhline(0.0, color=fc.OI["black"], lw=0.8, label="PO integral")
    ax_near.plot(n, ref["near_po_patch"], "o-", color=fc.OI["blue"], label="K1, po_patch")
    ax_near.plot(n, ref["near_point"], "s--", color=fc.OI["vermillion"], mfc="none",
                 label="K1, point (forced)")
    ax_near.set_xscale("log", base=4)
    ax_near.set_xticks(n)
    ax_near.set_xticklabels([f"{int(v)}" for v in n])
    ax_near.set_ylim(-3, 33)
    ax_near.set_xlabel("cells $N$ on a fixed 1 m$^2$ aperture")
    ax_near.set_ylabel(r"$\Delta P$ (dB)")
    ax_near.legend(loc="upper right", title="near anomalous, 5 m", alignment="left")

    for i, nn in enumerate(n):
        rows.add("a-far", "K1 auto minus closed form", "SIM-sremu", "N", nn, "delta_db",
                 ref["far_auto"][i], note=f"cell_model={ref['far_auto_model'][i]}")
        rows.add("a-near", "K1 po_patch (Richardson) minus PO integral", "SIM-sremu", "N", nn,
                 "delta_db", ref["near_po_patch"][i])
        rows.add("a-near", "K1 point (forced) minus PO integral", "SIM-sremu", "N", nn,
                 "delta_db", ref["near_point"][i],
                 note=f"pitch/lambda={ref['pitch_over_lambda'][i]:.4f}")
    rows.add("a-far", "far-field plate closed form", "ANALYTIC", "N", "all", "level_db",
             ref["far_ref_db"][0])
    rows.add("a-near", "continuous PO plate integral (200x200 Gauss-Legendre)", "ANALYTIC",
             "N", "all", "level_db", ref["near_ref_db"][0])

    panels = sorted({r["panel"] for r in tang})
    colors = [fc.OI["blue"], fc.OI["vermillion"], fc.OI["green"], fc.OI["purple"]]
    for p, col in zip(panels, colors, strict=True):
        pr = sorted((r for r in tang if r["panel"] == p), key=lambda r: float(r["d2_m"]))
        d2 = [float(r["d2_m"]) for r in pr]
        pub = [float(r["published_eq9_dbm"]) for r in pr]
        eng = [float(r["engine_dbm"]) for r in pr]
        meas = [float(r["measured_dbm"]) for r in pr]
        lab = f"$d_1$ = {float(pr[0]['d1_m']):g} m, {float(pr[0]['theta_deg']):g}$^\\circ$"
        ax_t.plot(d2, pub, "-", color=col, lw=1.0)
        ax_t.plot(d2, eng, "o", color=col, mfc="none", ms=4.5, mew=0.8)
        ax_t.plot(d2, meas, "^", color=col, ms=3.5, label=lab)
        for x, a, b, c in zip(d2, pub, eng, meas, strict=True):
            rows.add(f"b-{p}", "published Eq. 9 model curve (vector-extracted)", "DIG", "d2_m",
                     x, "p_rx_dbm", a)
            rows.add(f"b-{p}", "sremu K1 (auto cell model)", "SIM-sremu", "d2_m", x,
                     "p_rx_dbm", b)
            rows.add(f"b-{p}", "chamber measurement (digitized)", "DIG", "d2_m", x,
                     "p_rx_dbm", c)
    rows.add("b", "link budget P_T + G_t G_r G_line (Tang Tables III/IV)", "REPORTED",
             "", "", "", float("nan"), note="P_T 20 dBm; measured G_t G_r G_line 24.0 dB")
    ax_t.set_xlabel("RIS-to-receiver distance $d_2$ (m)")
    ax_t.set_ylabel("received power (dBm)")
    leg1 = ax_t.legend(loc="upper right", title=None, ncol=2, columnspacing=1.0)
    style = [Line2D([], [], color="k", lw=1.0, label="published model"),
             Line2D([], [], color="k", ls="none", marker="o", mfc="none", ms=4.5,
                    label="sremu"),
             Line2D([], [], color="k", ls="none", marker="^", ms=3.5, label="measured")]
    ax_t.add_artist(leg1)
    ax_t.legend(handles=style, loc="lower left", ncol=3)
    fc.panel_label(ax_t, "(b)", x=0.015)

    inputs = [
        {"path": TANG_POINTS, "data_class": "DIG",
         "note": "published_eq9_dbm and measured_dbm are DIG (Tang 2022 Fig. 10); engine_dbm "
                 "is SIM-sremu",
         "provenance": fc.study_provenance(fc.input_path(TANG_META))},
        {"path": TANG_META, "data_class": "REPORTED",
         "note": "run metadata incl. link-budget parameters from Tang 2022 Tables III/IV"},
    ]
    assumptions = [
        "(a) PH-04 geometry: 1 m^2 plate, 3.5 GHz, Gamma = 1, Tx at 30 deg; computed with "
        "the installed sremu at plot time",
        "(a) near anomalous reference: continuous PO plate integral with the default "
        "obliquity sqrt(cos cos) and factor j, 200 x 200 Gauss-Legendre nodes (independent "
        "numpy, not sremu)",
        "(b) committed V-TANG run; Tang Fig. 10 x positions snapped to the nominal 0.5 m grid "
        "by the study",
    ]
    extra = {"panel_a_values": ref}
    fc.write_outputs(FID, fig, rows, args=args, inputs=inputs, assumptions=assumptions,
                     extra=extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
