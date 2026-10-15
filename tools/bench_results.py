"""bench_results.py -- Section VI values and Table VII from the committed benchmark results
(WP12; DESIGN 12, 13.3). Called by tools/gen_results.py; run alone it prints the values.

Reads ONLY ``paper/sweeps/results/bench/{B1,B2,B2-F45,B3,B4,B4-classic,B6}`` (byte copies of
the AWS and laptop run directories, see each ``meta.json``) and ``sweeps/results/E-F/latency``
(the data-plane latency of the regeneration job), never typed numbers. Data class MEAS
throughout (B6 literature rows: REPORTED, not used here).

Runs and commits (each meta.json): B1 and B2 (lockstep scaling, F0-F4, surface-count and
thread scans) at 309c633; B2-F45 (F4 and F5 rerun, F5 stress), B3 (headless paced), B4
(lockstep commands; served paced commands, plan served-1ms) and B4-classic (served paced, the
original 0.5 ms plan) at bf08268; B6 (laptop) at 06cdf7a. No number is compared across
instance types, with the laptop, or across commits; ratios (F5/F4) are taken within one run.

Checks that the text's statements still hold (SystemExit otherwise), e.g. that every failed
B2 point is an F5 point, that the static link limit is the same for every element count, that
the B4 lockstep lateness is zero, that no served paced repetition is Sustainable, that no
repetition at 10 and 100 commands/s is backlogged and every one at 1000 commands/s is, and
that the B3 headline is the largest Sustainable configuration.

One correction is applied to the raw summaries (``research/analysis/bench_findings.md`` h):
B2 aggregate throughput is recomputed as ``n_rx * f_s * RTF`` (exact for the benchmark scenes,
whose receivers share one rate group): the engine's ``aggregate_sps`` after
``reset_metrics()`` also counts the samples produced before the restart (sremu
``metrics/rt.py``), so ``points.csv`` overstates it by about 2x. The F5 rows of the 309c633 B2
run (failed, a benchmark error) are never reported; F5 comes from B2-F45, whose RTF is the
end-to-end figure (virtual time delivered to the RX client process over the wall time from
the served engine's warm-up boundary to the client's last sample; bench/_engine_point.py).

Usage: python tools/bench_results.py [--paper-root DIR]
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import re
import sys
from decimal import ROUND_CEILING, ROUND_FLOOR
from fractions import Fraction
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_common import PAPER_ROOT

BENCH = Path("sweeps") / "results" / "bench"
EF_LAT = Path("sweeps") / "results" / "E-F" / "latency"
SLACK_1MS = "1ms"
SLACK_5MS = "5ms"


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


def short(commit: str) -> str:
    return str(commit)[:7]


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


def b2_threads(rows: list[dict[str, str]], links: int) -> dict[int, float]:
    """RTF over the numba thread count at ``links`` links (N = 240, one surface, 1 MS/s, F1):
    the explicit-thread tier plus the default point (16 threads, its slot size)."""
    out = {int(r["threads"]): float(r["rtf_median"]) for r in rows
           if r["status"] == "ok" and r["threads_explicit"] == "True" and int(r["L"]) == links
           and (r["N"], r["n_ris"], r["f_s"], r["features"]) == ("240", "1", "1e6", "F1")}
    base = b2_series(b2_ok(rows), "240", "1", "1e6", "F1")[links]
    out[int(base["threads"])] = float(base["rtf_median"])
    return out


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


def b3_end_point(rows: list[dict[str, str]], key: str) -> list[dict[str, str]]:
    """Repetitions of the point that ended a B3 series (its largest L)."""
    lmax = max(int(r["L"]) for r in rows if r["series"] == key)
    return [r for r in rows if r["series"] == key and int(r["L"]) == lmax]


# --------------------------------------------------------------------------- B4 (paced)
def served_reps(pr: list[dict[str, Any]], rate: int | None = None,
                mode: str | None = None) -> list[dict[str, Any]]:
    return [r for r in pr if (rate is None or r["rate_hz"] == rate)
            and (mode is None or r["mode"] == mode)]


def lead_ms(r: dict[str, Any]) -> float:
    return round(1e3 * float(r["lead_s"]), 6)


# --------------------------------------------------------------------------- values
def bench_values(paper_root: Path = PAPER_ROOT) -> tuple[dict[str, str], dict[str, Any],
                                                         dict[str, str]]:
    gr = _fmt()
    dec, integer, sci, pct = gr.dec, gr.integer, gr.sci, gr.pct
    root = paper_root / BENCH
    v: dict[str, str] = {}
    prov: dict[str, Any] = {}

    # ---------------------------------------------------------------- B2 (lockstep, 309c633)
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
    v["numBtwoPruned"] = str(int(b2s["points_planned"]) - int(b2s["points_run"]))
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
    # surface count (must-fix 13): one link at 1 MS/s, N = 240, static (F1) and mobile (F3),
    # and at 23.04 MS/s static, for R = 0, 1, 2, 4 surfaces
    def ris_rtf(fs: str, feat: str) -> dict[int, float]:
        return {int(r["n_ris"]): float(r["rtf_median"]) for r in ok
                if (r["N"], r["f_s"], r["features"], r["L"]) == ("240", fs, feat, "1")}

    r_st, r_mo, r_top = ris_rtf("1e6", "F1"), ris_rtf("1e6", "F3"), ris_rtf("23.04e6", "F1")
    for name, ser in (("static", r_st), ("mobile", r_mo), ("top", r_top)):
        if sorted(ser) != [0, 1, 2, 4]:
            raise SystemExit(f"bench_results: B2 surface-count series {name} is not R = 0,1,2,4")
    if not (r_st[0] > r_st[4] and r_mo[0] > r_mo[1] > r_mo[2] > r_mo[4]):
        raise SystemExit("bench_results: Section VI says surfaces lower the one-link RTF, "
                         "strictly with mobility")
    if not (min(r_top[0], r_top[1], r_top[2]) >= 1.0 > r_top[4]):
        raise SystemExit("bench_results: Section VI says one 23.04 MS/s link keeps up with up "
                         "to two surfaces but not with four")
    v["numBtwoRzeroStaticRtf"] = dec(r_st[0], 2)
    v["numBtwoRfourStaticRtf"] = dec(r_st[4], 2)
    v["numBtwoRzeroMobileRtf"] = dec(r_mo[0], 2)
    v["numBtwoRfourMobileRtf"] = dec(r_mo[4], 2)
    v["numBtwoTopRtwoRtf"] = dec(r_top[2], 2)
    v["numBtwoTopRfourRtf"] = dec(r_top[4], 2)
    if abs(r_st[1] - float(base[1]["rtf_median"])) > 1e-12 or abs(
            r_mo[1] - feats["F3"]) > 1e-12 or abs(r_top[1] - float(top["rtf_median"])) > 1e-12:
        raise SystemExit("bench_results: the R = 1 points of the surface scan are not the "
                         "points quoted elsewhere")
    # numba thread count at the static link limit (8 links) and at 64 links
    t8, t64 = b2_threads(rows, lim), b2_threads(rows, 64)
    if min(t8) != 1 or max(t8) < 64 or min(t64) != 1:
        raise SystemExit("bench_results: B2 thread scan no longer spans 1 to >= 64 threads")
    best8 = max(t8, key=t8.get)
    v["numBtwoThrOneRtf"] = dec(t8[1], 2)
    v["numBtwoThrBestRtf"] = dec(t8[best8], 2)
    v["numBtwoThrBest"] = str(best8)
    v["numBtwoThrMax"] = str(max(t8))
    v["numBtwoThrMaxRtf"] = dec(t8[max(t8)], 2)
    v["numBtwoThrGain"] = dec(t8[best8] / t8[1], 1)
    best64 = max(t64, key=t64.get)
    if best64 != best8 or any(t8[t] >= t8[best8] for t in t8 if t > best8) or any(
            t64[t] >= t64[best64] for t in t64 if t > best64):
        raise SystemExit("bench_results: Appendix E says the same thread count is best at 8 and "
                         "64 links and every larger count is slower")
    v["numBtwoThrGainSixtyfour"] = dec(t64[best64] / t64[1], 1)
    v["numBtwoThrLinksBig"] = "64"
    if t8[1] < 1.0 or t8[max(t8)] >= t8[best8]:
        raise SystemExit("bench_results: Section VI says one thread keeps 8 links at RTF >= 1 "
                         "and the most threads are slower than the best count")
    b2m = read_json(root / "B2" / "run" / "manifest.json")
    v["txtHostServer"] = b2m["aws_instance"]["type"]
    prov["B2"] = {"results": str(BENCH / "B2"), "run": "20261011T084048Z_b2_server",
                  "aggregate": "recomputed as n_rx * f_s * RTF (points.csv aggregate counts "
                               "pre-restart samples)",
                  "f5": "not reported (benchmark error); F5 from B2-F45"}

    # ---------------------------------------------------------------- B2-F45 (F4/F5, bf08268)
    f45 = root / "B2-F45"
    fs_rows = read_csv(f45 / "run" / "points.csv")
    fs_sum = read_json(f45 / "run" / "summary.json")
    if fs_sum["points_failed"] or fs_sum["points_ok"] != fs_sum["points_run"] or any(
            r["status"] != "ok" for r in fs_rows):
        raise SystemExit("bench_results: Section VI says every B2-F45 point completed")
    fs_man = read_json(f45 / "run" / "manifest.json")
    if fs_man["aws_instance"]["type"] != v["txtHostServer"]:
        raise SystemExit("bench_results: B2-F45 ran on another instance type than B2")
    if any(r["features"] == "F5" and not r["rtf_source"].startswith("end-to-end")
           for r in fs_rows):
        raise SystemExit("bench_results: an F5 row does not carry the end-to-end RTF")
    by = {(r["L"], r["N"], r["n_ris"], r["f_s"], r["features"]): r for r in fs_rows}
    pairs = [(by[(*k[:4], "F5")], r) for k, r in by.items() if k[4] == "F4"
             and (*k[:4], "F5") in by]
    if len(pairs) != sum(1 for r in fs_rows if r["features"] == "F5"):
        raise SystemExit("bench_results: an F5 point of B2-F45 has no F4 twin")
    ratio = [float(a["rtf_median"]) / float(b["rtf_median"]) for a, b in pairs]
    f5b = by[("1", "240", "1", "1e6", "F5")]
    f4b = by[("1", "240", "1", "1e6", "F4")]
    v["numBtwoFfiveRtf"] = dec(float(f5b["rtf_median"]), 2)
    v["numBtwoFfiveFfourRtf"] = dec(float(f4b["rtf_median"]), 2)
    v["numBtwoFfivePoints"] = str(len(pairs))
    v["numBtwoFfiveRatioMin"] = dec(min(ratio), 2, mode=ROUND_FLOOR)
    v["numBtwoFfiveRatioMax"] = dec(max(ratio), 2, mode=ROUND_CEILING)
    if max(ratio) > 1.0 + 1e-9 or any(float(a["rtf_median"]) >= 1.0 for a, _ in pairs):
        raise SystemExit("bench_results: Section VI says F5 never beats F4 and no F5 point "
                         "keeps up with the wall clock")
    if any(int(r["bad_packets"]) or int(r["seq_errors"]) for r in fs_rows
           if r["features"] == "F5"):
        raise SystemExit("bench_results: an F5 point recorded bad packets or sequence errors")
    stress = {s: read_json(f45 / s / "summary.json") for s in ("stress_separate",
                                                               "stress_pause3")}
    sep, pau = stress["stress_separate"], stress["stress_pause3"]
    for s in (sep, pau):
        if s["runs_ok"] != s["runs_requested"] or s["runs_stalled_or_lost"] or s["runs_failed"] \
                or s["bad_packets_or_seq_errors"]:
            raise SystemExit("bench_results: Section VI says every F5 stress run completed")
    v["numBtwoFfiveStressRuns"] = str(sep["runs_requested"])
    v["numBtwoFfiveStressWorkers"] = str(sep["concurrency"])
    v["numBtwoFfiveStressMed"] = dec(sep["rtf_end_to_end"]["median"], 2)
    v["numBtwoFfiveStressMin"] = dec(sep["rtf_end_to_end"]["min"], 2)
    v["numBtwoFfiveStressMax"] = dec(sep["rtf_end_to_end"]["max"], 2)
    v["numBtwoFfivePauseRuns"] = str(pau["runs_requested"])
    v["numBtwoFfivePauseS"] = dec(pau["settings"]["rx_pause_s"], 0)
    v["numBtwoFfivePauseRtf"] = dec(pau["rtf_end_to_end"]["median"], 2)
    v["numBtwoFfivePauseClientRtf"] = dec(pau["rtf_rx_client"]["median"], 0)
    if not (pau["rtf_end_to_end"]["max"] < 1.0 < pau["rtf_rx_client"]["min"]):
        raise SystemExit("bench_results: the starved-client check no longer separates the "
                         "end-to-end and client-only figures")
    fiso = read_csv(f45 / "run" / "isolation.csv")
    v["numBtwoFfiveIsoRatio"] = dec(max(float(r["ratio_isolated_over_parallel"])
                                        for r in fiso), 2)
    prov["B2-F45"] = {"results": str(BENCH / "B2-F45"), "run": "20261012T032501Z_b2_server",
                      "commit": short(fs_man["git_commit"]),
                      "f5_metric": "end-to-end (bench/_engine_point.py measure_zmq)"}

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

    # ---------------------------------------------------------------- B3 (paced, headless)
    b3 = read_json(root / "B3" / "run" / "summary.json")
    b3p = read_csv(root / "B3" / "run" / "points.csv")
    b3m = read_json(root / "B3" / "run" / "manifest.json")
    b3meta = read_json(root / "B3" / "run" / "meta.json")
    proto = b3["protocol_12_3"]
    if not proto["meets_12_3"] or b3["points_failed"]:
        raise SystemExit("bench_results: B3 did not meet the section 12.3 protocol")
    if b3m["git_dirty"] or any(int(r["late_samples"]) for r in b3p):
        raise SystemExit("bench_results: B3 dirty tree or late input samples (headless runs "
                         "have none)")
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
        "FourRis": series("1e6", "5e-4", ris=4), "Low": series("1e5", "1e-3"),
    }
    if hkey != names["One"]:
        raise SystemExit("bench_results: the B3 headline is no longer the 1 ms page series")
    for stem, key in names.items():
        s = ls[key]
        if s is None:
            raise SystemExit(f"bench_results: B3 series {key} has no Sustainable point")
        if stem in ("Top", "BigN"):
            if s["L"] != 1:  # the text says "a single link" / "one link" for these
                raise SystemExit(f"bench_results: B3 {key} now sustains {s['L']} links")
            continue
        v[f"numBthree{stem}Links"] = str(s["L"])
    if ls[names["FourRis"]]["L"] != ls[names["Half"]]["L"]:  # the text says "as many as"
        raise SystemExit("bench_results: four surfaces no longer sustain as many links as one "
                         "at 0.5 ms pages")
    if ls[names["Low"]]["L"] != ls[names["One"]]["L"]:  # "as many as at 1 MS/s"
        raise SystemExit("bench_results: 0.1 MS/s no longer sustains the headline link count")
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
    low_rate = Fraction("1e5")
    v["numBthreeLowMsps"] = dec(float(low_rate) / 1e6, 1)
    v["numBthreeLowPageMs"] = dec(b3_page_samples(b3p, names["Low"]) / float(low_rate) * 1e3, 2)
    none = sorted(k for k, s in ls.items() if s is None)
    k_f3 = series("1e6", "5e-4", feat="F3")
    if none != [k_f3]:
        raise SystemExit(f"bench_results: B3 series without a Sustainable point changed: {none}")
    # stopping rule: the point that ended each series, and how many repetitions it ran
    ends = {k: b3_end_point(b3p, k) for k in ls}
    for k, reps in ends.items():
        if all(r["sustainable"] == "True" for r in reps):
            raise SystemExit(f"bench_results: B3 series {k} ended on a Sustainable point "
                             "(budget cut?); the text says every series ended on a failure")
    single = [k for k, reps in ends.items() if len(reps) == 1]
    v["numBthreeSingleRepEnds"] = str(len(single))
    v["numBthreeSeries"] = str(len(ls))
    # short pages: at 0.5 ms, the first unsustainable point fails on computation (high median
    # utilization, no multi-ms stall); rare stalls decide the marginal 0.25 ms and N = 4096 ends
    hf = ends[names["Half"]]
    if len(hf) != 1:
        raise SystemExit("bench_results: the 0.5 ms end point ran more than one repetition")
    hf = hf[0]
    v["numBthreeHalfFailLinks"] = hf["L"]
    v["numBthreeHalfFailMissPct"] = pct(float(hf["miss_rate_1ms"]), 1)
    v["numBthreeHalfFailUtil"] = dec(float(hf["util_p50"]), 2)
    if float(hf["util_p50"]) < 0.5 or float(hf["lat_max_us"]) > 5000:
        raise SystemExit("bench_results: the 0.5 ms series no longer ends on computation")
    qf = [r for r in ends[names["Quarter"]] if r["sustainable"] == "False"]
    bf = [r for r in ends[names["BigN"]] if r["sustainable"] == "False"]
    if len(qf) != 1 or len(bf) != 1 or len(ends[names["Quarter"]]) != 3 \
            or len(ends[names["BigN"]]) != 3:
        raise SystemExit("bench_results: Section VI says the 0.25 ms and N = 4096 series ended "
                         "in the third repetition")
    v["numBthreeQuarterFailMiss"] = sci(float(qf[0]["miss_rate_1ms"]), 3)
    v["numBthreeQuarterStallMs"] = dec(float(qf[0]["lat_max_us"]) / 1e3, 0)
    v["numBthreeBigNStallMs"] = dec(float(bf[0]["lat_max_us"]) / 1e3, 0)
    passed_q = [r for r in ends[names["Quarter"]] if r["sustainable"] == "True"]
    if float(qf[0]["lat_max_us"]) < 5000 or float(bf[0]["lat_max_us"]) < 5000 \
            or max(float(r["util_p50"]) for r in passed_q) > float(qf[0]["util_p50"]) + 0.05:
        raise SystemExit("bench_results: the marginal 0.25 ms and N = 4096 ends are no longer "
                         "single multi-millisecond stalls")
    # mobility (F3): one repetition at L = 1; misses on pages that hold a snapshot
    f3r = [r for r in b3p if r["series"] == k_f3]
    if len(f3r) != 1 or f3r[0]["L"] != "1":
        raise SystemExit("bench_results: B3 F3 series changed")
    f3r = f3r[0]
    if {r["series"] for r in b3p if r["features"] == "F3"} != {k_f3}:
        raise SystemExit("bench_results: Section VI says mobility was tried at 0.5 ms pages")
    tu = {float(r["t_u_s"]) for r in rows if r["point_id"] == "L1_N240_R1_fs1e6_F3"}
    if len(tu) != 1:
        raise SystemExit("bench_results: T_u of the F3 base point not unique")
    tu_s = tu.pop()
    page_s = int(f3r["page"]) / 1e6
    v["numBthreeFthreeMissPct"] = pct(float(f3r["miss_rate_1ms"]), 0)
    v["numBthreeFthreeTuMs"] = dec(1e3 * tu_s, 2)
    v["numBthreeFthreeSnapPct"] = pct(page_s / tu_s, 0)
    v["numBthreeFthreeUtilMs"] = dec(float(f3r["util_p99"]) * page_s * 1e3, 1)
    if not (0.5 * page_s / tu_s < float(f3r["miss_rate_1ms"]) <= page_s / tu_s):
        raise SystemExit("bench_results: the F3 miss rate is no longer within the snapshot-page "
                         "share (and above half of it)")
    if float(f3r["util_p99"]) * page_s < 1e-3:
        raise SystemExit("bench_results: the F3 snapshot pages no longer exceed the slack")
    # the served reference: B3 at the B4 configurations (2 links; 1024 and 512 us pages)
    srv_ref = {pg: [float(r["miss_rate_1ms"]) for r in b3p
                    if r["series"] == names[stem] and r["L"] == "2"]
               for pg, stem in ((1024, "One"), (512, "Half"))}
    if len(srv_ref[1024]) != 3 or max(srv_ref[1024]) != 0.0 or len(srv_ref[512]) != 3:
        raise SystemExit("bench_results: Section VI says two links with 1024 us pages missed no "
                         "page headless in three repetitions")
    v["numBthreeClassicRefMiss"] = sci(max(srv_ref[512]), 2)
    if b3_page_samples(b3p, names["One"]) != 1024 or b3_page_samples(b3p, names["Half"]) != 512:
        raise SystemExit("bench_results: B3 page sizes differ from the B4 pages")
    rmw = {r["render_min_work"] for r in b3p} | {r["threading_layer"] for r in b3p}
    prov["B3"] = {"results": str(BENCH / "B3"), "run": "20261012T025455Z_b3_server",
                  "headline": hkey, "commit": short(b3m["git_commit"]),
                  "dirty": b3m["git_dirty"], "render_and_threading": sorted(rmw)}

    # ---------------------------------------------------------------- B4 lockstep
    lk = read_json(root / "B4" / "lockstep" / "summary.json")
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
            s["scheduled"] != 1 or s.get("after_end", 1) != 1 for s in sched.values()):
        raise SystemExit("bench_results: Section VI says one command per 10 kHz run stays "
                         "scheduled, after the end of the run")

    # ---------------------------------------------------------------- B4 served paced
    pr = read_json(root / "B4" / "paced" / "paced.json")
    ps = read_json(root / "B4" / "paced" / "summary.json")
    pspec = read_json(root / "B4" / "paced" / "spec.json")
    pman = read_json(root / "B4" / "paced" / "manifest.json")
    if any(r["sustainable"] for r in pr) or not ps["protocol_12_3"]["meets_12_3"]:
        raise SystemExit("bench_results: Section VI says no B4 paced repetition is Sustainable")
    if ps.get("plan") != "served-1ms" or ps["paced_failed"] or ps["paced_dropped"]:
        raise SystemExit("bench_results: B4 paced is not the complete served-1ms plan")
    # the backlog rule quoted in Appendix E (definition macros numBacklogSlackMs/MissPct)
    from paper_common import load_numbers_spec

    defs = {m.macro: m.value for m in load_numbers_spec(paper_root / "numbers.yaml")}
    rule = (f"miss rate at {defs['numBacklogSlackMs']} ms slack exceeds "
            f"{float(defs['numBacklogMissPct']) / 100:g}")
    if "page latency p50 exceeds one page" not in ps["backlog_rule"] or rule not in \
            ps["backlog_rule"]:
        raise SystemExit("bench_results: the backlog rule of the run differs from Appendix E's")
    if any(r["counters"].get("late_samples", 0) or r["counters"].get("overflow_pages", 0)
           for r in pr):
        raise SystemExit("bench_results: a served repetition had late samples or overflow "
                         "(the text says the deadline rule alone fails)")
    v["numBfourLinks"] = str(pspec["links"])
    pages = {int(r["page_samples"]) for r in pr}
    if len(pages) != 1 or {r["page_s"] for r in pr} != {pages.copy().pop() / 1e6}:
        raise SystemExit("bench_results: B4 paced page sizes differ")
    v["numBfourPageUs"] = dec(1e6 * pr[0]["page_s"], 0)
    lo_r, mid_r, hi_r = 10, 100, 1000
    if sorted({r["rate_hz"] for r in pr}) != [lo_r, mid_r, hi_r]:
        raise SystemExit("bench_results: B4 served rates changed")
    v["numBfourRateLowHz"], v["numBfourRateMidHz"] = str(lo_r), str(mid_r)
    v["numBfourRateHighHz"] = str(hi_r)
    live = [r for r in pr if r["rate_hz"] < hi_r]
    if any(r["backlogged"] for r in live) or not all(r["backlogged"] and r["engine_bound"]
                                                     for r in pr if r["rate_hz"] >= hi_r):
        raise SystemExit("bench_results: Section VI says no repetition at 10 and 100 commands/s "
                         "is backlogged and every one at 1000 commands/s is (engine-bound)")
    if any(r["render_min_work"] != 1 << 18 or (r.get("threading") or {}).get(
            "omp_wait_policy") != "PASSIVE" for r in pr):
        raise SystemExit("bench_results: served repetitions without the served render "
                         "threshold or the passive OpenMP wait policy")
    for stem, rate in (("Low", lo_r), ("Mid", mid_r)):
        m1 = [r["miss_rate"][SLACK_1MS] for r in served_reps(pr, rate)]
        m5 = [r["miss_rate"][SLACK_5MS] for r in served_reps(pr, rate)]
        v[f"numBfourMiss{stem}MinPct"] = pct(min(m1), 1)
        v[f"numBfourMiss{stem}MaxPct"] = pct(max(m1), 1)
        v[f"numBfourMissFive{stem}Max"] = sci(max(m5), 2)
    asap = served_reps(live, mode="asap")
    vis50 = [r["visible_latency_us"]["p50"] for r in asap]
    vis99 = [r["visible_latency_us"]["p99"] for r in asap]
    v["numBfourVisPfiftyMinMs"] = dec(min(vis50) / 1e3, 1)
    v["numBfourVisPfiftyMaxMs"] = dec(max(vis50) / 1e3, 1)
    v["numBfourVisPninetynineMinMs"] = dec(min(vis99) / 1e3, 1)
    v["numBfourVisPninetynineMaxMs"] = dec(max(vis99) / 1e3, 1)
    v["numBfourVisMaxMs"] = dec(max(r["visible_latency_us"]["max"] for r in asap) / 1e3, 0)
    if any(r["lateness_max"] for r in asap) or any(r["exact_fraction"] != 1.0 for r in asap):
        raise SystemExit("bench_results: an asap command was late or off its sample")
    timed = served_reps(live, mode="timed")
    leads = sorted({lead_ms(r) for r in timed})
    if leads[0] != 2.0 or leads[1] != 4.0:
        raise SystemExit(f"bench_results: B4 timed leads changed: {leads}")

    def per_key(lead: float, field: str) -> list[float]:
        """Per-(rate, lead) fraction over the repetitions (commands-weighted)."""
        out = []
        for rate in (lo_r, mid_r):
            reps = [r for r in timed if r["rate_hz"] == rate and lead_ms(r) == lead]
            n = sum(r["commands"] for r in reps)
            out.append(sum(r[field] * r["commands"] for r in reps) / n)
        return out

    late2 = per_key(leads[0], "late_fraction")
    late4 = per_key(leads[1], "late_fraction")
    late6 = [x for ld in leads[2:] for x in per_key(ld, "late_fraction")]
    exact4 = [x for ld in leads[1:] for x in per_key(ld, "exact_fraction")]
    v["numBfourLeadMinMs"] = dec(leads[0], 0)
    v["numBfourLeadOkMs"] = dec(leads[1], 0)
    v["numBfourLeadSixMs"] = dec(leads[2], 0)
    v["numBfourLeadMaxMs"] = dec(leads[-1], 0)
    v["numBfourLateTwoMinPct"] = pct(min(late2), 1)
    v["numBfourLateTwoMaxPct"] = pct(max(late2), 1)
    v["numBfourLateFourMaxPct"] = dec(100 * max(late4), 2, mode=ROUND_CEILING)
    v["numBfourLateSixMaxPct"] = dec(100 * max(late6), 2, mode=ROUND_CEILING)
    v["numBfourExactOkMinPct"] = dec(100 * min(exact4), 1, mode=ROUND_FLOOR)
    if not (min(late2) > 0.01 and max(late4) < 0.005 and max(late6) <= max(late4)):
        raise SystemExit("bench_results: Section VI says a 2 ms lead leaves percent-level late "
                         "commands and 4 ms or more under half a percent")
    # every timed command that is not late is exact (lateness 0 on every RX)
    if any(abs(r["late_fraction"] + r["exact_fraction"] - 1.0) > 1e-12 for r in timed):
        raise SystemExit("bench_results: a timed command was neither exact nor late")
    # per-repetition p1 of the virtual margin (Appendix E: ranges over the repetitions). Under
    # one margin threshold, every 2 ms repetition with more than 1 % late commands puts the
    # threshold above its p1, and every >= 4 ms repetition with under 1 % late puts it below
    # its p1, so the text brackets it between max p1(2 ms) and min p1(4 ms).
    p1 = {ld: [r["virtual_margin_us"]["p1"] for r in timed if lead_ms(r) == ld] for ld in leads}
    v["numBfourMarginTwoPoneMs"] = dec(min(p1[leads[0]]) / 1e3, 1)
    v["numBfourMarginTwoPoneMaxMs"] = dec(max(p1[leads[0]]) / 1e3, 1)
    v["numBfourMarginFourPoneMs"] = dec(min(p1[leads[1]]) / 1e3, 1)
    v["numBfourMarginFourPoneMaxMs"] = dec(max(p1[leads[1]]) / 1e3, 1)
    if not (all(r["late_fraction"] > 0.01 for r in timed if lead_ms(r) == leads[0])
            and all(r["late_fraction"] < 0.01 for r in timed if lead_ms(r) >= leads[1])
            and max(p1[leads[0]]) < min(p1[leads[1]])):
        raise SystemExit("bench_results: Appendix E brackets the late-command margin between "
                         "the p1 of the 2 and 4 ms leads (2 ms: > 1 % late in every "
                         "repetition; >= 4 ms: < 1 % late in every repetition)")
    late4_rep = [r["late_fraction"] for r in timed if lead_ms(r) == leads[1]]
    v["numBfourLateFourRepMaxPct"] = dec(100 * max(late4_rep), 2, mode=ROUND_CEILING)
    v["numBfourApplyPfiftyMaxMs"] = dec(max(r["apply_delay_us"]["p50"] for r in live) / 1e3, 1)
    v["numBfourApplyPninetynineMaxMs"] = dec(max(r["apply_delay_us"]["p99"] for r in live)
                                             / 1e3, 1)
    # the largest apply delay of a repetition that is not backlogged came with a page stall of
    # about the same length in that repetition (Appendix E)
    worst = max(live, key=lambda r: r["apply_delay_us"]["max"])
    a_max = worst["apply_delay_us"]["max"]
    if not (abs(worst["page_latency_us"]["max"] - a_max) <= 0.05 * a_max
            and sum(r["apply_delay_us"]["max"] > 0.5 * a_max for r in live) == 1):
        raise SystemExit("bench_results: the largest served apply delay is no longer one "
                         "repetition's page stall of about the same length")
    v["numBfourApplyMaxMs"] = dec(a_max / 1e3, 0)
    v["numBfourRttPninetynineMaxMs"] = dec(max(r["send_to_schedule_rtt_us"]["p99"]
                                               for r in live) / 1e3, 1)
    hi = served_reps(pr, hi_r)
    ach = [r["client"]["achieved_rate_hz"] for r in hi]  # per repetition
    v["numBfourAchievedMin"] = dec(min(ach), 0)
    v["numBfourAchievedMax"] = dec(max(ach), 0)
    v["numBfourPageLatMaxS"] = dec(max(r["page_latency_us"]["max"] for r in hi) / 1e6, 1)
    if pman["git_commit"] != b3m["git_commit"]:
        raise SystemExit("bench_results: B3 and B4 ran at different commits")
    prov["B4"] = {"results": str(BENCH / "B4"),
                  "runs": ["20261012T025506Z_b4_lockstep", "20261012T025715Z_b4_paced"],
                  "commit": short(pman["git_commit"]), "plan": ps.get("plan")}

    # ---------------------------------------------------------------- B4-classic (512 us)
    cr = read_json(root / "B4-classic" / "paced" / "paced.json")
    cs = read_json(root / "B4-classic" / "paced" / "summary.json")
    cman = read_json(root / "B4-classic" / "paced" / "manifest.json")
    if any(r["sustainable"] for r in cr) or cs.get("plan") != "classic" or cs["paced_failed"]:
        raise SystemExit("bench_results: B4-classic changed (Sustainable repetition or plan)")
    clive = [r for r in cr if r["rate_hz"] < hi_r]
    if any(r["backlogged"] for r in clive) or not all(r["backlogged"] for r in cr
                                                      if r["rate_hz"] >= hi_r):
        raise SystemExit("bench_results: B4-classic backlog pattern changed")
    if {int(r["page_samples"]) for r in cr} != {512} or {lead_ms(r) for r in cr
                                                         if r["mode"] == "timed"} != {2.0}:
        raise SystemExit("bench_results: B4-classic pages or lead changed")
    v["numBfourClassicPageUs"] = dec(1e6 * cr[0]["page_s"], 0)
    cm1 = [r["miss_rate"][SLACK_1MS] for r in clive]
    v["numBfourClassicMissMinPct"] = pct(min(cm1), 1)
    v["numBfourClassicMissMaxPct"] = pct(max(cm1), 1)
    clate = [cs["paced"][k]["late_fraction"] for k in cs["paced"]
             if cs["paced"][k]["mode"] == "timed" and cs["paced"][k]["rate_hz"] < hi_r]
    v["numBfourClassicLateMinPct"] = pct(min(clate), 1)
    v["numBfourClassicLateMaxPct"] = pct(max(clate), 1)
    v["numBfourClassicVisPninetynineMaxMs"] = dec(max(r["visible_latency_us"]["p99"]
                                                      for r in clive if r["mode"] == "asap")
                                                  / 1e3, 1)
    cach = [r["client"]["achieved_rate_hz"] for r in cr if r["rate_hz"] >= hi_r]  # per rep
    v["numBfourClassicAchievedMin"] = dec(min(cach), 0)
    v["numBfourClassicAchievedMax"] = dec(max(cach), 0)
    if cman["git_commit"] != pman["git_commit"]:
        raise SystemExit("bench_results: B4-classic ran at another commit than B4")
    prov["B4-classic"] = {"results": str(BENCH / "B4-classic"),
                          "run": "20261012T033849Z_b4_paced_classic",
                          "commit": short(cman["git_commit"])}

    # ---------------------------------------------------------------- E-F data-plane latency
    ef = read_json(paper_root / EF_LAT / "latency.json")
    efm = read_json(paper_root / EF_LAT / "manifest.json")
    if efm["git_dirty"] or not efm["platform"].startswith("Linux") or \
            (efm.get("aws_instance") or {}).get("type") != v["txtHostServer"]:
        raise SystemExit("bench_results: E-F latency is not from a clean Linux run on the "
                         "server instance type")
    inp, zq = ef["inproc"], ef["zmq_two_process"]
    v["numApiBufferSamples"] = integer(inp["buffer"])
    if zq["buffer"] != inp["buffer"]:
        raise SystemExit("bench_results: E-F in-process and ZMQ buffers differ")
    v["numApiSendPfiftyUs"] = dec(inp["send"]["p50_us"], 0)
    v["numApiSendPninetynineUs"] = dec(inp["send"]["p99_us"], 0)
    v["numApiRecvPfiftyUs"] = dec(inp["recv"]["p50_us"], 0)
    v["numApiInprocMsps"] = dec(inp["sustained_msps"], 2)
    v["numZmqRttPfiftyUs"] = integer(round(zq["round_trip"]["p50_us"]))
    v["numZmqRttPninetynineUs"] = integer(round(zq["round_trip"]["p99_us"]))
    v["numApiThroughputMsps"] = dec(zq["sustained_msps"], 2)
    v["numApiZmqBufferSamples"] = integer(zq["sustained_buffer"])
    busy = [s["cpu_busy_fraction"] for s in ef["host_load"]]
    v["numEfLatencyBusyMaxPct"] = pct(max(busy), 0)
    prov["E-F latency"] = {"results": str(EF_LAT), "commit": short(efm["git_commit"]),
                           "host": efm["aws_instance"]}

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
    busy6 = read_json(root / "B6" / "run" / "sections.json")["sremu"]["background_busy"]["mean"]
    v["numBsixBusyPct"] = pct(busy6, 0)
    b6m = read_json(root / "B6" / "run" / "manifest.json")
    prov["B6"] = {"results": str(BENCH / "B6"), "run": "20261011T185526Z_b6_laptop",
                  "commit": b6m["git_commit"], "dirty": b6m["git_dirty"],
                  "dirty_patch_sha256": b6m.get("dirty_patch_sha256")}

    tables = {"table7_platforms.tex": render_table7(root)}
    return v, prov, tables


# --------------------------------------------------------------------------- Table VII
def render_table7(root: Path) -> str:
    """Platforms and software versions of the benchmark runs (from the copied host files and
    run manifests)."""
    def aws(b: str, run_sub: str) -> dict[str, str]:
        man = read_json(root / b / run_sub / "manifest.json")
        meta = read_json(root / b / run_sub / "meta.json")
        lscpu = (root / b / "host" / "lscpu.txt").read_text(encoding="utf-8")
        mem = mem_gib((root / b / "host" / "meminfo.txt").read_text(encoding="utf-8"))
        sockets = int(lscpu_field(lscpu, "Socket(s)"))
        cores = int(lscpu_field(lscpu, "Core(s) per socket")) * sockets
        kernel = ".".join(man["platform"].split("-")[1].split(".")[:2])  # Linux-7.0.0-... -> 7.0
        sysinfo = root / b / "host" / "sysinfo.json"
        osname = "Linux"
        if sysinfo.is_file():
            m = re.search(r"~(\d+\.\d+)\.\d+-Ubuntu",
                          read_json(sysinfo)["uname"]["version"])
            osname = f"Ubuntu {m.group(1)}" if m else "Linux"
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
            "commit": man["git_commit"], "instance": man["aws_instance"]["id"],
        }

    srv = aws("B2", "run")
    f45 = aws("B2-F45", "run")
    pac = aws("B3", "run")
    b4 = aws("B4", "paced")
    b4c = aws("B4-classic", "paced")
    for name, h in (("B2-F45", f45),):
        if (h["host"], h["cpu"], h["cores"], h["py"], h["np"], h["nb"], h["zmq"]) != (
                srv["host"], srv["cpu"], srv["cores"], srv["py"], srv["np"], srv["nb"],
                srv["zmq"]):
            raise SystemExit(f"bench_results: {name} host or software differs from B2's row")
    for name, h in (("B4", b4), ("B4-classic", b4c)):
        if (h["host"], h["cpu"], h["cores"], h["py"], h["np"], h["nb"], h["zmq"], h["pin"]) != (
                pac["host"], pac["cpu"], pac["cores"], pac["py"], pac["np"], pac["nb"],
                pac["zmq"], pac["pin"]):
            raise SystemExit(f"bench_results: {name} host, software or pinning differs from "
                             "B3's row")
    insts = {pac["instance"], b4["instance"], b4c["instance"]}
    slots = read_json(root / "B2" / "run" / "slots.json")["slots"]
    b1_cpus = cpu_count(read_json(root / "B1" / "run" / "meta.json")["pinning"]["affinity"])
    srv["pin"] = (f"B1 on {b1_cpus} vCPUs with the rest of the host idle, B2 and B2-F45 as up "
                  f"to {len(slots)} concurrent points of {cpu_count(slots[0])} vCPUs")
    clients = read_json(root / "B3" / "host" / "sysinfo.json")["cpu_plan"]["clients"]
    pac["pin"] = (f"engine on {cpu_count(pac['pin'])} vCPUs, B4 command client on "
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
    if man6["git_dirty"] is not False:
        raise SystemExit("bench_results: Table VII says B6 ran from a clean tree")
    rows = [(r"\shortstack[l]{B1, B2,\\ B2-F45}", srv),
            (r"\shortstack[l]{B3, B4,\\ B4-classic}", pac), ("B6", lap)]
    # Tree state and commit per benchmark from its provenance record (MINOR-14): B1/B2
    # recorded a dirty flag although their uploaded bundle equals the commit's tree.
    state = {b: read_json(root / b / "meta.json")["sremu"]
             for b in ("B1", "B2", "B2-F45", "B3", "B4", "B4-classic")}
    flagged = [b for b, s in state.items() if s.get("dirty_flag_recorded")]
    if any("byte-identical" not in str(state[b].get("dirty_note", "")) for b in flagged):
        raise SystemExit("bench_results: a dirty AWS benchmark tree is not the commit's tree")
    groups: dict[str, list[str]] = {}
    for b, s in state.items():
        groups.setdefault(short(s["commit"]), []).append(b)
    parts = []
    for commit, bs in groups.items():
        fl = [b for b in bs if b in flagged]
        cl = [b for b in bs if b not in flagged]
        txt = (bs[0] if len(bs) == 1 else ", ".join(bs[:-1]) + " and " + bs[-1]) \
            + r" at \texttt{" + commit + "}"
        if fl and cl:
            raise SystemExit("bench_results: one commit with flagged and clean benchmarks")
        txt += (" (dirty flag recorded, uploaded file tree equal to the commit's)" if fl
                else " (clean tree)")
        parts.append(txt)
    lines = [
        "% GENERATED by paper/tools/bench_results.py (via gen_results.py) from",
        "% paper/sweeps/results/bench/*/host and the run manifests. Do not edit.",
        r"\begin{table*}[t]",
        r"\caption{Platforms and software of the benchmark runs (MEAS). Emulator commits: "
        + "; ".join(parts) + r"; B6 at \texttt{" + short(man6["git_commit"]) + r"} (clean tree)."
        r" Server: " + srv["pin"] + "; paced host: " + pac["pin"] + r". B3, B4 and B4-classic"
        r" ran on " + _fmt().prose_count(len(insts)) + r" instances of one type, B2 and B2-F45"
        r" on two of"
        r" another. n/r: not recorded by the run. No number is compared across instance types"
        r" or with the laptop.}",
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
