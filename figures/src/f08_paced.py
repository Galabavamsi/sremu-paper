"""F8 -- paced mode and control loop (B3 and B4 on AWS c7i.8xlarge; MEAS).

Data: the B3 server-plan run and the B4 paced run copied in paper/sweeps/results/bench/B3 and
B4 (runs 20261011T074437Z_b3_server and 20261011T074639Z_b4_paced, sremu 309c633 clean;
provenance in each meta.json). Protocol: 5 s warm-up, 60 s measured, 3 repetitions.

(a) Deadline-miss rate vs slack, i.e. the complementary distribution (CCDF) of the page
    latency, pooled over the repetitions of a point, for headless B3 points (the largest
    Sustainable point of three series, and the first unsustainable point of the mobility and
    0.1 MS/s series), with the 1 ms slack and the 1e-3 threshold of the Sustainable rule. The
    served B4 runs (two links, 10 commands/s) appear as their reported percentiles (p50, p95,
    p99, p99.9 at CCDF 0.5, 0.05, 0.01, 0.001). A page is computed from its release on, so
    zero slack is missed by every page. No stage breakdown was recorded.
(b) Largest Sustainable link count per B3 configuration ("none": not even one link).
(c) Visible command latency (receipt at the emulator to delivery of the page holding the
    switch) vs requested command rate: median of the per-repetition p50 and the worst p99,
    timed (2 ms lead) and asap. At >= 1 kHz the engine is the bottleneck (shaded).
(d) Fraction of timed commands applied late and fraction of pages missing the 1 ms deadline vs
    command rate (median over repetitions; markers: repetitions).
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import figcommon as fc

FID = "F8"
B3 = "sweeps/results/bench/B3"
B4 = "sweeps/results/bench/B4"
POINTS = f"{B3}/run/points.csv"
HIST = f"{B3}/run/latency_hist.json"
SUMMARY3 = f"{B3}/run/summary.json"
META3 = f"{B3}/meta.json"
PACED = f"{B4}/paced/paced.json"
SUMMARY4 = f"{B4}/paced/summary.json"
META4 = f"{B4}/meta.json"

# (a) CCDF series: (series key, L, label, colour, line style)
CCDF = (
    ("fs1e6_page5e-4_N240_R1_F1", 1, "1 MS/s, 0.51 ms pages, $L$=1", fc.OI["blue"], "-"),
    ("fs1e6_page1e-3_N240_R1_F1", 8, "1 MS/s, 1.02 ms pages, $L$=8", fc.OI["green"], "-"),
    ("fs23.04e6_page1e-3_N240_R1_F1", 1, "23.04 MS/s, 0.71 ms pages, $L$=1", fc.OI["orange"],
     "-"),
    ("fs1e6_page5e-4_N240_R1_F3", 1, "mobility (F3), 0.51 ms, $L$=1", fc.OI["vermillion"],
     "--"),
    ("fs1e5_page1e-3_N240_R1_F1", 1, "0.1 MS/s, 1.28 ms pages, $L$=1", fc.OI["purple"], "--"),
)
# (b) configurations in plan order: (series key, label)
CONFIGS = (
    ("fs1e6_page5e-4_N240_R1_F1", "1 MS/s, 0.5 ms"),
    ("fs1e6_page1e-3_N240_R1_F1", "1 MS/s, 1 ms"),
    ("fs1e6_page2.5e-4_N240_R1_F1", "1 MS/s, 0.25 ms"),
    ("fs5.76e6_page5e-4_N240_R1_F1", "5.76 MS/s, 0.5 ms"),
    ("fs23.04e6_page1e-3_N240_R1_F1", "23.04 MS/s, 1 ms"),
    ("fs1e6_page5e-4_N4096_R1_F1", "$N$ = 4096, 0.5 ms"),
    ("fs1e6_page5e-4_N240_R4_F1", "four surfaces, 0.5 ms"),
    ("fs1e6_page5e-4_N240_R1_F3", "mobility (F3), 0.5 ms"),
    ("fs1e5_page1e-3_N240_R1_F1", "0.1 MS/s, 1 ms"),
)
PCT_CCDF = (("p50", 0.5), ("p95", 0.05), ("p99", 0.01), ("p999", 0.001))


def _hist_ccdf(hists: list[dict]) -> tuple[list[float], list[float], int]:
    """Pooled CCDF P(latency >= x) at the bucket edges (x in ms) of log-linear histograms."""
    from collections import Counter

    from sremu.metrics.hist import LogLinearHistogram  # the run's histogram layout

    res = {h["resolution"] for h in hists}
    sub = {h["sub_buckets"] for h in hists}
    if len(res) != 1 or len(sub) != 1:
        raise SystemExit("F8: histograms of one point differ in layout")
    hh = LogLinearHistogram(res.pop(), sub.pop())
    counts: Counter[int] = Counter()
    for h in hists:
        for b, c in h["buckets"].items():
            counts[int(b)] += int(c)
    n = sum(counts.values())
    edges = sorted(counts)
    xs, ys = [], []
    above = n
    for b in edges:
        lo, _hi = hh.bucket_bounds(b)
        xs.append(lo / 1e6)
        ys.append(above / n)
        above -= counts[b]
    xs.append(max(h["max"] for h in hists) / 1e6)
    ys.append(1 / n)
    return xs, ys, n


def main(argv: list[str] | None = None) -> int:
    args = fc.parse_args(FID, argv)
    fc.setup_style()
    import matplotlib.pyplot as plt

    pts = list(csv.DictReader(fc.input_path(POINTS).open(encoding="utf-8")))
    hists = json.loads(fc.input_path(HIST).read_text(encoding="utf-8"))
    s3 = json.loads(fc.input_path(SUMMARY3).read_text(encoding="utf-8"))
    paced = json.loads(fc.input_path(PACED).read_text(encoding="utf-8"))
    s4 = json.loads(fc.input_path(SUMMARY4).read_text(encoding="utf-8"))
    rows = fc.Rows()

    fig = plt.figure(figsize=(fc.DOUBLE_IN, 4.6))
    gs = fig.add_gridspec(2, 3, width_ratios=[1.0, 1.0, 1.0], height_ratios=[1.0, 1.0],
                          hspace=0.42, wspace=0.55)
    ax_a = fig.add_subplot(gs[0, 0:2])
    ax_b, ax_c, ax_d = (fig.add_subplot(gs[1, k]) for k in range(3))

    # (a) miss rate vs slack = page-latency CCDF
    for key, L, lab, col, ls in CCDF:
        ids = [r["point_id"] for r in pts if r["series"] == key and int(r["L"]) == L]
        if not ids:
            raise SystemExit(f"F8: no B3 point {key} L={L}")
        xs, ys, n = _hist_ccdf([hists[i] for i in ids])
        ax_a.step(xs, ys, where="post", color=col, ls=ls, lw=1.0, label=lab)
        for x, y in zip(xs, ys, strict=True):
            rows.add("a", f"page-latency CCDF {key} L={L}", "MEAS", "slack_ms", x,
                     "miss_rate", y, note=f"pooled over {len(ids)} repetitions, {n} pages")
    srv = [r for r in paced if r["rate_hz"] == 10]
    for k, r in enumerate(srv):
        xs = [r["page_latency_us"][p] / 1e3 for p, _ in PCT_CCDF]
        ys = [q for _, q in PCT_CCDF]
        ax_a.plot(xs, ys, ls="", marker="x", ms=3.5, mew=0.8, color=fc.OI["black"],
                  label="served, 2 links, 10 cmd/s" if k == 0 else None)
        for (p, q), x in zip(PCT_CCDF, xs, strict=True):
            rows.add("a", "served B4 page latency percentile (10 Hz)", "MEAS", "slack_ms", x,
                     "miss_rate", q, note=f"{r['mode']} rep {r['rep']} {p}")
    ax_a.axvline(1.0, color=fc.OI["grey"], lw=0.8, ls=":")
    ax_a.axhline(1e-3, color=fc.OI["grey"], lw=0.8, ls=":")
    ax_a.set_xscale("log")
    ax_a.set_yscale("log")
    ax_a.set_xlim(0.03, 40)
    ax_a.set_ylim(5e-6, 1.6)
    ax_a.set_xlabel("slack (ms)")
    ax_a.set_ylabel("deadline-miss rate")
    ax_a.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), fontsize=fc.FONT_PT,
                handlelength=1.8, labelspacing=0.25, borderaxespad=0.0)
    fc.panel_label(ax_a, "(a)", x=0.94)

    # (b) largest Sustainable L per configuration (dot plot, log2 axis; "none" at 0.5)
    ls_ = s3["largest_sustainable"]
    yy = list(range(len(CONFIGS)))
    for y, (key, _lab) in zip(yy, CONFIGS, strict=True):
        best = ls_[key]
        L = best["L"] if best else 0.5
        ax_b.plot([0.5, L], [y, y], color=fc.OI["grey"], lw=0.6)
        ax_b.plot([L], [y], "o" if best else "x", color=fc.OI["blue"] if best
                  else fc.OI["vermillion"], ms=4, mew=1.0)
        rows.add("b", "largest Sustainable L", "MEAS", "configuration", key, "links",
                 float(best["L"]) if best else 0.0,
                 note="none: L = 1 not Sustainable" if best is None else
                 f"worst p99 {best['worst_p99_latency_us']} us; worst miss rate at 1 ms "
                 f"{best['worst_miss_rate_1ms']}")
    ax_b.set_yticks(yy)
    ax_b.set_yticklabels([lab for _, lab in CONFIGS])
    ax_b.set_ylim(len(CONFIGS) - 0.5, -0.5)
    ax_b.set_xscale("log", base=2)
    ax_b.set_xticks([0.5, 1, 2, 4, 8])
    ax_b.set_xticklabels(["none", "1", "2", "4", "8"])
    ax_b.set_xlim(0.4, 11)
    ax_b.set_xlabel("largest Sustainable links $L$")
    ax_b.grid(axis="y", visible=False)
    fc.panel_label(ax_b, "(b)", x=0.80)

    # (c) visible command latency vs command rate
    rates = sorted({r["rate_hz"] for r in paced})
    styles = (("asap", "p50", fc.OI["blue"], "-", "o"), ("asap", "p99", fc.OI["blue"], "--", "o"),
              ("timed", "p50", fc.OI["orange"], "-", "s"),
              ("timed", "p99", fc.OI["orange"], "--", "s"))
    for mode, p, col, ls, mk in styles:
        ys = []
        for f in rates:
            vals = [r["visible_latency_us"][p] / 1e3 for r in paced
                    if r["rate_hz"] == f and r["mode"] == mode]
            y = statistics.median(vals) if p == "p50" else max(vals)
            ys.append(y)
            rows.add("c", f"visible latency {mode} {p}", "MEAS", "rate_hz", f,
                     "latency_ms", y, note="median of 3 reps" if p == "p50" else
                     "worst of 3 reps")
        ax_c.plot(rates, ys, color=col, ls=ls, marker=mk, ms=3, lw=1.0,
                  label=f"{mode}, {'median' if p == 'p50' else 'p99'}")
    ax_c.axvspan(700, 14000, color=fc.OI["grey"], alpha=0.15, lw=0)
    ax_c.text(3000, 0.65, "engine-bound", ha="center", va="bottom")
    ax_c.axhline(2.0, color=fc.OI["grey"], lw=0.8, ls=":")
    ax_c.text(500, 1.75, "2 ms lead", color=fc.OI["grey"], va="top", ha="right")
    ax_c.set_xscale("log")
    ax_c.set_yscale("log")
    ax_c.set_xlim(7, 14000)
    ax_c.set_ylim(0.5, 3e4)
    ax_c.set_xlabel("requested command rate (Hz)")
    ax_c.set_ylabel("visible command latency (ms)")
    ax_c.legend(loc="center left", bbox_to_anchor=(0.0, 0.6), fontsize=fc.FONT_PT,
                handlelength=1.8, labelspacing=0.12)
    fc.panel_label(ax_c, "(c)")

    # (d) late timed commands and pages missing the 1 ms deadline
    for name, col, mk, fn, sel in (
            ("timed commands applied late", fc.OI["orange"], "s",
             lambda r: r["status"].get("late_applied", 0) / r["commands"], "timed"),
            ("pages missing 1 ms deadline", fc.OI["black"], "o",
             lambda r: r["miss_rate"]["1ms"], None)):
        med = []
        for f in rates:
            vals = [fn(r) for r in paced if r["rate_hz"] == f and (sel is None
                                                                    or r["mode"] == sel)]
            med.append(statistics.median(vals))
            ax_d.plot([f] * len(vals), vals, ls="", marker=mk, ms=2.5, color=col, alpha=0.6,
                      mfc="none", mew=0.7)
            for v in vals:
                rows.add("d", name, "MEAS", "rate_hz", f, "fraction", v, note="repetition")
        ax_d.plot(rates, med, color=col, lw=1.0, marker=mk, ms=3, label=name)
        if sel == "timed":
            ax_d.text(40, 0.87, "late timed\ncommands", color=col, va="bottom", ha="center")
        else:
            ax_d.text(3200, 0.9, "pages missing\n1 ms deadline", color=col, va="top",
                      ha="center")
    ax_d.axvspan(700, 14000, color=fc.OI["grey"], alpha=0.15, lw=0)
    ax_d.set_xscale("log")
    ax_d.set_xlim(7, 14000)
    ax_d.set_ylim(-0.04, 1.15)
    ax_d.set_xlabel("requested command rate (Hz)")
    ax_d.set_ylabel("fraction")
    fc.panel_label(ax_d, "(d)", y=0.12, x=0.84)

    m3 = json.loads(fc.input_path(META3).read_text(encoding="utf-8"))
    m4 = json.loads(fc.input_path(META4).read_text(encoding="utf-8"))
    inputs = [
        {"path": POINTS, "data_class": "MEAS", "run_id": m3["run"]["run_id"],
         "host": m3["host"]["instance_type"], "sremu_commit": m3["sremu"]["commit"]},
        {"path": HIST, "data_class": "MEAS"},
        {"path": SUMMARY3, "data_class": "MEAS"},
        {"path": META3, "data_class": "MEAS"},
        {"path": PACED, "data_class": "MEAS", "run_id": m4["runs"]["paced"]["run_id"],
         "host": m4["host"]["instance_type"], "sremu_commit": m4["sremu"]["commit"]},
        {"path": SUMMARY4, "data_class": "MEAS"},
        {"path": META4, "data_class": "MEAS"},
    ]
    assumptions = [
        "B3 headless paced runs: in-process TX generators, RX kept in the engine, no "
        "transport; B4: engine served over the control socket, one external command process",
        "page latency = delivery - release; miss rate at slack s = P(latency > s); zero slack "
        "is missed by construction",
        "CCDF pooled over the repetitions of a point (log-linear histograms, 1 us "
        "resolution); B4 served runs shown by their reported percentiles only",
        "timed commands: at = client's estimate of virtual time + 2 ms lead",
        f"protocol: {json.dumps(s3['protocol_12_3'])}",
    ]
    fc.write_outputs(FID, fig, rows, args=args, inputs=inputs, assumptions=assumptions,
                     extra={"b4_paced_summary": {k: {kk: v[kk] for kk in (
                         "sustainable_reps", "achieved_rate_hz_median", "engine_bound_reps")}
                         for k, v in s4["paced"].items()}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
