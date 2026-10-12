"""F5 -- (a) CFO estimation error vs applied offset with noise off (V-CFO-1) and the generic
integer-samples-per-tick pitfall; (b) frequency-estimator RMSE vs the Cramer-Rao bound
(V-CFO-4); (c) BER vs Eb/N0 through the emulator with Clopper-Pearson intervals (E-F).

Data: the committed V-CFO run (validation/cfo/results/<run>) and the committed E-F BER result
(paper/sweeps/results/E-F/ber). DESIGN D24: the pitfall series is the emulator's own CFO-free
output re-timed with an integer number of samples per 2 ms tick (11 instead of 11.76 at
5880 S/s), shown as a generic design lesson; it is not the output of any other tool and the
digitized rows of the study are not plotted. The analytic references are the line
(11.76/11 - 1) f, the CRB of a tone in complex white Gaussian noise, the small-error variance
of the lag-1 estimator, and Q(sqrt(2 Eb/N0)).
"""

from __future__ import annotations

import csv
import json
import math
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figcommon as fc

FID = "F5"
CFO_RUN = "../sremu/validation/cfo/results/20261012T000448Z_vcfo"
SWEEP = f"{CFO_RUN}/vcfo_sweep_noise_free.csv"
CRB = f"{CFO_RUN}/vcfo4_rmse_vs_crb.csv"
CFO_META = f"{CFO_RUN}/meta.json"
BER = "sweeps/results/E-F/ber/ber.csv"
BER_META = "sweeps/results/E-F/ber/meta.json"


def _read(rel: str) -> list[dict[str, str]]:
    return list(csv.DictReader(fc.input_path(rel).open(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    args = fc.parse_args(FID, argv)
    fc.setup_style()
    import matplotlib.pyplot as plt
    from scipy.special import erfc

    rows = fc.Rows()
    sw = _read(SWEEP)
    crb = _read(CRB)
    ber = _read(BER)

    fig, (ax_a, ax_b, ax_c) = plt.subplots(1, 3, figsize=(fc.DOUBLE_IN, 2.15),
                                           gridspec_kw={"wspace": 0.42})
    # ------------------------------------------------------------------ (a)
    f = np.array([float(r["actual_cfo_hz"]) for r in sw])
    e_ls = np.array([float(r["engine_ls_hz"]) for r in sw]) - f
    e_lag = np.array([float(r["engine_lag1_hz"]) for r in sw]) - f
    e_tick = np.array([float(r["legacy_ls_hz"]) for r in sw]) - f
    # samples per tick, needed / delivered, read from the study (not typed here)
    nf = json.loads(fc.input_path(CFO_META).read_text(encoding="utf-8"))["numbers"]["noise_free"]
    spt = int(nf["legacy_samples_per_tick"])
    scale = float(nf["legacy_apparent_scale"])
    needed = float(Fraction(scale).limit_denominator(1000) * spt)
    ax_a.plot(f, (scale - 1.0) * f, "-", color=fc.OI["black"], lw=0.8,
              label=rf"$({needed:g}/{spt}-1)\,f$")
    ax_a.plot(f, e_tick, "s", color=fc.OI["vermillion"], mfc="none",
              label=f"{spt} samples per tick")
    ax_a.plot(f, e_ls, "o", color=fc.OI["blue"], ms=3, label="sremu (exact counts)")
    ax_a.set_xlabel("applied offset $f$ (Hz)")
    ax_a.set_ylabel(r"estimate $-$ applied (Hz)")
    # head room above the data so that the legend rows sit clear of the 11-samples staircase
    ax_a.set_ylim(-112, 140)
    ax_a.legend(loc="upper left", handlelength=1.2)
    ax_a.text(0.97, 0.04, f"sremu: max $|$error$|$\n{fc.sci_tex(float(np.max(np.abs(e_ls))))} "
              "Hz (LS)",
              transform=ax_a.transAxes, ha="right", va="bottom")
    fc.panel_label(ax_a, "(a)", x=0.03, y=0.38)  # below the zero line of the sremu points
    for i in range(len(f)):
        rows.add("a", "sremu, unwrapped-phase LS", "SIM-sremu", "cfo_hz", f[i], "error_hz",
                 float(e_ls[i]))
        rows.add("a", "sremu, lag-1", "SIM-sremu", "cfo_hz", f[i], "error_hz", float(e_lag[i]))
        rows.add("a", "integer samples per tick (sremu output re-timed), LS", "SIM-sremu",
                 "cfo_hz", f[i], "error_hz", float(e_tick[i]),
                 note="generic pitfall, D24; not another tool's output")
        rows.add("a", f"({needed:g}/{spt} - 1) f", "ANALYTIC", "cfo_hz", f[i], "error_hz",
                 float((scale - 1.0) * f[i]))
    # ------------------------------------------------------------------ (b)
    snr = np.array([float(r["snr_db"]) for r in crb])
    c = np.array([float(r["crb_std_hz"]) for r in crb])
    pg = np.array([float(r["periodogram_rmse_pooled_hz"]) for r in crb])
    lag = np.array([float(r["lag1_rmse_pooled_hz"]) for r in crb])
    lagt = np.array([float(r["lag1_theory_std_hz"]) for r in crb])
    ss = np.linspace(snr.min(), snr.max(), 41)
    n = float(crb[0]["n_samples"])
    fs = float(crb[0]["fs_sps"])

    def crb_fn(s_db: np.ndarray) -> np.ndarray:
        s = 10 ** (s_db / 10)
        return fs / (2 * math.pi) * np.sqrt(6.0 / (s * n * (n * n - 1)))

    def lag_fn(s_db: np.ndarray) -> np.ndarray:
        s = 10 ** (s_db / 10)
        var = 1 / (s * (n - 1) ** 2) + 1 / (2 * s * s * (n - 1))
        return fs / (2 * math.pi) * np.sqrt(var)

    ax_b.semilogy(ss, crb_fn(ss), "-", color=fc.OI["black"], lw=0.9, label="CRB")
    ax_b.semilogy(ss, 1.5 * crb_fn(ss), ":", color=fc.OI["black"], lw=0.8,
                  label=r"$1.5\times$CRB")
    ax_b.semilogy(snr, pg, "o", color=fc.OI["blue"], label="periodogram")
    ax_b.semilogy(ss, lag_fn(ss), "--", color=fc.OI["green"], lw=0.9, label="lag-1 theory")
    ax_b.semilogy(snr, lag, "^", color=fc.OI["green"], mfc="none", label="lag-1")
    ax_b.set_xlabel("per-sample SNR (dB)")
    ax_b.set_ylabel("RMSE of $\\hat f$ (Hz)")
    ax_b.set_xticks(snr)
    ax_b.set_ylim(1e-3, 3e3)
    ax_b.legend(loc="upper right", handlelength=1.4, labelspacing=0.2)
    fc.panel_label(ax_b, "(b)", x=0.03, y=0.10)
    for i in range(len(snr)):
        rows.add("b", "CRB", "ANALYTIC", "snr_db", snr[i], "std_hz", float(c[i]))
        rows.add("b", "periodogram + parabolic, pooled RMSE", "SIM-sremu", "snr_db", snr[i],
                 "rmse_hz", float(pg[i]),
                 note=f"ratio max point {crb[i]['periodogram_rmse_over_crb_max_point']}")
        rows.add("b", "lag-1, pooled RMSE", "SIM-sremu", "snr_db", snr[i], "rmse_hz",
                 float(lag[i]))
        rows.add("b", "lag-1 small-error theory", "ANALYTIC", "snr_db", snr[i], "std_hz",
                 float(lagt[i]))
    # ------------------------------------------------------------------ (c)
    eb = np.linspace(0, 10, 101)
    theory = 0.5 * erfc(np.sqrt(10 ** (eb / 10)))
    ax_c.semilogy(eb, theory, "-", color=fc.OI["black"], lw=0.9,
                  label=r"$Q(\sqrt{2E_b/N_0})$")
    styles = {"bpsk": (fc.OI["blue"], "o", -0.12), "qpsk": (fc.OI["vermillion"], "s", 0.12)}
    for mod, (col, mk, dx) in styles.items():
        rr = [r for r in ber if r["modulation"] == mod]
        x = np.array([float(r["ebn0_db"]) for r in rr])
        y = np.array([float(r["genie_ber"]) for r in rr])
        lo = np.array([float(r["genie_ci95_low"]) for r in rr])
        hi = np.array([float(r["genie_ci95_high"]) for r in rr])
        yl = np.array([float(r["ls64_ber"]) for r in rr])
        ax_c.errorbar(x + dx, y, yerr=[y - lo, hi - y], fmt=mk, color=col, ms=3,
                      elinewidth=0.8, capsize=1.5, label=f"{mod.upper()}, genie")
        ax_c.plot(x + 2 * dx, yl, mk, color=col, mfc="none", ms=3,
                  label=f"{mod.upper()}, 64-pilot LS")
        for r in rr:
            rows.add("c", f"{mod} genie BER", "SIM-sremu", "ebn0_db", float(r["ebn0_db"]),
                     "ber", float(r["genie_ber"]), float(r["genie_ci95_low"]),
                     float(r["genie_ci95_high"]),
                     note=f"bits={r['bits']}; errors={r['genie_errors']}; "
                          f"theory_in_ci95={r['genie_theory_in_ci95']}")
            rows.add("c", f"{mod} 64-pilot LS BER", "SIM-sremu", "ebn0_db",
                     float(r["ebn0_db"]), "ber", float(r["ls64_ber"]), float(r["ls64_ci95_low"]),
                     float(r["ls64_ci95_high"]),
                     note=f"errors={r['ls64_errors']}; theory_in_ci95={r['ls64_theory_in_ci95']}")
            rows.add("c", "Q(sqrt(2 Eb/N0))", "ANALYTIC", "ebn0_db", float(r["ebn0_db"]), "ber",
                     float(r["theory_ber"]))
    rows.add("c", "lab-like scene: lab state amplitudes |Gamma| = 1", "ASSUMED", "", "", "",
             float("nan"), note="E-F scene assumption")
    ax_c.set_xlabel("$E_b/N_0$ (dB)")
    ax_c.set_ylabel("bit error rate")
    ax_c.set_xticks([0, 2, 4, 6, 8, 10])
    ax_c.set_ylim(1e-6, 0.2)
    ax_c.legend(loc="lower left", handlelength=1.2, fontsize=fc.FONT_PT, labelspacing=0.25)
    fc.panel_label(ax_c, "(c)", x=0.88)

    inputs = [
        {"path": SWEEP, "data_class": "SIM-sremu",
         "note": "engine_* and legacy_* columns plotted (legacy_* = sremu output re-timed by the "
                 "integer-samples-per-tick adapter); dig_* columns not plotted (D24)",
         "provenance": fc.study_provenance(fc.input_path(CFO_META))},
        {"path": CRB, "data_class": "SIM-sremu", "note": "CRB columns are ANALYTIC"},
        {"path": CFO_META, "data_class": "SIM-sremu"},
        {"path": BER, "data_class": "SIM-sremu", "note": "theory_ber column is ANALYTIC",
         "provenance": fc.study_provenance(fc.input_path(BER_META))},
        {"path": BER_META, "data_class": "ASSUMED", "note": "lab |Gamma| ASSUMED; genie = "
                                                           "oracle CSI"},
    ]
    assumptions = [
        "(a) BPSK burst: 200-symbol header, 2389 samples at 5880 S/s; estimators on header "
        "samples; the pitfall series re-times the engine's output with 11 samples per 2 ms "
        "tick (D24: generic lesson, not another simulator)",
        "(b) periodogram + parabolic estimator, N = 2000 header samples, 7 CFO points x 200 "
        "keyed trials per SNR; noise is the engine's physical k T0 F f_s",
        "(c) static lab-like LOS + RIS scene, Eb/N0 set through the TX power against the "
        "physical noise; >= 200 errors or 2e7 bits per point; Clopper-Pearson 95 % intervals",
    ]
    fc.write_outputs(FID, fig, rows, args=args, inputs=inputs, assumptions=assumptions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
