"""F8 -- paced mode and the served control loop (B3 and B4 on AWS c7i.8xlarge; MEAS).

Data: the B3 server-plan run and the B4 served paced run (plan served-1ms) copied in
paper/sweeps/results/bench/B3 and B4 (runs 20261012T025455Z_b3_server and
20261012T025715Z_b4_paced, sremu bf08268 clean, two instances of one type; provenance in each
meta.json). Protocol: 5 s warm-up, 60 s measured, 3 repetitions (a B3 series stops at its first
unsustainable repetition).

(a) Deadline-miss rate vs slack, i.e. the complementary distribution (CCDF) of the page
    latency, pooled over the repetitions of a point, for headless B3 points (solid: the
    largest Sustainable point of three series and the two links with 1024 us pages that B4
    serves; dashed: the unsustainable points that ended the 512 us and mobility series), with
    the 1 ms slack and the 1e-3 threshold of the Sustainable rule. The served B4 runs (two
    links, 1024 us pages) appear as their reported percentiles (p50, p95, p99, p99.9 at CCDF
    0.5, 0.05, 0.01, 0.001), at 10 and 100 commands/s. A page is computed from its release on,
    so zero slack is missed by every page. No stage breakdown was recorded.
(b) Largest Sustainable link count per B3 configuration (dots; "none": not even one link) and
    the link count whose repetition failed (crosses), labelled with the number of repetitions
    run there. Page durations are those that ran (page samples / sample rate).
(c) Served timed commands: fraction applied late (after their switch sample had been
    computed; applied on arrival) vs lead, at 10 and 100 commands/s (lines: pooled over the
    repetitions; markers: repetitions; a zero is drawn at the axis floor). Every other timed
    command was applied on its exact sample (exact = 1 - late, checked). Shaded: the range of
    the asap visible-latency p99 over the repetitions.
(d) Apply delay (wall time from the nominal release of the page that holds the applied switch
    to the emission of the command record) vs requested command rate, median and p99 per
    repetition, asap and timed with an 8 ms lead (the two modes run at every rate). Repetitions
    that are backlogged (page latency p50 above one page or more than 1 % of pages later than
    5 ms) are grey crosses; shaded: the engine limits the command rate.
"""

from __future__ import annotations

import csv
import json
import math
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

# Labels name the page duration that ran (page samples / sample rate, read from points.csv)
# in microseconds, the unit of Section VI, not the page target of the series key.
# (a) CCDF series: (series key, L, label prefix, colour, line style, line width)
CCDF = (
    ("fs1e6_page1e-3_N240_R1_F1", 8, "", fc.OI["green"], "-", 1.0),
    ("fs1e6_page5e-4_N240_R1_F1", 4, "", fc.OI["blue"], "-", 1.0),
    ("fs23.04e6_page1e-3_N240_R1_F1", 1, "", fc.OI["orange"], "-", 1.0),
    ("fs1e6_page1e-3_N240_R1_F1", 2, "as served (B4 scene), ", fc.OI["black"], "-", 0.8),
    ("fs1e6_page5e-4_N240_R1_F1", 8, "", fc.OI["purple"], "--", 1.0),
    ("fs1e6_page5e-4_N240_R1_F3", 1, "mobility (F3), ", fc.OI["vermillion"], "--", 1.0),
)
# (b) configurations in plan order: (series key, label prefix; None: the sample rate)
CONFIGS = (
    ("fs1e6_page5e-4_N240_R1_F1", None),
    ("fs1e6_page1e-3_N240_R1_F1", None),
    ("fs1e6_page2.5e-4_N240_R1_F1", None),
    ("fs5.76e6_page5e-4_N240_R1_F1", None),
    ("fs23.04e6_page1e-3_N240_R1_F1", None),
    ("fs1e6_page5e-4_N4096_R1_F1", "$N$ = 4096"),
    ("fs1e6_page5e-4_N240_R4_F1", "four surfaces"),
    ("fs1e6_page5e-4_N240_R1_F3", "mobility (F3)"),
    ("fs1e5_page1e-3_N240_R1_F1", None),
)
RATE_UNIT = "commands/s"  # command-rate unit of the caption and of Section VI
PCT_CCDF = (("p50", 0.5), ("p95", 0.05), ("p99", 0.01), ("p999", 0.001))
# (c): a late fraction of zero is drawn here (log axis); it lies below every non-zero value
# (checked), so a small non-zero fraction is never drawn as zero
LATE_FLOOR = 1e-5


def series_page(pts: list[dict], key: str) -> tuple[str, str]:
    """(sample-rate text, page-duration text in us) of a B3 series, from its points."""
    found = {(r["rate_sps"], r["page"]) for r in pts if r["series"] == key}
    if len(found) != 1:
        raise SystemExit(f"F8: series {key} has {len(found)} (rate, page) pairs")
    rate, page = found.pop()
    fs = float(rate)
    return f"{fs / 1e6:g} MS/s", f"{int(page) / fs * 1e6:.0f} $\\mu$s"


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
    import matplotlib.ticker as mticker
    from matplotlib.lines import Line2D

    pts = list(csv.DictReader(fc.input_path(POINTS).open(encoding="utf-8")))
    hists = json.loads(fc.input_path(HIST).read_text(encoding="utf-8"))
    s3 = json.loads(fc.input_path(SUMMARY3).read_text(encoding="utf-8"))
    paced = json.loads(fc.input_path(PACED).read_text(encoding="utf-8"))
    s4 = json.loads(fc.input_path(SUMMARY4).read_text(encoding="utf-8"))
    rows = fc.Rows()
    page_us = {round(r["page_s"] * 1e6) for r in paced}
    if len(page_us) != 1:
        raise SystemExit("F8: B4 repetitions with different pages")
    page_us_v = page_us.pop()
    links4 = json.loads(fc.input_path(f"{B4}/paced/spec.json").read_text(encoding="utf-8"))[
        "links"]

    fig = plt.figure(figsize=(fc.DOUBLE_IN, 4.5))
    gs = fig.add_gridspec(2, 3, width_ratios=[1.0, 1.0, 1.0], height_ratios=[1.0, 1.0],
                          hspace=0.45, wspace=0.55)
    ax_a = fig.add_subplot(gs[0, 0:2])
    ax_b, ax_c, ax_d = (fig.add_subplot(gs[1, k]) for k in range(3))

    # (a) miss rate vs slack = page-latency CCDF
    for key, L, prefix, col, ls, lw in CCDF:
        rate_txt, page_txt = series_page(pts, key)
        lab = f"{prefix}{rate_txt}, {page_txt} pages, $L$ = {L}"
        ids = [r["point_id"] for r in pts if r["series"] == key and int(r["L"]) == L]
        if not ids:
            raise SystemExit(f"F8: no B3 point {key} L={L}")
        xs, ys, n = _hist_ccdf([hists[i] for i in ids])
        ax_a.step(xs, ys, where="post", color=col, ls=ls, lw=lw, label=lab)
        for x, y in zip(xs, ys, strict=True):
            rows.add("a", f"page-latency CCDF {key} L={L}", "MEAS", "slack_ms", x,
                     "miss_rate", y, note=f"pooled over {len(ids)} repetitions, {n} pages")
    for rate, mk, col in ((10, "x", fc.OI["black"]), (100, "+", fc.OI["grey"])):
        srv = [r for r in paced if r["rate_hz"] == rate]
        for k, r in enumerate(srv):
            xs = [r["page_latency_us"][p] / 1e3 for p, _ in PCT_CCDF]
            ys = [q for _, q in PCT_CCDF]
            ax_a.plot(xs, ys, ls="", marker=mk, ms=3.5, mew=0.8, color=col,
                      label=f"served, {links4} links, {page_us_v} $\\mu$s, {rate} {RATE_UNIT}"
                      if k == 0 else None)
            for (p, q), x in zip(PCT_CCDF, xs, strict=True):
                rows.add("a", f"served B4 page latency percentile ({rate} Hz)", "MEAS",
                         "slack_ms", x, "miss_rate", q,
                         note=f"{r['key']} rep {r['rep']} {p}")
    ax_a.axvline(1.0, color=fc.OI["grey"], lw=0.8, ls=":")
    ax_a.axhline(1e-3, color=fc.OI["grey"], lw=0.8, ls=":")
    ax_a.set_xscale("log")
    ax_a.set_yscale("log")
    ax_a.set_xlim(0.03, 300)
    ax_a.set_ylim(5e-6, 1.6)
    ax_a.set_xlabel("slack (ms)")
    ax_a.set_ylabel("deadline-miss rate")
    ax_a.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), fontsize=fc.FONT_PT,
                handlelength=1.8, labelspacing=0.25, borderaxespad=0.0)
    fc.panel_label(ax_a, "(a)", x=0.94)

    # (b) largest Sustainable L per configuration (dot plot, log2 axis; "none" at 0.5) and the
    # failing link count with the number of repetitions run there
    ls_ = s3["largest_sustainable"]
    yy = list(range(len(CONFIGS)))
    for y, (key, _lab) in zip(yy, CONFIGS, strict=True):
        best = ls_[key]
        L = best["L"] if best else 0.5
        lmax = max(int(r["L"]) for r in pts if r["series"] == key)
        end = [r for r in pts if r["series"] == key and int(r["L"]) == lmax]
        if all(r["sustainable"] == "True" for r in end):
            raise SystemExit(f"F8: series {key} did not end on a failing repetition")
        ax_b.plot([0.5, lmax], [y, y], color=fc.OI["grey"], lw=0.6)
        if best:
            ax_b.plot([L], [y], "o", color=fc.OI["blue"], ms=4, mew=1.0)
        ax_b.plot([lmax], [y], "x", color=fc.OI["vermillion"], ms=4, mew=1.0)
        ax_b.text(lmax * 1.25, y, str(len(end)), va="center", ha="left",
                  color=fc.OI["vermillion"])
        rows.add("b", "largest Sustainable L", "MEAS", "configuration", key, "links",
                 float(best["L"]) if best else 0.0,
                 note="none: L = 1 not Sustainable" if best is None else
                 f"worst p99 {best['worst_p99_latency_us']} us; worst miss rate at 1 ms "
                 f"{best['worst_miss_rate_1ms']}; 3 repetitions")
        rows.add("b", "failing L", "MEAS", "configuration", key, "links", float(lmax),
                 note=f"{len(end)} repetition(s) run, "
                      f"{sum(r['sustainable'] == 'False' for r in end)} unsustainable")
    ax_b.set_yticks(yy)
    b_labels = []
    for key, prefix in CONFIGS:
        rate_txt, page_txt = series_page(pts, key)
        b_labels.append(f"{prefix or rate_txt}, {page_txt}")
    ax_b.set_yticklabels(b_labels)
    ax_b.set_ylim(len(CONFIGS) - 0.5, -0.5)
    ax_b.set_xscale("log", base=2)
    ax_b.set_xticks([0.5, 1, 2, 4, 8, 16])
    ax_b.set_xticklabels(["none", "1", "2", "4", "8", "16"])
    ax_b.set_xlim(0.4, 34)
    ax_b.set_xlabel("links $L$")
    ax_b.grid(axis="y", visible=False)
    fc.panel_label(ax_b, "(b)", x=0.80)

    # (c) late fraction of timed commands vs lead at 10 and 100 commands/s
    live = [r for r in paced if not r["backlogged"]]
    timed = [r for r in live if r["mode"] == "timed"]
    leads = sorted({round(r["lead_s"] * 1e3, 6) for r in timed})
    for rate, col, mk in ((10, fc.OI["blue"], "o"), (100, fc.OI["orange"], "s")):
        med = []
        for ld in leads:
            reps = [r for r in timed if r["rate_hz"] == rate and round(r["lead_s"] * 1e3, 6) == ld]
            n = sum(r["commands"] for r in reps)
            pooled = sum(r["late_fraction"] * r["commands"] for r in reps) / n
            if any(0 < x <= 2 * LATE_FLOOR for x in [pooled] + [r["late_fraction"]
                                                             for r in reps]):
                raise SystemExit("F8: a non-zero late fraction lies at the zero floor of (c)")
            med.append(pooled if pooled > 0 else LATE_FLOOR)
            yv = [r["late_fraction"] if r["late_fraction"] > 0 else LATE_FLOOR for r in reps]
            ax_c.plot([ld] * len(yv), yv, ls="", marker=mk, ms=2.5, color=col, alpha=0.6,
                      mfc="none", mew=0.7)
            for r in reps:
                rows.add("c", f"late fraction, timed, {rate} Hz", "MEAS", "lead_ms", ld,
                         "late_fraction", r["late_fraction"],
                         note=f"rep {r['rep']}; exact {r['exact_fraction']}; "
                              f"{r['commands']} commands")
            rows.add("c", f"late fraction pooled, timed, {rate} Hz", "MEAS", "lead_ms", ld,
                     "late_fraction", pooled, note="pooled over repetitions; zero drawn at "
                                                   f"{LATE_FLOOR:g}")
        ax_c.plot(leads, med, color=col, lw=1.0, marker=mk, ms=3,
                  label=f"{rate}")  # legend title: the unit
    asap99 = [r["visible_latency_us"]["p99"] / 1e3 for r in live if r["mode"] == "asap"]
    ax_c.axvspan(min(asap99), max(asap99), color=fc.OI["grey"], alpha=0.2, lw=0)
    ax_c.text((min(asap99) * max(asap99)) ** 0.5, 0.18, "asap\np99", ha="center", va="top",
              linespacing=1.0)
    for v in (min(asap99), max(asap99)):
        rows.add("c", "asap visible latency p99 (range over repetitions)", "MEAS", "lead_ms", v,
                 "", "", note="10 and 100 commands/s")
    ax_c.set_xscale("log", base=2)
    ax_c.set_xticks(leads)
    ax_c.set_xticklabels([f"{x:g}" for x in leads])
    ax_c.set_xlim(leads[0] / 1.25, leads[-1] * 1.25)
    ax_c.set_yscale("log")
    ax_c.set_ylim(LATE_FLOOR / 1.6, 0.3)
    e0 = round(math.log10(LATE_FLOOR))
    c_ticks = [10.0 ** (e0 + k) for k in range(-e0)]  # floor (labelled 0), decades up to 0.1
    ax_c.set_yticks(c_ticks)
    ax_c.set_yticklabels(["0"] + [f"$10^{{{e0 + k}}}$" for k in range(1, -e0)])
    ax_c.yaxis.set_minor_locator(mticker.NullLocator())
    ax_c.set_xlabel("lead of timed commands (ms)")
    ax_c.set_ylabel("fraction applied late")
    ax_c.legend(loc="lower left", bbox_to_anchor=(0.0, 0.07), title=RATE_UNIT,
                fontsize=fc.FONT_PT, title_fontsize=fc.FONT_PT, handlelength=1.6,
                labelspacing=0.12, borderaxespad=0.2)
    fc.panel_label(ax_c, "(c)", x=0.86)

    # (d) apply delay vs requested command rate (asap; timed with the lead run at every rate)
    rates = sorted({r["rate_hz"] for r in paced})
    lead_all = max(ld for ld in leads if all(any(r["rate_hz"] == f and r["mode"] == "timed"
                                                   and round(r["lead_s"] * 1e3, 6) == ld
                                                   for r in paced) for f in rates))
    styles = (("asap", None, fc.OI["blue"], "o"), ("timed", lead_all, fc.OI["orange"], "s"))
    for mode, ld, col, mk in styles:
        for p, ls in (("p50", "-"), ("p99", "--")):
            mx, my = [], []
            for f in rates:
                reps = [r for r in paced if r["rate_hz"] == f and r["mode"] == mode and (
                    ld is None or round(r["lead_s"] * 1e3, 6) == ld)]
                good = [r["apply_delay_us"][p] / 1e3 for r in reps if not r["backlogged"]]
                bad = [r["apply_delay_us"][p] / 1e3 for r in reps if r["backlogged"]]
                if good:
                    mx.append(f)
                    my.append(statistics.median(good))
                    ax_d.plot([f] * len(good), good, ls="", marker=mk, ms=2.5, color=col,
                              alpha=0.6, mfc="none", mew=0.7)
                if bad:
                    ax_d.plot([f] * len(bad), bad, ls="", marker="x", ms=3.5, mew=0.8,
                              color=fc.OI["grey"])
                for r in reps:
                    rows.add("d", f"apply delay {p}, {mode}"
                             + (f" lead {ld:g} ms" if ld else ""), "MEAS", "rate_hz", f,
                             "apply_delay_ms", r["apply_delay_us"][p] / 1e3,
                             note=f"rep {r['rep']}"
                             + ("; backlogged: the engine lags, delay = backlog"
                                if r["backlogged"] else ""))
            ax_d.plot(mx, my, color=col, ls=ls, lw=1.0, marker=mk, ms=3)
    bound = sorted({r["rate_hz"] for r in paced if r["backlogged"]})
    if bound and any(r["rate_hz"] >= bound[0] and not r["backlogged"] for r in paced):
        raise SystemExit("F8: backlog is not monotone in the command rate")
    if bound and any(r["rate_hz"] < bound[0] and r["backlogged"] for r in paced):
        raise SystemExit("F8: a repetition below the engine-bound rate is backlogged")
    shade_lo = bound[0] / 10 ** 0.3 if bound else None
    if shade_lo is not None:
        ax_d.axvspan(shade_lo, 3000, color=fc.OI["grey"], alpha=0.15, lw=0)
        ax_d.text((shade_lo * 3000) ** 0.5, 12, "engine-\nbound", ha="center", va="center",
                  linespacing=1.0)
    ax_d.set_xscale("log")
    ax_d.set_yscale("log")
    ax_d.set_xlim(6, 3000)
    ax_d.set_ylim(0.5, 2e4)
    ax_d.set_xlabel(f"requested command rate ({RATE_UNIT})")
    ax_d.set_ylabel("apply delay (ms)")
    d_handles = [Line2D([], [], color=fc.OI["blue"], marker="o", ms=3, lw=1.0, label="asap"),
                 Line2D([], [], color=fc.OI["orange"], marker="s", ms=3, lw=1.0,
                        label=f"timed {lead_all:g} ms"),
                 Line2D([], [], color=fc.OI["black"], ls="-", lw=1.0, label="median"),
                 Line2D([], [], color=fc.OI["black"], ls="--", lw=1.0, label="p99"),
                 Line2D([], [], ls="", marker="x", ms=3.5, mew=0.8, color=fc.OI["grey"],
                        label="backlogged")]
    ax_d.legend(handles=d_handles, loc="upper left", bbox_to_anchor=(0.0, 1.0),
                fontsize=fc.FONT_PT, handlelength=1.6, labelspacing=0.12, borderaxespad=0.2)
    fc.panel_label(ax_d, "(d)", x=0.86, y=0.10)

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
        {"path": f"{B4}/paced/spec.json", "data_class": "MEAS"},
        {"path": SUMMARY4, "data_class": "MEAS"},
        {"path": META4, "data_class": "MEAS"},
    ]
    assumptions = [
        "B3 headless paced runs: in-process TX generators, RX kept in the engine, no "
        "transport, no client process, no control socket; B4: engine served over the control "
        "socket, one external command process, TX generated and RX kept in the engine",
        "page latency = delivery - release; miss rate at slack s = P(latency > s); zero slack "
        "is missed by construction",
        "CCDF pooled over the repetitions of a point (log-linear histograms, 1 us "
        "resolution); B4 served runs shown by their reported percentiles",
        "timed commands: at = client's estimate of virtual time + lead (paced.json lead_s)",
        "backlogged (bench/b4_control.py): page latency p50 > one page or miss rate at 5 ms "
        "> 1 %; such repetitions are excluded from late and exact fractions",
        f"protocol: {json.dumps(s3['protocol_12_3'])}",
    ]
    fc.write_outputs(FID, fig, rows, args=args, inputs=inputs, assumptions=assumptions,
                     extra={"b4_paced_summary": {k: {kk: v[kk] for kk in (
                         "sustainable_reps", "backlogged_reps", "late_fraction",
                         "exact_fraction", "achieved_rate_hz_median", "engine_bound_reps")}
                         for k, v in s4["paced"].items()}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
