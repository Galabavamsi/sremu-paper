"""bench_results.py -- Section VI values and Table VII from the committed benchmark results
(WP12; DESIGN 12, 13.3). Called by tools/gen_results.py; run alone it prints the values.

Reads ONLY ``paper/sweeps/results/bench/{B1,B2,B3,B4,B6}`` (byte copies of the AWS and laptop
run directories, see each ``meta.json``), never typed numbers. Data class MEAS throughout
(B6 literature rows: REPORTED, not used here).

Checks that the text's statements still hold (SystemExit otherwise), e.g. that every failed B2
point is an F5 (native ZMQ) point, that the static link limit is the same for every element
count, that the B4 lockstep lateness is zero, and that the B3 headline is the largest
Sustainable configuration.

Two corrections are applied to the raw summaries, both documented in
``research/analysis/bench_findings.md``:

* B2 aggregate throughput is recomputed as ``n_rx * f_s * RTF`` (exact for the benchmark
  scenes, whose receivers share one rate group): the engine's ``aggregate_sps`` after
  ``reset_metrics()`` also counts the samples produced before the restart (sremu
  ``metrics/rt.py``), so ``points.csv`` overstates it by about 2x.
* The B4 lockstep check is read from ``summary_recomputed.json`` (bench.b4_control
  --recompute): a command whose switch lies after the end of the run is not a failure.

F5 (native ZMQ) results of the B2 run are never reported: the transport stall is being fixed
and F5 will be rerun; its macros stay placeholders.

Usage: python tools/bench_results.py [--paper-root DIR]
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import re
import statistics
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_common import PAPER_ROOT

BENCH = Path("sweeps") / "results" / "bench"
SLACK_1MS = "1ms"


# --------------------------------------------------------------------------- helpers
def _fmt() -> Any:
    import gen_results as gr  # the shared rounding helpers (half-even on the exact decimal)

    return gr


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def cpu_count(spec: str) -> int:
    """Number of CPUs in a Linux CPU list such as '1-8,17-24'."""
    n = 0
    for part in spec.split(","):
        a, _, b = part.partition("-")
        n += (int(b) - int(a) + 1) if b else 1
    return n


def lscpu_field(text: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}:\s*(.+)$", text, re.M)
    if not m:
        raise SystemExit(f"bench_results: lscpu has no {name!r}")
    return m.group(1).strip()


def mem_gib(meminfo: str) -> int:
    m = re.search(r"MemTotal:\s*(\d+)\s*kB", meminfo)
    if not m:
        raise SystemExit("bench_results: meminfo has no MemTotal")
    return round(int(m.group(1)) / 2**20)


# --------------------------------------------------------------------------- B2
def b2_rows(root: Path) -> list[dict[str, str]]:
    return read_csv(root / "B2" / "run" / "points.csv")


def b2_ok(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Measured points of the main grid (status ok, default threads, never F5)."""
    return [r for r in rows if r["status"] == "ok" and r["threads_explicit"] == "False"
            and r["features"] != "F5"]


def b2_aggregate_msps(r: dict[str, str]) -> float:
    """Aggregate RX throughput of a B2 row, MS/s: n_rx * f_s * RTF (one rate group)."""
    return int(r["n_rx"]) * float(Fraction(r["f_s"])) * float(r["rtf_median"]) / 1e6


def b2_series(rows: list[dict[str, str]], n: str, ris: str, fs: str,
              feat: str) -> dict[int, dict[str, str]]:
    return {int(r["L"]): r for r in rows if (r["N"], r["n_ris"], r["f_s"], r["features"])
            == (n, ris, fs, feat)}


# --------------------------------------------------------------------------- B3
def b3_series_key(fs: str, page: str, n: int, ris: int, feat: str) -> str:
    return f"fs{fs}_page{page}_N{n}_R{ris}_{feat}"


def b3_page_samples(rows: list[dict[str, str]], key: str) -> int:
    pages = {int(r["page"]) for r in rows if r["series"] == key}
    if len(pages) != 1:
        raise SystemExit(f"bench_results: B3 series {key} has page sizes {pages}")
    return pages.pop()


def headline(summary: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The B3 headline: the Sustainable configuration with the most links (ties: the higher
    sample rate)."""
    best = None
    for key, v in summary["largest_sustainable"].items():
        if v is None:
            continue
        rate = float(Fraction(re.match(r"fs([^_]+)_", key).group(1)))
        cand = (int(v["L"]), rate)
        if best is None or cand > best[0]:
            best = (cand, key, v)
    if best is None:
        raise SystemExit("bench_results: B3 has no Sustainable configuration")
    return best[1], best[2]


# --------------------------------------------------------------------------- values
def bench_values(paper_root: Path = PAPER_ROOT) -> tuple[dict[str, str], dict[str, Any],
                                                         dict[str, str]]:
    gr = _fmt()
    dec, integer, sci, pct = gr.dec, gr.integer, gr.sci, gr.pct
    root = paper_root / BENCH
    v: dict[str, str] = {}
    prov: dict[str, Any] = {}

    # ---------------------------------------------------------------- B2 (lockstep)
    b2s = read_json(root / "B2" / "run" / "summary.json")
    rows = b2_rows(root)
    failed = [r for r in rows if r["status"] == "failed"]
    if any(r["features"] != "F5" for r in failed):
        raise SystemExit("bench_results: Section VI says every failed B2 point is an F5 point")
    if len(failed) != b2s["points_failed"]:
        raise SystemExit("bench_results: B2 failed points differ from the summary")
    timeouts = sum(1 for r in failed if "recv error TIMEOUT" in r["error"])
    if (timeouts, len(failed) - timeouts) != (3, 1):  # the text says "three ... and one ..."
        raise SystemExit(f"bench_results: B2 failures are no longer 3 time-outs + 1 error: "
                         f"{[r['error'][:60] for r in failed]}")
    v["numBtwoPlanned"] = str(b2s["points_planned"])
    v["numBtwoRun"] = str(b2s["points_run"])
    v["numBtwoOk"] = str(b2s["points_ok"])
    v["numBtwoBelowFloor"] = str(b2s["points_below_floor"])
    v["numBtwoFailed"] = str(b2s["points_failed"])
    below = [r for r in rows if r["status"] == "below_floor"]
    if {r["features"] for r in below} != {"F3"} or min(int(r["L"]) for r in below) < 64:
        raise SystemExit("bench_results: below-floor B2 points are not all heavy F3 points")
    slots = read_json(root / "B2" / "run" / "slots.json")["slots"]
    v["numBtwoSlots"] = str(len(slots))
    slot_cpus = {cpu_count(s) for s in slots}
    if len(slot_cpus) != 1:
        raise SystemExit("bench_results: B2 slots differ in size")
    v["numBtwoSlotCpus"] = str(slot_cpus.pop())
    ok = b2_ok(rows)
    base_key = ("240", "1", "1e6", "F1")
    base = b2_series(ok, *base_key)
    if abs(float(base[1]["rtf_median"]) - float(b2s["base_point_rtf"])) > 1e-12:
        raise SystemExit("bench_results: B2 base point differs from the summary")
    v["numBtwoBaseN"] = base_key[0]
    v["numBtwoBaseMsps"] = dec(float(Fraction(base_key[2])) / 1e6, 0)
    v["numBtwoBaseRtf"] = dec(float(base[1]["rtf_median"]), 2)
    cells = sorted({int(r["N"]) for r in ok})
    v["numBtwoNmin"], v["numBtwoNmax"] = integer(cells[0]), integer(cells[-1])
    one = {n: float(b2_series(ok, str(n), "1", "1e6", "F1")[1]["rtf_median"]) for n in cells}
    v["numBtwoNspreadPct"] = dec(100 * (max(one.values()) / min(one.values()) - 1), 0,
                                 mode="ROUND_CEILING")
    f3 = {n: float(b2_series(ok, str(n), "1", "1e6", "F3")[1]["rtf_median"]) for n in cells}
    v["numBtwoFthreeRtfSmall"] = dec(f3[cells[0]], 2)
    v["numBtwoFthreeRtfLarge"] = dec(f3[cells[-1]], 2)
    if not f3[cells[0]] > f3[cells[-1]]:
        raise SystemExit("bench_results: F3 RTF does not fall with N")
    # the cost-model paragraph says the snapshot cost grows with N at the larger counts: the
    # snapshot period is the same for N >= 900, yet the single-link F3 RTF still falls
    big = [n for n in cells if n >= 900]
    tu_big = {b2_series(ok, str(n), "1", "1e6", "F3")[1]["t_u_s"] for n in big}
    if len(tu_big) != 1 or any(f3[a] <= f3[b] for a, b in itertools.pairwise(big)):
        raise SystemExit("bench_results: F3 at N >= 900 no longer shows the per-snapshot cost "
                         "growing with N at a fixed snapshot period")
    maxl = {}
    for n in cells:
        ser = b2_series(ok, str(n), "1", "1e6", "F1")
        maxl[n] = max(L for L, r in ser.items() if float(r["rtf_median"]) >= 1.0)
    if len(set(maxl.values())) != 1 or {f"N{n}": L for n, L in maxl.items()} != \
            b2s["max_L_rtf_ge_1"]:
        raise SystemExit(f"bench_results: static link limit differs between N: {maxl}")
    lim = maxl[cells[0]]
    v["numBtwoMaxLinks"] = str(lim)
    # a link is a TX-RX pair (bench/_scenes.py: L = n_tx * n_rx); the text gives L = lim
    if any(int(r["n_tx"]) * int(r["n_rx"]) != int(r["L"]) for r in ok):
        raise SystemExit("bench_results: a B2 scene has L != n_tx * n_rx")
    v["numBtwoLimTx"] = str(int(base[lim]["n_tx"]))  # numerals: the sentence also has "8 links"
    v["numBtwoLimRx"] = str(int(base[lim]["n_rx"]))
    nxt = 2 * lim
    l16 = [float(b2_series(ok, str(n), "1", "1e6", "F1")[nxt]["rtf_median"]) for n in cells]
    v["numBtwoLsixteen"] = str(nxt)
    v["numBtwoLsixteenMin"] = dec(min(l16), 2)
    v["numBtwoLsixteenMax"] = dec(max(l16), 2)
    top = b2_series(ok, "240", "1", "23.04e6", "F1")[1]
    v["numBtwoTopMsps"] = "23.04"
    if Fraction(top["f_s"]) != Fraction("23.04e6"):
        raise SystemExit("bench_results: B2 top rate changed")
    v["numBtwoTopRtf"] = dec(float(top["rtf_median"]), 2)
    if float(top["rtf_median"]) < 1.0:
        raise SystemExit("bench_results: the single 23.04 MS/s link no longer keeps up")
    feats = {f: float(b2_series(ok, "240", "1", "1e6", f)[1]["rtf_median"])
             for f in ("F0", "F1", "F2", "F3", "F4")}
    if not all(feats[a] > feats[b] for a, b in (("F0", "F1"), ("F1", "F2"), ("F2", "F3"),
                                                 ("F3", "F4"))):
        raise SystemExit(f"bench_results: feature-set RTFs are not decreasing: {feats}")
    v["numBtwoRtfFzero"] = dec(feats["F0"], 2)
    v["numBtwoRtfFtwo"] = dec(feats["F2"], 2)
    v["numBtwoRtfFthree"] = dec(feats["F3"], 2)
    v["numBtwoRtfFfour"] = dec(feats["F4"], 2)
    agg = [(b2_aggregate_msps(r), r) for r in ok if r["features"] == "F1" and r["n_ris"] != "0"]
    a_max, r_max = max(agg, key=lambda x: x[0])
    if (r_max["N"], r_max["n_ris"]) != base_key[:2]:  # the text points to Fig. 7(c)
        raise SystemExit("bench_results: the peak aggregate point is not in Fig. 7(c) (N = 240, "
                         "one surface)")
    if float(r_max["rtf_median"]) >= 1.0:  # the text says "an RTF below one"
        raise SystemExit("bench_results: the peak aggregate point now keeps up with the wall "
                         "clock")
    v["numBtwoAggMaxMsps"] = dec(a_max, 0)
    v["numBtwoAggMaxRx"] = str(int(r_max["n_rx"]))
    v["numBtwoAggMaxRate"] = dec(float(Fraction(r_max["f_s"])) / 1e6, 2)
    iso = read_csv(root / "B2" / "run" / "isolation.csv")
    ratios = [float(r["ratio_isolated_over_parallel"]) for r in iso]
    if (abs(min(ratios) - b2s["isolation_ratio"]["min"]) > 1e-12
            or abs(max(ratios) - b2s["isolation_ratio"]["max"]) > 1e-12):
        raise SystemExit("bench_results: isolation ratios differ from the summary")
    v["numBtwoIsoPoints"] = str(len(iso))
    v["numBtwoIsoMin"] = dec(min(ratios), 2)
    v["numBtwoIsoMax"] = dec(max(ratios), 2)
    v["numBtwoIsoMaxPct"] = dec(100 * (max(ratios) - 1), 0)
    b2m = read_json(root / "B2" / "run" / "manifest.json")
    v["txtHostServer"] = b2m["aws_instance"]["type"]
    prov["B2"] = {"results": str(BENCH / "B2"), "run": "20261011T084048Z_b2_server",
                  "aggregate": "recomputed as n_rx * f_s * RTF (points.csv aggregate counts "
                               "pre-restart samples)",
                  "f5": "not reported (pending rerun after the transport fix)"}

    # ---------------------------------------------------------------- B1 (kernels, cost)
    b1 = read_json(root / "B1" / "run" / "summary.json")
    cm = b1["engine_cost_model"]
    ext = b1["engine_cost_model_extended"]
    pts = read_csv(root / "B1" / "run" / "engine_cost_points.csv")
    if len(pts) != cm["n_points"]:
        raise SystemExit("bench_results: B1 cost-model points differ from the summary")
    v["numBonePoints"] = str(cm["n_points"])
    v["numBoneRelRmsPct"] = pct(cm["rel_rms"], 0)
    v["numBoneRelMaxPct"] = pct(cm["rel_max_abs"], 0)
    v["numBoneFixedUs"] = dec(1e6 * cm["a_s"], 0)
    bpt = [r for r in pts if (r["L"], r["N"], r["n_ris"], r["f_s"], r["features"])
           == ("1", "240", "1", "1e6", "F1")]
    if len(bpt) != 1:
        raise SystemExit("bench_results: B1 base engine point missing")
    t_base = float(bpt[0]["T_page_s"])
    v["numBoneBasePageUs"] = dec(1e6 * t_base, 0)
    v["numBoneFixedSharePct"] = pct(cm["a_s"] / t_base, 0)
    f3p = [r for r in pts if r["features"] == "F3"]
    worst = max(f3p, key=lambda r: abs(float(r["rel_err"])))
    if abs(float(worst["rel_err"])) != max(abs(float(r["rel_err"])) for r in pts) or \
            float(worst["rel_err"]) >= 0:
        raise SystemExit("bench_results: the largest cost-model error is not an F3 underestimate")
    v["numBoneSnapMs"] = dec(ext["g_us_per_snapshot"] / 1e3, 2)
    v["numBoneSnapLinkMs"] = dec(ext["h_us_per_link_snapshot"] / 1e3, 2)
    v["numBoneExtRelRmsPct"] = pct(ext["rel_rms"], 0)
    v["numBoneExtCoeffs"] = str(ext["n_coefficients"])
    fits = read_json(root / "B1" / "run" / "fits.json")
    v["numBoneSnapExpNumpy"] = dec(b1["snapshot_exponent_alpha"]["numpy_ref"], 2)
    v["numBoneSnapExpNumba"] = dec(b1["snapshot_exponent_alpha"]["numba_cpu"], 2)
    rq = fits["snapshot"]["numpy_ref"]["RQ_range"]
    v["numBoneRqMin"] = integer(int(rq[0]))
    v["numBoneRqMax"] = sci(float(rq[1]), 2)
    v["numBoneRqMaxNumba"] = sci(float(fits["snapshot"]["numba_cpu"]["RQ_range"][1]), 2)
    l16 = [r for r in pts if (r["L"], r["N"], r["n_ris"], r["f_s"], r["features"])
           == (str(nxt), "240", "1", "1e6", "F1")]
    if len(l16) != 1:
        raise SystemExit("bench_results: B1 has no L = 2 x limit point at the base")
    v["numBoneLsixteenRtf"] = dec(float(l16[0]["rtf_median"]), 2)
    prov["B1"] = {"results": str(BENCH / "B1"), "run": "20261011T083749Z_b1_slot16",
                  "cost_model": cm["model"]}

    # ---------------------------------------------------------------- B3 (paced)
    b3 = read_json(root / "B3" / "run" / "summary.json")
    b3p = read_csv(root / "B3" / "run" / "points.csv")
    b3m = read_json(root / "B3" / "run" / "manifest.json")
    b3meta = read_json(root / "B3" / "run" / "meta.json")
    proto = b3["protocol_12_3"]
    if not proto["meets_12_3"] or b3["points_failed"]:
        raise SystemExit("bench_results: B3 did not meet the section 12.3 protocol")
    v["numBthreeMeasureS"] = dec(proto["measure_s"], 0)
    v["numBthreeReps"] = str(proto["reps"])
    v["numBthreeMinPages"] = integer(proto["min_pages_per_rep"])
    v["numPacedEngineCpus"] = str(cpu_count(b3meta["pinning"]["affinity"]))
    plat = b3m["aws_instance"]["type"]
    if not b3m["platform"].startswith("Linux"):
        raise SystemExit("bench_results: paced numbers must come from Linux")
    v["txtPacedPlatform"] = f"an AWS {plat} (Linux)"
    v["txtHostPaced"] = plat
    hkey, hval = headline(b3)
    hrate = Fraction(re.match(r"fs([^_]+)_", hkey).group(1))
    v["numPacedLinks"] = str(hval["L"])
    v["numPacedMSps"] = dec(float(hrate) / 1e6, 0 if hrate % 10**6 == 0 else 2)
    ls = b3["largest_sustainable"]

    def series(fs: str, page: str, n: int = 240, ris: int = 1, feat: str = "F1") -> str:
        return b3_series_key(fs, page, n, ris, feat)

    names = {  # macro stem -> series
        "Half": series("1e6", "5e-4"), "One": series("1e6", "1e-3"),
        "Quarter": series("1e6", "2.5e-4"), "Mid": series("5.76e6", "5e-4"),
        "Top": series("23.04e6", "1e-3"), "BigN": series("1e6", "5e-4", n=4096),
        "FourRis": series("1e6", "5e-4", ris=4),
    }
    if hkey != names["One"]:
        raise SystemExit("bench_results: the B3 headline is no longer the 1 ms page series")
    for stem, key in names.items():
        s = ls[key]
        if s is None:
            raise SystemExit(f"bench_results: B3 series {key} has no Sustainable point")
        if stem in ("Half", "Top", "BigN"):
            if s["L"] != 1:  # the text says "one link" / "a single link" for these
                raise SystemExit(f"bench_results: B3 {key} now sustains {s['L']} links")
            continue
        v[f"numBthree{stem}Links"] = str(s["L"])
    if not ls[names["FourRis"]]["L"] > ls[names["Half"]]["L"]:  # the text says "more than"
        raise SystemExit("bench_results: four surfaces no longer sustain more links than one "
                         "at 0.5 ms pages")
    for stem in ("Half", "One", "Quarter", "Top", "Mid"):
        key = names[stem]
        rate = Fraction(re.match(r"fs([^_]+)_", key).group(1))
        v[f"numBthree{stem}PageUs"] = dec(1e6 * b3_page_samples(b3p, key) / float(rate), 0)
    v["numBthreeOnePninetynineUs"] = dec(ls[names["One"]]["worst_p99_latency_us"], 0)
    v["numBthreeOneMiss"] = sci(ls[names["One"]]["worst_miss_rate_1ms"], 2)
    v["numBthreeHalfPninetynineUs"] = dec(ls[names["Half"]]["worst_p99_latency_us"], 0)
    v["numBthreeTopPninetynineUs"] = dec(ls[names["Top"]]["worst_p99_latency_us"], 0)
    v["numBthreeTopMiss"] = sci(ls[names["Top"]]["worst_miss_rate_1ms"], 2)
    v["numBthreeTopMsps"] = dec(float(Fraction("23.04e6")) / 1e6, 2)
    v["numBthreeMidMsps"] = dec(float(Fraction("5.76e6")) / 1e6, 2)
    v["numBthreeBigN"] = integer(4096)
    none = sorted(k for k, s in ls.items() if s is None)
    k_f3, k_low = series("1e6", "5e-4", feat="F3"), series("1e5", "1e-3")
    if none != sorted([k_f3, k_low]):
        raise SystemExit(f"bench_results: B3 series without a Sustainable point changed: {none}")
    # short pages: the two-link point at 0.5 ms (rare multi-millisecond stalls)
    two = [r for r in b3p if r["series"] == names["Half"] and r["L"] == "2"]
    miss2 = [float(r["miss_rate_1ms"]) for r in two]
    passed = [m for m in miss2 if m < 1e-3]
    if len(miss2) != 3 or len(passed) != 2:
        raise SystemExit("bench_results: Section VI says the two-link 0.5 ms point fails in one "
                         "of three repetitions")
    v["numBthreeTwoLinkMissOther"] = sci(max(passed), 2)
    # three digits: two would print the failed repetition's 1.03e-3 as the threshold 1.0e-3
    v["numBthreeTwoLinkMissMax"] = sci(max(miss2), 3)
    v["numBthreeTwoLinkUtil"] = dec(statistics.median(float(r["util_p50"]) for r in two), 2)
    v["numBthreeStallMaxMs"] = dec(max(float(r["lat_max_us"]) for r in two) / 1e3, 0)
    # mobility (F3): snapshot pages
    f3r = [r for r in b3p if r["series"] == k_f3]
    if len(f3r) != 1 or f3r[0]["L"] != "1":
        raise SystemExit("bench_results: B3 F3 series changed")
    f3r = f3r[0]
    tu = {float(r["t_u_s"]) for r in rows if r["point_id"] == "L1_N240_R1_fs1e6_F3"}
    if len(tu) != 1:
        raise SystemExit("bench_results: T_u of the F3 base point not unique")
    tu_s = tu.pop()
    page_s = int(f3r["page"]) / 1e6
    v["numBthreeFthreeMissPct"] = pct(float(f3r["miss_rate_1ms"]), 0)
    v["numBthreeFthreeTuMs"] = dec(1e3 * tu_s, 2)
    v["numBthreeFthreeSnapPct"] = pct(page_s / tu_s, 0)
    v["numBthreeFthreeUtilMs"] = dec(float(f3r["util_p99"]) * page_s * 1e3, 1)
    if abs(float(f3r["miss_rate_1ms"]) - page_s / tu_s) > 0.05:
        raise SystemExit("bench_results: F3 miss rate no longer matches the snapshot-page share")
    # 0.1 MS/s, 1 ms pages: one repetition, marginal failure
    low = [r for r in b3p if r["series"] == k_low]
    if len(low) != 1:
        raise SystemExit("bench_results: B3 low-rate series changed")
    low = low[0]
    v["numBthreeLowMsps"] = dec(0.1, 1)
    v["numBthreeLowPage"] = low["page"]
    v["numBthreeLowPageMs"] = dec(int(low["page"]) / 1e5 * 1e3, 2)
    v["numBthreeLowUtilPct"] = pct(float(low["util_p50"]), 0)
    v["numBthreeLowMiss"] = sci(float(low["miss_rate_1ms"]), 2)
    v["numBthreeLowMissPages"] = str(round(float(low["miss_rate_1ms"]) * int(low["pages"])))
    v["numBthreeLowMaxMs"] = dec(float(low["lat_max_us"]) / 1e3, 1)
    if float(low["miss_rate_5ms"]) != 0.0:
        raise SystemExit("bench_results: the low-rate point now misses at 5 ms slack")
    prov["B3"] = {"results": str(BENCH / "B3"), "run": "20261011T074437Z_b3_server",
                  "headline": hkey, "commit": b3m["git_commit"], "dirty": b3m["git_dirty"]}

    # ---------------------------------------------------------------- B4 (control)
    lk = read_json(root / "B4" / "lockstep" / "summary_recomputed.json")
    lrows = read_json(root / "B4" / "lockstep" / "lockstep.json")
    if not (lk["lockstep_check_ok"] and lk["lockstep_all_lateness_zero"]):
        raise SystemExit("bench_results: B4 lockstep lateness is not zero")
    v["numBfourLockCommands"] = integer(lk["lockstep_commands_total"])
    rates = sorted({int(r["rate_hz"]) for r in lrows})
    v["numBfourRateMinHz"] = str(rates[0])
    v["numBfourRateMaxKHz"] = dec(rates[-1] / 1e3, 0)
    rtf = {(r["variant"], int(r["rate_hz"])): float(r["rtf"]) for r in lrows}
    cost = [(1 / rtf[("inproc", f)] - 1 / rtf[("none", f)]) / f for f in rates if f >= 100]
    v["numBfourCmdCostMinMs"] = dec(1e3 * min(cost), 2)
    v["numBfourCmdCostMaxMs"] = dec(1e3 * max(cost), 2)
    v["numBfourRtfNone"] = dec(rtf[("none", rates[-1])], 2)
    v["numBfourRtfInproc"] = dec(rtf[("inproc", rates[-1])], 3)
    v["numBfourRtfRemote"] = dec(rtf[("remote", rates[-1])], 3)
    sched = lk["lockstep_scheduled_at_end"]
    if set(sched) != {f"inproc@{rates[-1]}Hz", f"remote@{rates[-1]}Hz"} or any(
            s["scheduled"] != 1 for s in sched.values()):
        raise SystemExit("bench_results: Section VI says one command per 10 kHz run stays "
                         "scheduled")
    pr = read_json(root / "B4" / "paced" / "paced.json")
    ps = read_json(root / "B4" / "paced" / "summary.json")
    pspec = read_json(root / "B4" / "paced" / "spec.json")
    if any(r["sustainable"] for r in pr) or not ps["protocol_12_3"]["meets_12_3"]:
        raise SystemExit("bench_results: Section VI says no B4 paced repetition is Sustainable")
    v["numBfourLinks"] = str(pspec["links"])
    page_ms = {round(1e3 * r["measure_s"] / r["pages"], 6) for r in pr}
    if len(page_ms) != 1:
        raise SystemExit("bench_results: B4 paced page sizes differ")
    v["numBfourPageMs"] = dec(page_ms.pop(), 3)

    def sel(rate: int, mode: str | None = None) -> list[dict[str, Any]]:
        return [r for r in pr if r["rate_hz"] == rate and (mode is None or r["mode"] == mode)]

    lo_r, mid_r = 10, 100
    v["numBfourRateLowHz"], v["numBfourRateMidHz"] = str(lo_r), str(mid_r)
    v["numBfourRateHighKHz"] = dec(1000 / 1e3, 0)
    for stem, rate in (("Low", lo_r), ("Mid", mid_r)):
        m = [r["miss_rate"][SLACK_1MS] for r in sel(rate)]
        v[f"numBfourMiss{stem}MinPct"] = pct(min(m), 1)
        v[f"numBfourMiss{stem}MaxPct"] = pct(max(m), 1)
        late = sum(r["status"].get("late_applied", 0) for r in sel(rate, "timed"))
        n = sum(r["commands"] for r in sel(rate, "timed"))
        v[f"numBfourLate{stem}"] = integer(late)
        v[f"numBfourCmds{stem}"] = integer(n)
    if any(r["miss_rate"][SLACK_1MS] != 1.0 for r in pr if r["rate_hz"] >= 1000):
        raise SystemExit("bench_results: Section VI says every page misses at >= 1 kHz")
    fr = [r["status"].get("late_applied", 0) / r["commands"] for r in sel(lo_r, "timed")]
    v["numBfourLateRepMinPct"] = pct(min(fr), 0)
    v["numBfourLateRepMaxPct"] = pct(max(fr), 0)
    asap = sel(lo_r, "asap") + sel(mid_r, "asap")
    v["numBfourVisPfiftyMinMs"] = dec(min(r["visible_latency_us"]["p50"] for r in asap) / 1e3, 1)
    v["numBfourVisPfiftyMaxMs"] = dec(max(r["visible_latency_us"]["p50"] for r in asap) / 1e3, 1)
    v["numBfourVisPninetynineMinMs"] = dec(
        min(r["visible_latency_us"]["p99"] for r in asap) / 1e3, 1)
    v["numBfourVisPninetynineMaxMs"] = dec(
        max(r["visible_latency_us"]["p99"] for r in asap) / 1e3, 1)
    v["numBfourVisMinMs"] = dec(min(r["visible_latency_us"]["min"] for r in asap) / 1e3, 1)
    v["numBfourVisMaxMs"] = dec(max(r["visible_latency_us"]["max"] for r in asap) / 1e3, 0)
    if any(r["lateness_max"] for r in asap):
        raise SystemExit("bench_results: an asap command was late")
    hi = [k for k in ps["paced"] if int(k.split("@")[1][:-2]) >= 1000]
    ach = [ps["paced"][k]["achieved_rate_hz_median"] for k in hi]
    v["numBfourAchievedMin"] = dec(min(ach), 0)
    v["numBfourAchievedMax"] = dec(max(ach), 0)
    if not all(ps["paced"][k]["engine_bound_reps"] == ps["paced"][k]["reps"] for k in hi):
        raise SystemExit("bench_results: the >= 1 kHz cases are not all engine-bound")
    # wall/virtual of the whole repetition: the engine catches up once the client stops, so
    # this is far smaller than the lag during the run, which the page latency shows
    v["numBfourLagPct"] = pct(max(ps["paced"][k]["wall_over_virtual_max"] for k in hi) - 1, 0)
    v["numBfourPageLatMaxS"] = dec(max(r["page_latency_us"]["max"] for r in pr
                                       if r["rate_hz"] >= 1000) / 1e6, 0)
    v["numBfourTimedVisMaxS"] = dec(max(ps["paced"][k]["visible_latency_p99_us_worst"]
                                        for k in hi if k.startswith("timed")) / 1e6, 1)
    two_hl = [float(r["miss_rate_1ms"]) for r in b3p if r["series"] == names["Half"]
              and r["L"] == str(pspec["links"])]
    if max(two_hl) * 10 > min(r["miss_rate"][SLACK_1MS] for r in sel(lo_r)):
        raise SystemExit("bench_results: the served runs no longer miss far more often than the "
                         "headless ones")
    prov["B4"] = {"results": str(BENCH / "B4"),
                  "runs": ["20261011T074442Z_b4_lockstep", "20261011T074639Z_b4_paced"],
                  "lockstep_check": "summary_recomputed.json (bench.b4_control --recompute)"}

    # ---------------------------------------------------------------- B6 (cross-tool)
    ct = read_csv(root / "B6" / "run" / "context_table.csv")
    b6s = read_json(root / "B6" / "run" / "summary.json")
    mraw = read_csv(root / "B6" / "run" / "matlab_raw.csv")
    rel = {r["matlab_release"] for r in mraw}
    if len(rel) != 1:
        raise SystemExit("bench_results: B6 MATLAB release not unique")
    v["txtBsixMatlab"] = "R" + rel.pop()
    ns = sorted({int(r["N"]) for r in ct if r["N"]})
    v["numBsixNmin"], v["numBsixNmax"] = integer(ns[0]), integer(ns[-1])
    v["numBsixAgreeDb"] = sci(b6s["matlab_same_model_max_abs_h_diff_db"], 2)

    def times(method: str) -> list[float]:
        return [float(r["t_per_eval_us"]) for r in ct if r["method"] == method]

    for stem, method in (("Full", "sremu_numba_cpu_snapshot+combine"),
                         ("Comb", "sremu_numba_cpu_combine_only"),
                         ("Vec", "matlab_hand_vec"), ("Loop", "matlab_hand_loop"),
                         ("Risr", "matlab_risreflector_step")):
        t = times(method)
        if len(t) != len(ns):
            raise SystemExit(f"bench_results: B6 {method} rows missing")
        v[f"numBsix{stem}MinUs"] = integer(round(min(t)))
        v[f"numBsix{stem}MaxUs"] = integer(round(max(t)))
    same = {r["method"]: r["same_model_as_sremu"] for r in ct if r["method"]}
    if same.get("matlab_risreflector_step") != "False":
        raise SystemExit("bench_results: Section VI says RISReflector is a different model")
    busy = read_json(root / "B6" / "run" / "sections.json")["sremu"]["background_busy"]["mean"]
    v["numBsixBusyPct"] = pct(busy, 0)
    b6m = read_json(root / "B6" / "run" / "manifest.json")
    prov["B6"] = {"results": str(BENCH / "B6"), "run": "20261011T031205Z_b6_laptop",
                  "commit": b6m["git_commit"], "dirty": b6m["git_dirty"],
                  "dirty_patch_sha256": b6m["dirty_patch_sha256"]}

    tables = {"table7_platforms.tex": render_table7(root)}
    return v, prov, tables


# --------------------------------------------------------------------------- Table VII
def render_table7(root: Path) -> str:
    """Platforms and software versions of the benchmark runs (from the copied host files and
    run manifests)."""
    def aws(b: str, run_sub: str) -> dict[str, str]:
        sysinfo = read_json(root / b / "host" / "sysinfo.json")
        man = read_json(root / b / run_sub / "manifest.json")
        meta = read_json(root / b / run_sub / "meta.json")
        lscpu = (root / b / "host" / "lscpu.txt").read_text(encoding="utf-8")
        mem = mem_gib((root / b / "host" / "meminfo.txt").read_text(encoding="utf-8"))
        sockets = int(lscpu_field(lscpu, "Socket(s)"))
        cores = int(lscpu_field(lscpu, "Core(s) per socket")) * sockets
        rel = sysinfo["uname"]["version"]
        m = re.search(r"~(\d+\.\d+)\.\d+-Ubuntu", rel)
        osname = f"Ubuntu {m.group(1)}" if m else "Linux"
        kernel = ".".join(sysinfo["uname"]["release"].split(".")[:2])  # 7.0.0-1014-aws -> 7.0
        ver = man["versions"]
        return {
            "host": f"AWS {man['aws_instance']['type']}",
            "cpu": lscpu_field(lscpu, "Model name").replace("(R)", "").replace("Intel ", ""),
            "cores": f"{cores}/{lscpu_field(lscpu, 'CPU(s)')}",
            "mem": f"{mem}",
            "os": f"{osname}, kernel {kernel}",
            "py": ver["python"], "np": ver["numpy"], "nb": ver["numba"], "zmq": ver["pyzmq"],
            "matlab": "---",
            "pin": meta["pinning"]["affinity"],
        }

    srv = aws("B2", "run")
    pac = aws("B3", "run")
    slots = read_json(root / "B2" / "run" / "slots.json")["slots"]
    b1_cpus = cpu_count(read_json(root / "B1" / "run" / "meta.json")["pinning"]["affinity"])
    srv["pin"] = (f"B1 on {b1_cpus} vCPUs with the rest of the host idle, B2 as "
                  f"{len(slots)} concurrent points of {cpu_count(slots[0])} vCPUs")
    if read_json(root / "B4" / "paced" / "meta.json")["pinning"]["affinity"] != pac["pin"]:
        raise SystemExit("bench_results: B3 and B4 engine pinning differ")
    clients = read_json(root / "B3" / "host" / "sysinfo.json")["cpu_plan"]["clients"]
    pac["pin"] = (f"engine on {cpu_count(pac['pin'])} vCPUs, B4 clients on "
                  f"{cpu_count(clients)}, the rest idle")
    man6 = read_json(root / "B6" / "run" / "manifest.json")
    meta6 = read_json(root / "B6" / "run" / "meta.json")
    rel6 = read_csv(root / "B6" / "run" / "matlab_raw.csv")[0]["matlab_release"]
    m6 = re.match(r"Windows-(\d+)-\d+\.\d+\.(\d+)", man6["platform"])  # Windows-11-10.0.26200
    ver6 = man6["versions"]
    lap = {
        "host": "Laptop",
        "cpu": man6["cpu_model"].split(" w/")[0].replace("AMD ", ""),
        # SMT 2: the manifest records logical CPUs only (8 cores / 16 threads for this part)
        "cores": f"{man6['logical_cpus'] // 2}/{man6['logical_cpus']}",
        "mem": "n/r",
        "os": (f"Windows {m6.group(1)}, {meta6['pinning']['power_plan_or_governor']}"
               if m6 else man6["platform"]),
        "py": ver6["python"], "np": ver6["numpy"], "nb": ver6["numba"], "zmq": ver6["pyzmq"],
        "matlab": f"R{rel6}",
    }
    rows = [("B1, B2", srv), ("B3, B4", pac), ("B6", lap)]
    commit = read_json(root / "B3" / "run" / "manifest.json")["git_commit"][:7]
    lines = [
        "% GENERATED by paper/tools/bench_results.py (via gen_results.py) from",
        "% paper/sweeps/results/bench/*/host and the run manifests. Do not edit.",
        r"\begin{table*}[t]",
        r"\caption{Platforms and software of the benchmark runs (MEAS). The AWS runs used "
        r"emulator commit \texttt{" + commit + r"}; B6 ran on the laptop from a working tree "
        r"with uncommitted changes (patch hash in the run's provenance). Server: "
        + srv["pin"] + "; paced host: " + pac["pin"] + r". n/r: not recorded by the run. "
        r"B3 and B4 ran on two instances of the same type; no number is compared across "
        r"instance types or with the laptop.}",
        r"\label{tab:platforms}",
        r"\centering",
        r"\footnotesize",
        r"\setlength{\tabcolsep}{2.5pt}",
        r"\begin{tabular}{@{}lllcclccccc@{}}",
        r"\toprule",
        r"Runs & Host & CPU & Cores/threads & RAM (GiB) & OS & Python & NumPy & Numba "
        r"& pyzmq & MATLAB \\",
        r"\midrule",
    ]
    for name, h in rows:
        lines.append(" & ".join([name, h["host"], h["cpu"], h["cores"], h["mem"], h["os"],
                                 h["py"], h["np"], h["nb"], h["zmq"], h["matlab"]]) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table*}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--paper-root", type=Path, default=PAPER_ROOT)
    args = ap.parse_args(argv)
    v, prov, tables = bench_values(args.paper_root)
    for k in sorted(v):
        print(f"{k} = {v[k]}")
    print(json.dumps(prov, indent=1))
    for name, t in tables.items():
        print(f"--- {name}\n{t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
