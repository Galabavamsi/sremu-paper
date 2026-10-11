"""F6 -- geometric Doppler through the engine (PH-17) and the interpolation error of the
snapshot grid (PH-18).

Scene (SIM geometry, built here with sremu's physics model, the T9 geometry of the PH-17 test):
3.5 GHz, TX at the origin, RX starting at (50, 0, 0) m and approaching it at 30 m/s, a
0.5 m x 0.5 m panel of 12 x 12 cells (Gamma = 1, built-in ideal 1-bit table, state 0) centred
at (25, 10, 0) m and facing -y; isotropic antennas; a DC tone at 10 kS/s, noise off, lockstep,
numpy reference backend, auto snapshot period T_u and render tiles.

(a) Instantaneous frequency (phase difference of consecutive samples) of the rendered LOS path
(run without the panel) and of the rendered RIS contribution (run with the panel minus run
without it, both at the same T_u), against the closed forms: v f_c / c for the radial LOS
path, and the same estimator applied to an independent numpy sum of the K1 cell terms
(ANALYTIC; written here, not sremu code).

(b) Relative interpolation error max |y - h| / |h| over 1 s, where y is the rendered channel and
h the exact narrowband channel of sremu's own snapshot evaluated at every sample time, and the
engine's verify_interp estimate (exact midpoint snapshots), against T_u. T_u is varied through
the policy's error target (1e-3 is the shipped default; larger targets give larger T_u, with
the T_u cap raised to 0.1 s for this figure only).
"""

from __future__ import annotations

import math
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figcommon as fc

FID = "F6"
FC_HZ = 3.5e9
FS = Fraction(10_000)
DUR_S = 1
V_MPS = 30.0
PANEL_C = np.array([25.0, 10.0, 0.0])
N_SIDE = 12
SIDE_M = 0.5
TARGETS = (1e-3, 3e-3, 1e-2, 3e-2, 1e-1)
NULL_FRAC = 0.1


def build_model(with_panel: bool):
    from sremu.contracts import StreamSpec
    from sremu.physics.geometry import ConstantVelocity, PanelGrid, Pose, Static
    from sremu.physics.propagation import NodeModel, PanelModel, ScenePropagation
    from sremu.physics.response import builtin

    nodes = [
        NodeModel("tx", Static([0.0, 0.0, 0.0]),
                  streams=(StreamSpec("tx.tx0", "tx", FC_HZ, FS),)),
        NodeModel("rx", ConstantVelocity([50.0, 0.0, 0.0], [-V_MPS, 0.0, 0.0]),
                  streams=(StreamSpec("rx.rx0", "rx", FC_HZ, FS),)),
    ]
    panels = []
    if with_panel:
        pose = Pose(PANEL_C, [0.0, -1.0, 0.0], [1.0, 0.0, 0.0])
        pitch = SIDE_M / N_SIDE
        panels.append(PanelModel("ris0", PanelGrid(pose, N_SIDE, N_SIDE, pitch, pitch),
                                 builtin("ideal_bbit(1)"), cell_model="auto"))
    return ScenePropagation(nodes, panels, horizon_s=DUR_S)


def run(model, *, t_u: Fraction | None = None, eps: float = 1e-3,
        t_u_max: Fraction = Fraction(1, 100)) -> tuple[np.ndarray, Fraction | None, float]:
    from sremu.engine.emulator import Emulator, EngineConfig
    from sremu.engine.streams import ToneSource, stream_configs_from_model

    cfgs = stream_configs_from_model(model, {"tx.tx0": {"occupied_bw_hz": 1.0},
                                             "rx.rx0": {"noise_figure_db": 0.0}})
    cfg = EngineConfig(backend="numpy_ref", noise=False, always_active=True, t_u_s=t_u,
                       interp_rel_error=eps, t_u_max_s=t_u_max)
    eng = Emulator(model, streams=cfgs, config=cfg, traffic={"tx.tx0": ToneSource(FS, 0.0, 1.0)})
    cap = eng.capture_accumulator("rx.rx0")
    summary = eng.run(Fraction(DUR_S))
    n = int(FS * DUR_S)
    y = np.zeros(n, np.complex128)
    got = np.zeros(n, bool)
    for a, v in cap:
        lo, hi = max(a, 0), min(a + len(v), n)
        if lo < hi:
            y[lo:hi] = v[lo - a : hi - a]
            got[lo:hi] = True
    if not got.all():
        raise RuntimeError("captured samples missing")
    # device power mapping (DESIGN 3.3): TX full scale 0 dBm, so a digital amplitude of 1 is
    # 1 mW and the RX accumulator is in sqrt(W); divide by sqrt(1 mW) to get h itself
    y /= math.sqrt(1e-3)
    return y, eng.t_u, float(summary.verify_interp_max_rel or float("nan"))


def exact_channel(model) -> np.ndarray:
    """Narrowband channel of sremu's own snapshot at every sample time (state 0 everywhere)."""
    from sremu.contracts import PanelConfig

    n = int(FS * DUR_S)
    cfg = {"ris0": PanelConfig("ris0", np.zeros(N_SIDE * N_SIDE, np.int16))}
    h = np.zeros(n, np.complex128)
    for k in range(n):
        lp = model.combine(model.snapshot(Fraction(k) / FS), cfg)["tx.tx0->rx.rx0"]
        h[k] = np.sum(lp.amp * np.exp(-2j * math.pi * FC_HZ * lp.delay_s))
    return h


def independent_ris(t: np.ndarray) -> np.ndarray:
    """K1 cell sum written here in numpy (not sremu): isotropic antennas, Gamma = 1,
    obliquity sqrt(cos theta_i cos theta_s), factor j, pitch area per cell."""
    c0 = 299_792_458.0
    k = 2 * math.pi * FC_HZ / c0
    pitch = SIDE_M / N_SIDE
    off = (np.arange(N_SIDE) - (N_SIDE - 1) / 2) * pitch
    uu, vv = np.meshgrid(off, off, indexing="ij")
    u_ax, v_ax, nrm = np.array([1.0, 0, 0]), np.array([0, 0, 1.0]), np.array([0, -1.0, 0])
    cells = PANEL_C + uu.reshape(-1, 1) * u_ax + vv.reshape(-1, 1) * v_ax
    tx = np.zeros(3)
    d1 = cells - tx
    r1 = np.linalg.norm(d1, axis=1)
    cos_i = np.abs(d1 @ nrm) / r1
    out = np.zeros(t.size, np.complex128)
    for i, ti in enumerate(t):
        rx = np.array([50.0 - V_MPS * ti, 0.0, 0.0])
        d2 = rx - cells
        r2 = np.linalg.norm(d2, axis=1)
        cos_s = np.abs(d2 @ nrm) / r2
        out[i] = np.sum(pitch * pitch * np.sqrt(cos_i * cos_s) / (4 * math.pi * r1 * r2)
                        * 1j * np.exp(-1j * k * (r1 + r2)))
    return out


def inst_freq(y: np.ndarray) -> np.ndarray:
    return np.angle(y[1:] * np.conj(y[:-1])) * float(FS) / (2 * math.pi)


def main(argv: list[str] | None = None) -> int:
    args = fc.parse_args(FID, argv)
    fc.setup_style()
    import matplotlib.pyplot as plt
    from sremu.units import C0

    rows = fc.Rows()
    m_ris, m_los = build_model(True), build_model(False)
    y_tot, tu_auto, vi_auto = run(m_ris)
    y_los, _, _ = run(m_los, t_u=tu_auto)
    y_ris = y_tot - y_los
    n = y_tot.size
    t = np.arange(n) / float(FS)
    f_los = inst_freq(y_los)
    f_ris = inst_freq(y_ris)
    f_los_cf = V_MPS * FC_HZ / C0
    h_ris_cf = independent_ris(t)
    f_ris_cf = inst_freq(h_ris_cf)
    # away from the nulls of the panel's scattering pattern (where the phase of the cell sum
    # jumps and an instantaneous frequency is undefined): |h_RIS| above NULL_FRAC of its max
    amp = np.abs(h_ris_cf)
    keep = np.minimum(amp[1:], amp[:-1]) > NULL_FRAC * amp.max()
    tm = 0.5 * (t[1:] + t[:-1])

    # (b) error vs T_u
    h = exact_channel(m_ris)
    sweep = []
    for eps in TARGETS:
        y, tu, vi = (y_tot, tu_auto, vi_auto) if eps == 1e-3 else run(
            m_ris, eps=eps, t_u_max=Fraction(1, 10))
        err = float(np.max(np.abs(y - h) / np.abs(h)))
        sweep.append((eps, float(tu), err, vi))

    fig, (ax_a, ax_b) = plt.subplots(2, 1, figsize=(fc.SINGLE_IN, 3.6),
                                     gridspec_kw={"hspace": 0.42})
    stride = 50
    ris_cf_line = np.where(keep, f_ris_cf, np.nan)
    idx = np.arange(0, n - 1, stride)
    idx_r = idx[keep[idx]]
    ax_a.axhline(f_los_cf, color=fc.OI["black"], lw=0.8, label="closed form")
    ax_a.plot(tm, ris_cf_line, "-", color=fc.OI["black"], lw=0.8)
    ax_a.plot(tm[idx], f_los[idx], "o", color=fc.OI["blue"], ms=2.6, label="LOS, rendered")
    ax_a.plot(tm[idx_r], f_ris[idx_r], "s", color=fc.OI["vermillion"], ms=2.6, mfc="none",
              label="RIS, rendered")
    ax_a.set_xlabel("time (s)")
    ax_a.set_ylabel("Doppler shift (Hz)")
    ax_a.set_xlim(0, DUR_S)
    ax_a.set_ylim(-250, 450)
    ax_a.legend(loc="lower left", handlelength=1.4)
    fc.panel_label(ax_a, "(a)", x=0.90, y=0.78)  # clear of the LOS line at the top
    dl = float(np.max(np.abs(f_los - f_los_cf)))
    dr_all = np.abs(f_ris - f_ris_cf)[keep]
    dr = float(np.max(dr_all))
    dr_med = float(np.median(dr_all))
    ax_a.text(0.40, 0.05, f"max $|$deviation$|$\nLOS {dl:.0e} Hz\nRIS {dr:.2f} Hz",
              transform=ax_a.transAxes, ha="left", va="bottom")
    for i in range(0, n - 1, 10):
        rows.add("a", "LOS rendered", "SIM-sremu", "t_s", float(tm[i]), "f_hz", float(f_los[i]))
        rows.add("a", "RIS rendered (with panel minus without)", "SIM-sremu", "t_s",
                 float(tm[i]), "f_hz", float(f_ris[i]),
                 note="" if keep[i] else "near a null of the panel pattern (not plotted)")
        rows.add("a", "LOS closed form v f_c / c", "ANALYTIC", "t_s", float(tm[i]), "f_hz",
                 f_los_cf)
        rows.add("a", "RIS closed form (independent K1 cell sum)", "ANALYTIC", "t_s",
                 float(tm[i]), "f_hz", float(f_ris_cf[i]))

    tus = np.array([s[1] for s in sweep])
    errs = np.array([s[2] for s in sweep])
    vis = np.array([s[3] for s in sweep])
    from matplotlib.ticker import FixedLocator, NullFormatter, ScalarFormatter

    ref = errs[0] * (tus / tus[0]) ** 2
    ax_b.loglog(tus * 1e3, ref, "-", color=fc.OI["grey"], lw=2.2, alpha=0.6,
                label=r"$\propto T_u^2$")
    ax_b.loglog(tus * 1e3, errs, "o-", color=fc.OI["blue"], label="vs exact channel")
    ax_b.loglog(tus * 1e3, vis, "^--", color=fc.OI["green"], mfc="none",
                label="verify_interp")
    ax_b.axhline(1e-3, color=fc.OI["vermillion"], lw=0.9, ls="--",
                 label=r"default $\varepsilon_{\mathrm{rel}}$")
    ax_b.axvline(float(tu_auto) * 1e3, color=fc.OI["black"], lw=0.6, ls="-.")
    ax_b.text(float(tu_auto) * 1e3 * 1.06, 2e-6, "auto $T_u$", fontsize=fc.FONT_PT)
    ax_b.set_xlim(0.6, 10)
    ax_b.xaxis.set_major_locator(FixedLocator([1, 2, 4, 8]))
    ax_b.xaxis.set_major_formatter(ScalarFormatter())
    ax_b.xaxis.set_minor_formatter(NullFormatter())
    ax_b.set_xlabel("snapshot period $T_u$ (ms)")
    ax_b.set_ylabel(r"max $|\Delta h|/|h|$")
    ax_b.set_ylim(1e-6, 1e-1)
    ax_b.legend(loc="upper left", handlelength=1.6, labelspacing=0.2)
    fc.panel_label(ax_b, "(b)", x=0.90, y=0.12)
    for eps, tu, err, vi in sweep:
        note = f"policy target eps={eps:g}"
        rows.add("b", "max |y - h|/|h| over 1 s (h: snapshot at every sample)", "SIM-sremu",
                 "t_u_s", tu, "rel_error", err, note=note)
        rows.add("b", "verify_interp estimate", "SIM-sremu", "t_u_s", tu, "rel_error", vi,
                 note=note)
    rows.add("b", "default target eps_rel", "ANALYTIC", "", "", "rel_error", 1e-3,
             note="code default EngineConfig.interp_rel_error")

    assumptions = [
        "SIM geometry of the PH-17 test (T9) built with sremu's physics model; panel "
        "12 x 12 cells of 41.7 mm at (25, 10, 0) m; state 0 (Gamma = 1) everywhere",
        "noise off; DC tone with a declared 1 Hz band (integer delay); numpy_ref backend",
        "RIS contribution = run with the panel minus run without it at the same T_u",
        "(b) larger T_u through the policy's error target with t_u_max raised to 0.1 s "
        "(this figure only; the shipped cap is 10 ms)",
    ]
    extra = {"v_mps": V_MPS, "fc_hz": FC_HZ, "t_u_auto_s": float(tu_auto),
             "verify_interp_auto": vi_auto,
             "max_dev_los_hz": dl, "max_dev_ris_hz": dr, "median_dev_ris_hz": dr_med,
             "ris_null_frac": NULL_FRAC, "ris_samples_kept_frac": float(keep.mean()),
             "f_los_closed_hz": f_los_cf,
             "f_ris_closed_t0_hz": float(f_ris_cf[0]),
             "sweep": [{"eps": e, "t_u_s": tu, "err_vs_exact": er, "verify_interp": v}
                       for e, tu, er, v in sweep]}
    fc.write_outputs(FID, fig, rows, args=args, inputs=[], assumptions=assumptions,
                     extra=extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
