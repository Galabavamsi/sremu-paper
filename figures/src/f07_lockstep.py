"""F7 -- lockstep scalability (B2 on an AWS c7i.48xlarge; MEAS).

Data: the B2 server-plan run copied in paper/sweeps/results/bench/B2 (run
20261011T084048Z_b2_server, sremu 309c633; provenance in B2/meta.json). Points ran 11 at a
time, each pinned to 16 vCPUs of whole cores in one NUMA node; the isolation reference points
at 1 MS/s (the same configuration rerun alone, run/isolation.csv; five of the six) are drawn
as open markers in (a).

(a) RTF vs links L (log-log) at 1 MS/s with one surface, for every element count N, static
    scene with thermal noise (F1, solid) and with mobility (F3, dashed), with the RTF = 1 line.
(b) RTF vs L at the anchor (N = 240, one surface, 1 MS/s) for the cumulative feature sets
    F0 (channel only), F1 (+ noise), F2 (+ CFO, phase noise), F3 (+ mobility), F4 (+ recording).
(c) Aggregate RX throughput vs L (N = 240, one surface, F1) for the four sample rates.
(d) RTF of one link vs the number of surfaces R = 0, 1, 2, 4 (N = 240): static at 1 and
    23.04 MS/s, with mobility (F3) at 1 MS/s (must-fix 13).
(e) RTF vs the numba thread count (N = 240, one surface, 1 MS/s, F1) at 1, 8, 64 and 128
    links; open markers: the 16-thread default grid points; counts above 16 ran in slots of
    as many vCPUs (tier 4).
(f) B2-F45 (another c7i.48xlarge instance, sremu bf08268): F5 (native ZMQ data plane to a TX
    and an RX client process; end-to-end RTF, dashed) against F4 (the same scene in process,
    solid) of the same run, for four configurations; the bar left of one link shows the range
    and median of the 100 stress runs of the F5 base point (10 concurrent workers).

Aggregate throughput is n_rx * f_s * RTF (all RX streams of a benchmark scene share one rate
group, so this is exact): the aggregate_msps column of points.csv counts samples produced
before the engine's metric restart and is not used (research/analysis/bench_findings.md h).
The failed F5 points of the 309c633 run (a benchmark error) are not drawn; F5 comes from the
B2-F45 rerun in (f), which is not compared with (a)-(e) (another instance and commit).
"""

from __future__ import annotations

import csv
import json
import math
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figcommon as fc

FID = "F7"
B2 = "sweeps/results/bench/B2"
POINTS = f"{B2}/run/points.csv"
ISO = f"{B2}/run/isolation.csv"
SUMMARY = f"{B2}/run/summary.json"
META = f"{B2}/meta.json"
F45 = "sweeps/results/bench/B2-F45"
F45_POINTS = f"{F45}/run/points.csv"
F45_STRESS = f"{F45}/stress_separate/summary.json"
F45_META = f"{F45}/meta.json"
CELLS = ("40", "240", "900", "4096", "16384")
N_COL = {"40": fc.OI["sky"], "240": fc.OI["blue"], "900": fc.OI["green"],
         "4096": fc.OI["orange"], "16384": fc.OI["vermillion"]}
FEATS = (("F0", "F0 channel only"), ("F1", "F1 + noise"), ("F2", "F2 + CFO, PN"),
         ("F3", "F3 + mobility"), ("F4", "F4 + recording"))
RATES = (("1e5", "0.1 MS/s"), ("1e6", "1 MS/s"), ("5.76e6", "5.76 MS/s"),
         ("23.04e6", "23.04 MS/s"))


def _read(rel: str) -> list[dict[str, str]]:
    with fc.input_path(rel).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _series(rows: list[dict[str, str]], n: str, ris: str, fs: str,
            feat: str) -> list[dict[str, str]]:
    return sorted((r for r in rows if (r["N"], r["n_ris"], r["f_s"], r["features"])
                   == (n, ris, fs, feat)), key=lambda r: int(r["L"]))


def _agg_msps(r: dict[str, str]) -> float:
    return int(r["n_rx"]) * float(Fraction(r["f_s"])) * float(r["rtf_median"]) / 1e6


def _log2_links(ax) -> None:  # type: ignore[no-untyped-def]
    ax.set_xscale("log", base=2)
    ax.set_xticks([1, 2, 4, 8, 16, 32, 64, 128])
    ax.set_xticklabels(["1", "2", "4", "8", "16", "32", "64", "128"])
    ax.set_xlim(0.8, 160)
    ax.set_xlabel("links $L$")


def main(argv: list[str] | None = None) -> int:
    args = fc.parse_args(FID, argv)
    fc.setup_style()
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    from matplotlib.lines import Line2D

    allrows = _read(POINTS)
    rows = [r for r in allrows if r["status"] == "ok" and r["threads_explicit"] == "False"
            and r["features"] != "F5"]
    iso = {r["point_id"]: r for r in _read(ISO)}
    summ = json.loads(fc.input_path(SUMMARY).read_text(encoding="utf-8"))
    out = fc.Rows()

    fig, axes = plt.subplots(2, 3, figsize=(fc.DOUBLE_IN, 5.3))
    plt.subplots_adjust(wspace=0.32, hspace=0.42)
    (ax_a, ax_b, ax_c), (ax_d, ax_e, ax_f) = axes

    # (a) RTF vs L for every N: F1 (static) solid, F3 (mobility) dashed
    for feat, ls in (("F1", "-"), ("F3", "--")):
        for n in CELLS:
            ser = _series(rows, n, "1", "1e6", feat)
            x = [int(r["L"]) for r in ser]
            y = [float(r["rtf_median"]) for r in ser]
            ax_a.plot(x, y, ls=ls, color=N_COL[n], lw=0.9, marker="o", ms=2.2)
            for r in ser:
                out.add("a", f"RTF {feat} N={n}", "MEAS", "L", int(r["L"]), "rtf_median",
                        float(r["rtf_median"]),
                        note=f"{r['point_id']}; packed (concurrent points at start "
                             f"{r['concurrent_points_at_start']}); rtf_min_worst="
                             f"{r['rtf_min_worst']}")
    for pid, r in iso.items():
        n, feat, fs = pid.split("_")[1][1:], pid.split("_")[4], pid.split("_")[3][2:]
        if fs != "1e6":
            continue  # the 23.04 MS/s reference point lies outside (a); not drawn (ratio 0.99)
        L = int(pid.split("_")[0][1:])
        ax_a.plot([L], [float(r["rtf_isolated"])], "o", mfc="white", mec=N_COL[n], ms=4.2,
                  mew=0.9, zorder=5)
        out.add("a", f"RTF {feat} N={n} alone (isolation check)", "MEAS", "L", L,
                "rtf_isolated", float(r["rtf_isolated"]),
                note=f"{pid}; ratio isolated/packed {r['ratio_isolated_over_parallel']}")
    ax_a.axhline(1.0, color=fc.OI["black"], lw=0.7, ls=":")
    _log2_links(ax_a)
    ax_a.set_yscale("log")
    ax_a.set_ylim(0.004, 120)
    ax_a.set_ylabel("real-time factor (RTF)")
    h_n = [Line2D([], [], color=N_COL[n], lw=1.2, label=f"$N$ = {int(n):,}".replace(",", " "))
           for n in CELLS]
    h_f = [Line2D([], [], color=fc.OI["grey"], ls="-", lw=1.0, label="static (F1)"),
           Line2D([], [], color=fc.OI["grey"], ls="--", lw=1.0, label="mobility (F3)"),
           Line2D([], [], ls="", marker="o", mfc="white", mec=fc.OI["grey"], ms=4,
                  label="run alone")]
    ax_a.legend(handles=h_n + h_f, loc="upper right", ncol=2, fontsize=fc.FONT_PT,
                handlelength=1.3, columnspacing=0.6, labelspacing=0.12, borderaxespad=0.2)
    fc.panel_label(ax_a, "(a)", y=0.06)

    # (b) feature sets at the anchor
    for (feat, lab), col in zip(FEATS, fc.CYCLE, strict=False):
        ser = _series(rows, "240", "1", "1e6", feat)
        x = [int(r["L"]) for r in ser]
        y = [float(r["rtf_median"]) for r in ser]
        ax_b.plot(x, y, color=col, lw=0.9, marker="o", ms=2.2, label=lab)
        for r in ser:
            out.add("b", f"RTF {feat} N=240 R=1 1 MS/s", "MEAS", "L", int(r["L"]),
                    "rtf_median", float(r["rtf_median"]), note=r["point_id"])
    ax_b.axhline(1.0, color=fc.OI["black"], lw=0.7, ls=":")
    _log2_links(ax_b)
    ax_b.set_yscale("log")
    ax_b.set_ylim(0.008, 12)
    ax_b.set_ylabel("RTF")
    ax_b.legend(loc="lower left", fontsize=fc.FONT_PT, handlelength=1.3, labelspacing=0.12)
    fc.panel_label(ax_b, "(b)", x=0.88)

    # (c) aggregate RX throughput for the four rates
    for (fs, lab), col in zip(RATES, (fc.OI["sky"], fc.OI["blue"], fc.OI["green"],
                                      fc.OI["vermillion"]), strict=True):
        ser = _series(rows, "240", "1", fs, "F1")
        x = [int(r["L"]) for r in ser]
        y = [_agg_msps(r) for r in ser]
        ax_c.plot(x, y, color=col, lw=0.9, marker="o", ms=2.2)
        ax_c.text(x[-1] * 1.12, y[-1], lab, color=col, va="center", ha="left")
        for r, val in zip(ser, y, strict=True):
            out.add("c", f"aggregate RX throughput f_s={fs} N=240 R=1 F1", "MEAS", "L",
                    int(r["L"]), "aggregate_msps", val,
                    note=f"{r['point_id']}; n_rx={r['n_rx']} x f_s x RTF={r['rtf_median']} "
                         "(points.csv aggregate_msps_median not used)")
    _log2_links(ax_c)
    ax_c.set_xlim(0.8, 900)
    ax_c.set_yscale("log")
    ax_c.set_ylim(0.4, 120)
    ax_c.set_ylabel("aggregate RX throughput (MS/s)")
    fc.panel_label(ax_c, "(c)", x=0.88)

    # (d) surfaces: one link, N = 240, R = 0, 1, 2, 4
    rx = [0, 1, 2, 4]
    for fs, feat, lab, col, mk in (("1e6", "F1", "static, 1 MS/s", fc.OI["blue"], "o"),
                                   ("23.04e6", "F1", "static, 23.04 MS/s", fc.OI["vermillion"],
                                    "s"),
                                   ("1e6", "F3", "mobility (F3), 1 MS/s", fc.OI["green"], "^")):
        ser = {int(r["n_ris"]): r for r in rows if (r["N"], r["f_s"], r["features"], r["L"])
               == ("240", fs, feat, "1")}
        if sorted(ser) != rx:
            raise SystemExit(f"F7: surface series {fs} {feat} is not R = 0, 1, 2, 4")
        ax_d.plot(rx, [float(ser[k]["rtf_median"]) for k in rx], color=col, lw=0.9,
                  marker=mk, ms=3, label=lab)
        for k in rx:
            out.add("d", f"RTF one link {feat} f_s={fs} N=240", "MEAS", "surfaces R", k,
                    "rtf_median", float(ser[k]["rtf_median"]), note=ser[k]["point_id"])
    ax_d.axhline(1.0, color=fc.OI["black"], lw=0.7, ls=":")
    ax_d.set_xticks(rx)
    ax_d.set_xlim(-0.3, 4.3)
    ax_d.set_yscale("log")
    ax_d.set_ylim(0.5, 60)
    ax_d.set_xlabel("surfaces $R$ (one link)")
    ax_d.set_ylabel("RTF")
    ax_d.legend(loc="upper right", fontsize=fc.FONT_PT, handlelength=1.3, labelspacing=0.12,
                borderaxespad=0.2)
    fc.panel_label(ax_d, "(d)", y=0.12)

    # (e) numba threads at 1, 8, 64 and 128 links (N = 240, one surface, 1 MS/s, F1)
    thr_rows = [r for r in allrows if r["status"] == "ok" and (
        r["N"], r["n_ris"], r["f_s"], r["features"]) == ("240", "1", "1e6", "F1")]
    for L, col in ((1, fc.OI["sky"]), (8, fc.OI["blue"]), (64, fc.OI["orange"]),
                   (128, fc.OI["vermillion"])):
        pts = sorted(((int(r["threads"]), float(r["rtf_median"]), r) for r in thr_rows
                      if int(r["L"]) == L and (r["threads_explicit"] == "True"
                                               or int(r["threads"]) == 16)),
                     key=lambda t: t[0])
        ax_e.plot([t for t, _, _ in pts], [y for _, y, _ in pts], color=col, lw=0.9,
                  marker="o", ms=2.6, label=f"$L$ = {L}")
        dflt = [(t, y) for t, y, r in pts if r["threads_explicit"] == "False"]
        ax_e.plot([t for t, _ in dflt], [y for _, y in dflt], ls="", marker="o", ms=4.2,
                  mfc="white", mec=col, mew=0.9, zorder=5)
        for t, y, r in pts:
            extra = "; default grid point" if r["threads_explicit"] == "False" else ""
            out.add("e", f"RTF L={L} N=240 R=1 1 MS/s F1", "MEAS", "numba threads", t,
                    "rtf_median", y, note=f"{r['point_id']}; tier {r['tier']}; slot "
                                          f"{r['slot_cpus']} vCPUs{extra}")
    ax_e.axhline(1.0, color=fc.OI["black"], lw=0.7, ls=":")
    ax_e.set_xscale("log", base=2)
    # ticks at the thread counts that ran (the largest, quoted in Section VI, included); a count
    # within 0.9 octave of the previous tick stays unlabelled
    e_ticks: list[int] = []
    for t in sorted({int(r["threads"]) for r in thr_rows
                     if r["threads_explicit"] == "True" or int(r["threads"]) == 16}):
        if not e_ticks or math.log2(t / e_ticks[-1]) >= 0.9:
            e_ticks.append(t)
        elif t == max(int(r["threads"]) for r in thr_rows):
            e_ticks[-1] = t  # the largest count keeps its label
    ax_e.set_xticks(e_ticks)
    ax_e.set_xticklabels([str(t) for t in e_ticks])
    ax_e.xaxis.set_minor_locator(mticker.NullLocator())
    ax_e.set_xlim(0.8, 120)
    ax_e.set_yscale("log")
    ax_e.set_ylim(0.1, 60)
    ax_e.set_xlabel("numba threads $T$")
    ax_e.set_ylabel("RTF")
    ax_e.legend(loc="upper right", ncol=2, fontsize=fc.FONT_PT, handlelength=1.2,
                columnspacing=0.6, labelspacing=0.12, borderaxespad=0.2)
    fc.panel_label(ax_e, "(e)", y=0.12)

    # (f) B2-F45: F5 end-to-end vs F4 of the same run
    frows = [r for r in _read(F45_POINTS) if r["status"] == "ok"]
    for (n, ris, fs), lab, col in (
            (("240", "1", "1e6"), "$N$ = 240, 1 MS/s", fc.OI["blue"]),
            (("4096", "1", "1e6"), "$N$ = 4096, 1 MS/s", fc.OI["orange"]),
            (("240", "1", "5.76e6"), "5.76 MS/s", fc.OI["green"]),
            (("240", "2", "23.04e6"), "23.04 MS/s, $R$ = 2", fc.OI["vermillion"])):
        for feat, ls in (("F4", "-"), ("F5", "--")):
            ser = _series(frows, n, ris, fs, feat)
            x = [int(r["L"]) for r in ser]
            y = [float(r["rtf_median"]) for r in ser]
            ax_f.plot(x, y, color=col, ls=ls, lw=0.9, marker="o", ms=2.2,
                      label=lab if feat == "F4" else None)
            for r in ser:
                out.add("f", f"RTF {feat} N={n} R={ris} f_s={fs} (B2-F45)", "MEAS", "L",
                        int(r["L"]), "rtf_median", float(r["rtf_median"]),
                        note=f"{r['point_id']}; {r['rtf_source'][:40]}")
    stress = json.loads(fc.input_path(F45_STRESS).read_text(encoding="utf-8"))
    e2e = stress["rtf_end_to_end"]
    ax_f.errorbar([0.86], [e2e["median"]], yerr=[[e2e["median"] - e2e["min"]],
                                                 [e2e["max"] - e2e["median"]]],
                  fmt="_", color=fc.OI["black"], ms=5, lw=0.9, capsize=1.5, zorder=6)
    for k in ("min", "median", "max"):
        out.add("f", "F5 base point, stress runs (end-to-end)", "MEAS", "L", 1, k,
                float(e2e[k]), note=f"n={stress['runs_requested']}, "
                                    f"{stress['concurrency']} concurrent workers")
    ax_f.axhline(1.0, color=fc.OI["black"], lw=0.7, ls=":")
    _log2_links(ax_f)
    ax_f.set_xlim(0.7, 160)
    ax_f.set_yscale("log")
    ax_f.set_ylim(0.004, 120)
    ax_f.set_ylabel("RTF")
    hf = ax_f.get_legend_handles_labels()[0] + [
        Line2D([], [], color=fc.OI["grey"], ls="-", lw=1.0, label="F4 (in process)"),
        Line2D([], [], color=fc.OI["grey"], ls="--", lw=1.0, label="F5 (ZMQ, end to end)")]
    ax_f.legend(handles=hf, loc="upper right", fontsize=fc.FONT_PT, handlelength=1.6,
                labelspacing=0.12, borderaxespad=0.2)
    fc.panel_label(ax_f, "(f)", y=0.12)

    out.add("a", "RTF = 1 reference", "MEAS", "", "", "rtf", 1.0)
    out.add("a", "max L with RTF >= 1 (F1, 1 MS/s, every N; summary)", "MEAS", "", "",
            "links", float(min(summ["max_L_rtf_ge_1"].values())),
            note=json.dumps(summ["max_L_rtf_ge_1"]))
    meta = json.loads(fc.input_path(META).read_text(encoding="utf-8"))
    f45m = json.loads(fc.input_path(F45_META).read_text(encoding="utf-8"))
    inputs = [
        {"path": POINTS, "data_class": "MEAS", "run_id": meta["run"]["run_id"],
         "host": meta["host"]["instance_type"], "sremu_commit": meta["sremu"]["commit"]},
        {"path": ISO, "data_class": "MEAS"},
        {"path": SUMMARY, "data_class": "MEAS"},
        {"path": META, "data_class": "MEAS"},
        {"path": F45_POINTS, "data_class": "MEAS", "run_id": f45m["run"]["run_id"],
         "host": f45m["host"]["instance_type"], "sremu_commit": f45m["sremu"]["commit"]},
        {"path": F45_STRESS, "data_class": "MEAS"},
        {"path": F45_META, "data_class": "MEAS"},
    ]
    assumptions = [
        "SIM benchmark scenes (LOS and RIS paths, one carrier at 3.5 GHz, non-zero RIS path "
        "asserted per scene); the workload is not a physics result",
        "11 points ran concurrently (16 vCPUs each); isolation reference points rerun alone "
        "were 0.99-1.16x faster (open markers)",
        "aggregate throughput = n_rx * f_s * RTF (one rate group per scene)",
        "(a)-(e): B2 run at 309c633; (f): B2-F45 rerun at bf08268 on another instance of the "
        "same type, F5 end-to-end RTF against F4 of the same run (not compared with (a)-(e))",
        "(e): thread counts above 16 ran in tier-4 slots of as many vCPUs, the others in "
        "16-vCPU slots; all points packed",
        "points below the pilot floor (9 heavy F3 points) and failed points are not drawn",
    ]
    fc.write_outputs(FID, fig, out, args=args, inputs=inputs, assumptions=assumptions,
                     extra={"failed_point_ids": summ["failed_point_ids"],
                            "isolation_ratio": summ["isolation_ratio"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
