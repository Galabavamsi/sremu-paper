"""case_tables.py -- closed-form E-B2 timing and the per-geometry E-A table (Section VII,
Appendix F). Called by tools/gen_results.py, which passes its rounding helpers; reads nothing
itself.

E-B2 closed form (lockstep, latency Lambda = Lambda_0 + J U with U uniform on [0, 1], guard
T_g, preamble T_pre, tick T_t): a packet is *exposed* when the switch that prepares it lands
after its preamble,

* asap (issued at the guard start, applied on arrival): P(Lambda > T_g + T_pre);
* timed (switch requested at the guard centre, issued a lead ell earlier, late commands
  applied on arrival): P(Lambda > ell + T_g/2 + T_pre);
* tick-based (issued at the guard start, applied at the next tick of a free-running T_t
  clock): P(Lambda + W > T_g + T_pre), W the wait for the next tick, uniform on (0, T_t]
  because the guard starts fall at equally often visited tick phases (checked by
  :func:`tick_phase_grid`); at J = 0 this is 1 - (T_g + T_pre - Lambda_0)/T_t. The closed
  form ignores the overwrite of a pending tick-based command by a later one.

Everything is exact rational arithmetic (the tick-based case integrates a piecewise-linear
function exactly).
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from decimal import ROUND_CEILING, Decimal
from fractions import Fraction
from itertools import pairwise
from math import gcd
from pathlib import Path
from typing import Any

Fmt = Callable[..., str]


def _clamp01(x: Fraction) -> Fraction:
    return min(Fraction(1), max(Fraction(0), x))


def p_latency_exceeds(x: Fraction, lat0: Fraction, jit: Fraction) -> Fraction:
    """P(Lambda_0 + J U > x), U uniform on [0, 1]."""
    if jit == 0:
        return Fraction(1) if lat0 > x else Fraction(0)
    return _clamp01((lat0 + jit - x) / jit)


def p_tick_exceeds(x: Fraction, lat0: Fraction, jit: Fraction, tick: Fraction) -> Fraction:
    """P(Lambda_0 + J U + W > x), W uniform on (0, T_t] and independent of U."""
    a = x - lat0

    def pw(y: Fraction) -> Fraction:  # P(W > y)
        return _clamp01((tick - y) / tick)

    if jit == 0:
        return pw(a)
    # g(u) = P(W > a - J u) is linear between its breakpoints (arg = 0 or T_t)
    pts = {Fraction(0), Fraction(1)}
    for c in (a - tick, a):
        u = c / jit
        if 0 < u < 1:
            pts.add(u)
    knots = sorted(pts)
    return sum(((pw(a - jit * u0) + pw(a - jit * u1)) / 2 * (u1 - u0)
                for u0, u1 in pairwise(knots)), Fraction(0))


def eb2_closed_form(sem: str, guard: Fraction, jit: Fraction, lead: Fraction | None,
                    lat0: Fraction, t_pre: Fraction, tick: Fraction) -> Fraction:
    """Probability that a packet is exposed (switch after its preamble), per semantics."""
    if sem == "timed":
        if lead is None:
            raise ValueError("timed semantics need a lead")
        return p_latency_exceeds(lead + guard / 2 + t_pre, lat0, jit)
    if sem == "asap":
        return p_latency_exceeds(guard + t_pre, lat0, jit)
    if sem == "tick-based":
        return p_tick_exceeds(guard + t_pre, lat0, jit, tick)
    raise ValueError(f"unknown semantics {sem!r}")


def tick_phase_grid(guard: Fraction, body: Fraction, tick: Fraction, n_packets: int) -> Fraction:
    """Spacing of the tick phases (m (T_p + T_g)) mod T_t of the guard starts over the run;
    raises unless every phase of that grid is visited equally often (the uniform-W premise)."""
    slot = body + guard
    phases = Counter((m * slot) % tick for m in range(n_packets))
    den = 1
    for p in [*phases, slot, tick]:
        den = den * p.denominator // gcd(den, p.denominator)
    step = Fraction(gcd(int(slot * den), int(tick * den)), den)
    if len(phases) != tick / step or len(set(phases.values())) != 1:
        raise SystemExit("case_tables: tick phases of the E-B2 guard starts are not uniform")
    return step


def _overwrite(r: Mapping[str, str]) -> bool:
    v = r.get("tick_overwritten_messages") or ""
    return v not in ("", "0", "0.0")


def _lead(r: Mapping[str, str]) -> Fraction | None:
    return Fraction(r["lead_s"]) if r.get("lead_s") else None


def eb2_compare(rows: Sequence[Mapping[str, str]], lat0: Fraction, t_pre: Fraction,
                tick: Fraction, es_hi: str, es_lo: str) -> dict[str, Any]:
    """Closed form against every E-B2 row; the statistics that Section VII-A and
    Appendix F quote (raises if a statement made there no longer holds)."""
    out: list[dict[str, Any]] = []
    for r in rows:
        cf = eb2_closed_form(r["semantics"], Fraction(r["guard_s"]), Fraction(r["jitter_s"]),
                             _lead(r), lat0, t_pre, tick)
        out.append({"row": r, "cf": cf, "per": float(r["per"]), "lo": float(r["per_ci_low"]),
                    "hi": float(r["per_ci_high"]), "ow": _overwrite(r)})
    blk = [x for x in out if x["row"]["los"] == "blocked" and x["row"]["es_n0_target_db"] == es_hi]
    at = [x for x in blk if x["row"]["semantics"] in ("asap", "timed")]
    tk = [x for x in blk if x["row"]["semantics"] == "tick-based"]
    # blocked, asap and timed: never below the closed form beyond its interval, zero where
    # the closed form is zero; the excess (switches late in the preamble) is quoted
    for x in at + [x for x in tk if not x["ow"]]:
        if x["hi"] < float(x["cf"]):
            raise SystemExit("case_tables: a blocked E-B2 cell lies below the closed form")
        if x["cf"] == 0 and x["per"] != 0.0:
            raise SystemExit("case_tables: a blocked E-B2 cell with closed form 0 lost packets")
    pos = [x for x in at if x["cf"] > 0]
    in_ci = [x for x in pos if x["lo"] <= float(x["cf"]) <= x["hi"]]
    worst = max(at, key=lambda x: x["per"] - float(x["cf"]))
    excess = worst["per"] - float(worst["cf"])
    if Fraction(worst["row"]["guard_s"]) != min(Fraction(x["row"]["guard_s"]) for x in at):
        raise SystemExit("case_tables: the largest excess over the closed form is no longer at "
                         "the shortest guard (Appendix F says so)")
    ow = [x for x in tk if x["ow"]]
    below = [x for x in blk if x["hi"] < float(x["cf"])]
    if not below or not all(x["ow"] for x in below):
        raise SystemExit("case_tables: blocked cells below the closed form are not all "
                         "tick-based overwrite cells")
    tick_free_excess = max(x["per"] - float(x["cf"]) for x in tk if not x["ow"])
    # LOS present: the fraction of exposed packets that fail (low Es/N0, both alphabets)
    los_lo = [x for x in out if x["row"]["los"] == "present" and not x["ow"]
              and x["row"]["es_n0_target_db"] == es_lo and x["cf"] >= Fraction(1, 10)]
    ratio = [x["per"] / float(x["cf"]) for x in los_lo]
    los_hi = [x for x in out
              if x["row"]["los"] == "present" and x["row"]["es_n0_target_db"] == es_hi]
    half = max(max(x["per"] - x["lo"], x["hi"] - x["per"]) for x in out)
    return {
        "rows": out,
        "n_blocked_at_pos": len(pos),
        "n_blocked_at_in_ci": len(in_ci),
        "blocked_at_max_excess": excess,
        "blocked_tick_free_max_excess": tick_free_excess,
        "n_tick_overwrite_cells": len(ow),
        "los_lo_ratio_min": min(ratio),
        "los_lo_ratio_max": max(ratio),
        "los_hi_per_max": max(x["per"] for x in los_hi),
        "ci_half_max": half,
    }


_SEM_ORDER = (("timed", "5e-4"), ("timed", "2e-3"), ("timed", "5e-3"), ("asap", None),
              ("tick-based", None))


def render_table_eb2(cmp: Mapping[str, Any], *, guards: Sequence[Fraction],
                     jitters: Sequence[Fraction], es_hi: str, es_lo: str, n_packets: int,
                     lat0: Fraction, zero_upper: float, dec: Fmt) -> str:
    """Appendix F table: the full pre-declared E-B2 grid of the arc run with the closed form."""
    rows = cmp["rows"]

    def ms(x: Fraction) -> str:
        v = 1000 * Fraction(x)
        return dec(Decimal(v.numerator) / Decimal(v.denominator), 1 if v % 1 else 0)

    def cell(sem: str, lead: str | None, g: Fraction, j: Fraction, los: str, es: str,
             alpha: str) -> dict[str, Any]:
        hit = [x for x in rows if x["row"]["semantics"] == sem and x["row"]["los"] == los
               and x["row"]["es_n0_target_db"] == es and x["row"]["alphabet"] == alpha
               and Fraction(x["row"]["guard_s"]) == g and Fraction(x["row"]["jitter_s"]) == j
               and (lead is None or Fraction(x["row"]["lead_s"]) == Fraction(lead))]
        if len(hit) != 1:
            raise SystemExit(f"case_tables: {len(hit)} E-B2 rows for {sem}/{lead}/{g}/{j}/"
                             f"{los}/{es}/{alpha}")
        return hit[0]

    def p(x: float) -> str:  # one packet in 2000 shows as 0.05, not 0.0
        return "0" if x == 0 else dec(100.0 * x, 1 if 100.0 * x >= 0.1 else 2)

    n_cols = 4 * len(jitters)
    head_j = " & ".join(
        rf"\multicolumn{{4}}{{c}}{{$J={ms(j)}$\,ms}}" if j else r"\multicolumn{4}{c}{$J=0$}"
        for j in jitters)
    rules = " ".join(rf"\cmidrule(lr){{{3 + 4 * i}-{6 + 4 * i}}}" for i in range(len(jitters)))
    sub = " & ".join(["CF & B & L & I"] * len(jitters))
    spec = "@{}ll" + "".join(r"@{\hspace{5pt}}rrrr" for _ in jitters) + "@{}"
    lines = [
        "% GENERATED by paper/tools/gen_results.py (tools/case_tables.py) from",
        "% paper/sweeps/results/E-B-arc/E-B2/eb2_per.csv and eb_plan.json. Do not edit.",
        r"\begin{table*}[t]",
        r"\caption{E-B2 on the arc, the full pre-declared grid: packet error rate in percent of "
        rf"{n_packets} packets per cell, by semantics (timed: lead $\ell$), guard "
        rf"$T_{{\mathrm{{g}}}}$ and jitter $J$ of the latency "
        rf"$\Lambda={ms(lat0)}\,\mathrm{{ms}}+JU$. CF: closed-form probability "
        r"that the switch lands after the preamble (Appendix~\ref{app:cases}); B: laboratory "
        rf"alphabet, line of sight blocked, $E_s/N_0={dec(float(es_hi), 0)}\,\mathrm{{dB}}$; "
        r"L, I: laboratory and ideal 1-bit alphabets, line of sight present, "
        rf"${dec(float(es_lo), 0)}\,\mathrm{{dB}}$. With the line of sight present at "
        rf"${dec(float(es_hi), 0)}\,\mathrm{{dB}}$ no cell of either alphabet exceeds "
        rf"{dec(100.0 * cmp['los_hi_per_max'], 1)}\,\%. $\dagger$: tick-based commands overwritten "
        r"before their tick, which the closed form ignores. Clopper--Pearson 95\,\% half-widths "
        rf"$\le{dec(100.0 * cmp['ci_half_max'], 1)}$ points (upper limit "
        rf"{dec(100.0 * zero_upper, 2)}\,\% at zero errors). SIM-sremu; laboratory amplitudes "
        r"ASSUMED.}",
        r"\label{tab:eb2grid}",
        r"\centering",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{1.6pt}",
        rf"\begin{{tabular}}{{{spec}}}",
        r"\toprule",
        rf" & & {head_j} \\",
        rules,
        rf"Semantics & $T_{{\mathrm{{g}}}}$\,(ms) & {sub} \\",
        r"\midrule",
    ]
    for sem, lead in _SEM_ORDER:
        label = {"timed": rf"timed, $\ell={ms(Fraction(lead))}$\,ms" if lead else "",
                 "asap": "asap", "tick-based": "tick-based"}[sem]
        for gi, g in enumerate(guards):
            cells = []
            for j in jitters:
                b = cell(sem, lead, g, j, "blocked", es_hi, "lab")
                lo_l = cell(sem, lead, g, j, "present", es_lo, "lab")
                lo_i = cell(sem, lead, g, j, "present", es_lo, "ideal1")
                dag = r"$^\dagger$" if b["ow"] else ""
                cells += [p(float(b["cf"])), p(b["per"]) + dag, p(lo_l["per"]) + dag,
                          p(lo_i["per"]) + dag]
            lines.append(f"{label if gi == 0 else ''} & {ms(g)} & " + " & ".join(cells) + r" \\")
        if (sem, lead) != _SEM_ORDER[-1]:
            lines.append(r"\addlinespace[2pt]")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table*}"]
    if len(lines[-4].split("&")) != 2 + n_cols:
        raise SystemExit("case_tables: E-B2 table row width")
    return "\n".join(lines) + "\n"


def ea_geometries(tau_rows: Sequence[Mapping[str, str]], gains: Sequence[Mapping[str, str]],
                  speed: float, c0: float) -> list[dict[str, Any]]:
    """Per computed E-A geometry: carrier, offset, partition loss, tau_half with interval, in
    units of lambda/v, P(0) and the instantaneous-optimum gain (mean of per-batch dB)."""
    out = []
    for r in tau_rows:
        if r["tau_half_flag"] != "ok":
            raise SystemExit(f"case_tables: E-A tau_half of {r['geometry']} is censored")
        inst = [g for g in gains if g["geometry"] == r["geometry"] and g["controller"] == "instant"]
        if len(inst) != 1:
            raise SystemExit(f"case_tables: {len(inst)} instant rows for {r['geometry']}")
        g = inst[0]
        fc = float(g["fc_hz"])
        lam_v = c0 / fc / speed
        out.append({"geometry": r["geometry"], "fc_hz": fc, "h_m": float(g["h_m"]),
                    "loss_db": float(g["loss_db"]), "tau": float(r["tau_half_s"]),
                    "tau_lo": float(r["tau_half_ci_low_s"]),
                    "tau_hi": float(r["tau_half_ci_high_s"]),
                    "tau_lv": float(r["tau_half_s"]) / lam_v,
                    "p0_dbm": float(r["p0_dbm"]), "gain_db": float(g["gain_db"])})
    out.sort(key=lambda x: (x["fc_hz"], x["h_m"], x["loss_db"]))
    return out


def render_table_ea(geos: Sequence[Mapping[str, Any]], *, primary: Sequence[str], speed: str,
                    n_batches: str, dec: Fmt) -> str:
    """Appendix F table: tau_half over every computed E-A geometry."""
    lines = [
        "% GENERATED by paper/tools/gen_results.py (tools/case_tables.py) from",
        "% paper/sweeps/results/E-A/tau_half.csv and gains.csv. Do not edit.",
        r"\begin{table}[t]",
        r"\caption{E-A over every computed geometry: half-retention delay $\tau_{1/2}$ of the "
        rf"exact optimum (95\,\% interval over {n_batches} batches), also in units of $\lambda/v$ "
        rf"($v={speed}\,\mathrm{{m/s}}$), received power with the instantaneous optimum $P(0)$, "
        r"and its gain over RIS absent (mean of per-batch dB gains). $h$: distance of the cell row "
        r"from the user line; Part.: partition loss, applied to the line of sight, not the RIS "
        r"hops. *: primary geometries of Section~\ref{sec:cases-ea}. "
        r"SIM-sremu.}",
        r"\label{tab:eageo}",
        r"\centering",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{2.5pt}",
        r"\begin{tabular}{@{}rrrrrrr@{}}",
        r"\toprule",
        r"$f_c$ & $h$ & Part. & $\tau_{1/2}$ & 95\,\% interval & $\tau_{1/2}v/\lambda$ & "
        r"$P(0)$\,/\,gain \\",
        r"(GHz) & (m) & (dB) & (s) & (s) & & (dBm\,/\,dB) \\",
        r"\midrule",
    ]
    for x in geos:
        star = "*" if x["geometry"] in primary else ""
        lines.append(
            f"{dec(x['fc_hz'] / 1e9, 1)}{star} & {dec(x['h_m'], 0)} & {dec(x['loss_db'], 0)} & "
            f"{dec(x['tau'], 3)} & {dec(x['tau_lo'], 3)}--{dec(x['tau_hi'], 3)} & "
            f"{dec(x['tau_lv'], 2)} & {dec(x['p0_dbm'], 1)}\\,/\\,{dec(x['gain_db'], 2)} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- gen_results hooks
def _thin(n: int) -> str:
    s = f"{int(n):d}"
    if len(s) < 5:
        return s
    groups = []
    while s:
        groups.insert(0, s[-3:])
        s = s[:-3]
    return "\\,".join(groups)


def _ms(x: Fraction, nd: int, dec: Fmt) -> str:
    v = 1000 * Fraction(x)
    return dec(Decimal(v.numerator) / Decimal(v.denominator), nd)


def _exact(x: Fraction) -> Decimal:
    return Decimal(x.numerator) / Decimal(x.denominator)


def eb2_values(v: dict[str, str], tables: dict[str, str], a2: Sequence[Mapping[str, str]], *,
               traffic: Mapping[str, Any], rate_sps: Any, lat_fixed: Fraction,
               guards: Sequence[Fraction], jits: Sequence[Fraction], guard_f9: str,
               lead_f9: str, es: tuple[str, str], n_packets: int, dec: Fmt) -> None:
    """Section VII-A / Appendix F macros of the E-B2 closed form and Table tab:eb2grid."""
    es_lo, es_hi = es
    rate = Fraction(str(rate_sps))
    sps, n_pre, n_pay = (int(traffic[k]) for k in ("sps", "preamble_syms", "payload_syms"))
    t_pre = Fraction(n_pre * sps) / rate
    body = Fraction((n_pre + n_pay) * sps) / rate
    tick = Fraction(v["numEbTickMs"]) / 1000
    steps = [tick_phase_grid(g, body, tick, n_packets) for g in guards]
    cmp = eb2_compare(a2, lat_fixed, t_pre, tick, es_hi, es_lo)

    def row(sem: str, g: Fraction, j: Fraction, lead: str | None = None) -> dict[str, Any]:
        hit = [x for x in cmp["rows"] if x["row"]["semantics"] == sem
               and x["row"]["los"] == "blocked" and x["row"]["es_n0_target_db"] == es_hi
               and x["row"]["alphabet"] == "lab" and Fraction(x["row"]["guard_s"]) == g
               and Fraction(x["row"]["jitter_s"]) == j
               and (lead is None or Fraction(x["row"]["lead_s"]) == Fraction(lead))]
        if len(hit) != 1:
            raise SystemExit(f"case_tables: {len(hit)} blocked E-B2 rows for {sem}/{g}/{j}")
        return hit[0]

    gf9, j1, j3 = Fraction(guard_f9), Fraction(1, 1000), Fraction(3, 1000)
    a1, t3 = row("asap", gf9, j1), row("timed", gf9, j3, lead_f9)
    v["numEbCfAsapOneMeasPct"] = dec(100.0 * a1["per"], 1)
    v["numEbCfAsapOnePct"] = dec(100.0 * float(a1["cf"]), 1)
    v["numEbCfTimedThreeMeasPct"] = dec(100.0 * t3["per"], 1)
    v["numEbCfTimedThreePct"] = dec(100.0 * float(t3["cf"]), 1)
    v["numEbCfMaxExcessPts"] = dec(100.0 * cmp["blocked_at_max_excess"], 1)
    v["numEbCfCellsPos"] = str(cmp["n_blocked_at_pos"])
    v["numEbCfCellsInCi"] = str(cmp["n_blocked_at_in_ci"])
    v["numEbCfTickExcessPts"] = dec(100.0 * cmp["blocked_tick_free_max_excess"], 1)
    v["numEbTickOverwriteCells"] = str(cmp["n_tick_overwrite_cells"])
    v["numEbLosLoExposedMinPct"] = dec(100.0 * cmp["los_lo_ratio_min"], 0)
    v["numEbLosLoExposedMaxPct"] = dec(100.0 * cmp["los_lo_ratio_max"], 0)
    v["numEbLosHiPerMaxPct"] = dec(100.0 * cmp["los_hi_per_max"], 1)
    v["numEbTpreMs"] = _ms(t_pre, 3, dec)
    # the asap threshold moves with the guard (blocked, J = 1 ms)
    short, long_ = min(guards), max(guards)
    if row("asap", long_, j1)["per"] != 0.0 or row("asap", short, j1)["per"] <= a1["per"]:
        raise SystemExit("case_tables: the asap loss at J = 1 ms no longer falls with the guard")
    v["numEbGuardShortMs"] = _ms(short, 1, dec)
    v["numEbGuardLongMs"] = _ms(long_, 0, dec)
    v["numEbAsapShortGuardPct"] = dec(100.0 * row("asap", short, j1)["per"], 0)
    # a switch early in the preamble costs nothing: the latest worst-case switch, measured from
    # the packet start, among the blocked asap/timed cells that lost no packet
    late = []
    for x in cmp["rows"]:
        r = x["row"]
        if (r["los"], r["es_n0_target_db"], r["alphabet"]) != ("blocked", es_hi, "lab") \
                or r["semantics"] not in ("asap", "timed") or x["per"] != 0.0:
            continue
        g, j = Fraction(r["guard_s"]), Fraction(r["jitter_s"])
        start = g if r["semantics"] == "asap" else Fraction(r["lead_s"]) + g / 2
        if lat_fixed + j > start:  # the worst switch falls inside the packet
            late.append(lat_fixed + j - start)
    if not late or max(late) >= t_pre:
        raise SystemExit("case_tables: no loss-free switch inside the preamble")
    v["numEbPreSafeUs"] = dec(_exact(max(late) * 10**6), 0)
    tick0 = [eb2_closed_form("tick-based", g, Fraction(0), None, lat_fixed, t_pre, tick)
             for g in guards]
    if any(c != 1 - (g + t_pre - lat_fixed) / tick for c, g in zip(tick0, guards, strict=True)):
        raise SystemExit("case_tables: the J = 0 tick-based closed form is not 1 - (...)/T_t")
    v["numEbCfTickZeroMinPct"] = dec(100.0 * float(min(tick0)), 0)
    v["numEbCfTickZeroMaxPct"] = dec(100.0 * float(max(tick0)), 0)
    v["numEbTickPhaseMinUs"] = dec(_exact(min(steps) * 10**6), 0)
    v["numEbTickPhaseMaxUs"] = dec(_exact(max(steps) * 10**6), 0)
    zero_upper = min(float(r["per_ci_high"]) for r in a2 if int(r["packet_errors"]) == 0)
    tables["tableF_eb2grid.tex"] = render_table_eb2(
        cmp, guards=guards, jitters=jits, es_hi=es_hi, es_lo=es_lo, n_packets=n_packets,
        lat0=lat_fixed, zero_upper=zero_upper, dec=dec)


def ea_values(v: dict[str, str], tables: dict[str, str], *, sw: Path, sremu_root: Path,
              plan_text: str, va1: Sequence[Mapping[str, str]], tol_db: float, speed: float,
              primary: Sequence[str], c0: float, dec: Fmt) -> None:
    """Section VII-B / Appendix F macros: the E-A evaluator and its checks, VA-1 coverage,
    tau_half over every computed geometry (Table tab:eageo), the WCL heuristic parameters."""
    meta = json.loads((sw / "E-A" / "meta.json").read_text(encoding="utf-8"))
    asm = " | ".join(meta["assumptions"])
    fine = re.search(r"sampled on a ([\d.]+) mm \(([\d.]+) mm above [\d.]+ GHz\) grid", asm)
    coarse = re.search(r"on a (\d+) cm GridChannel", asm)
    step = re.search(r"(\d+) ms pieces split at switch times, each evaluated at its mid point",
                     asm)
    if not (fine and coarse and step and "evaluation rule per point: mid" in asm
            and "vectorized form (experiments/ea.py)" in asm):
        raise SystemExit("case_tables: the E-A evaluator record changed")
    v["numEaFineGridMm"], v["numEaFineGridHiMm"] = fine.group(1), fine.group(2)
    v["numEaCoarseGridCm"], v["numEaStepMs"] = coarse.group(1), step.group(1)
    errs = [c["interp_max_db_err_all_on"] for c in meta["checks_per_geometry"].values()]
    v["numEaInterpMaxDb"] = dec(max(errs), 3, mode=ROUND_CEILING)
    dtc = meta["decision_table_checks"]
    if dtc["decision_mismatches"] or dtc["index_mismatches"]:
        raise SystemExit("case_tables: E-A decision tables differ from the live controllers")
    v["numEaDecisionBarriers"] = _thin(int(dtc["barriers"]))
    # VA-1 coverage (one geometry, two controllers, two delays, noise on/off, mean power)
    taus = sorted({Fraction(r["tau_sui_s"]) for r in va1})
    if {r["controller"] for r in va1} != {"exact", "urap"} or len(taus) != 2:
        raise SystemExit("case_tables: the VA-1 design changed (Section VII-B names it)")
    v["numVaOneTauMinS"] = dec(_exact(taus[0]), 1)
    v["numVaOneTauMaxS"] = dec(_exact(taus[1]), 0)
    by = {(r["controller"], r["tau_sui_s"], r["noise"]): float(r["p_iq_dbm"]) for r in va1}
    nd = max(abs(by[(c, t, "on")] - by[(c, t, "off")]) for c, t, _ in by)
    v["numVaOneNoiseDiffDb"] = f"\\ensuremath{{10^{{{math.ceil(math.log10(nd))}}}}}"
    vl = max(abs(float(r["p_vectorized_left_dbm"]) - float(r["p_run_batch_left5ms_dbm"]))
             for r in va1)
    v["numVaOneVecLeftDb"] = dec(vl, 4, mode=ROUND_CEILING)
    fail = [r for r in va1 if abs(float(r["diff_iq_minus_run_batch_db"])) > tol_db]
    if not fail or any(r["controller"] != "exact" or Fraction(r["tau_sui_s"]) != taus[0]
                       for r in fail):
        raise SystemExit("case_tables: run_batch no longer fails VA-1 only for the exact "
                         "optimum at the shorter delay")
    # Amendment 1 of the plan: the short IQ run that led to the midpoint rule
    am = re.search(r"smoke\s+test,\s+(\d+)\s+s,\s+ExactOptimum,\s+tau_SUI\s+=\s+([\d.]+)\s+s.*?"
                   r"left\s+point\s+on\s+the\s+\d+\s+ms\s+grid\s+\+([\d.]+)\s+dB.*?"
                   r"midpoint\s+on\s+the\s+\d+\s+ms\s+grid\s+-([\d.]+)\s+dB", plan_text, re.S)
    if not am:
        raise SystemExit("case_tables: cannot read Amendment 1 of E-A.md")
    v["numEaSmokeS"], v["numEaSmokeTauS"], v["numEaSmokeLeftDb"], v["numEaSmokeMidDb"] = \
        am.group(1, 2, 3, 4)
    # the WCL heuristic as run: plan parameters, checked against the experiment code
    w = re.search(r"WclHeuristic\(L = (\d+), M = (\d+)\)`?\s+\(K = (\d+) candidates", plan_text)
    code = (sremu_root / "experiments" / "ea.py").read_text(encoding="utf-8")
    wc = re.search(r'WclHeuristic\((\d+), (\d+), name="wcl_heuristic"', code)
    if not (w and wc and w.group(1, 2) == wc.group(1, 2)):
        raise SystemExit("case_tables: WCL heuristic parameters differ between plan and code")
    big_l, big_m, big_k = (int(x) for x in w.group(1, 2, 3))
    if big_k != big_l * big_m * (big_m + 1) // 2:
        raise SystemExit("case_tables: WCL candidate count is not L M (M+1)/2")
    v["numEaWclL"], v["numEaWclM"], v["numEaWclK"] = str(big_l), str(big_m), str(big_k)
    # tau_half over every computed geometry
    with open(sw / "E-A" / "tau_half.csv", newline="", encoding="utf-8") as f:
        tau_rows = list(csv.DictReader(f))
    with open(sw / "E-A" / "gains.csv", newline="", encoding="utf-8") as f:
        gains = list(csv.DictReader(f))
    geos = ea_geometries(tau_rows, gains, speed, c0)
    if not set(primary) <= {g["geometry"] for g in geos}:
        raise SystemExit("case_tables: the primary E-A geometries are not in tau_half.csv")
    # Section VII-B: tau_half rises with the partition loss and falls with the offset h
    for key, var in ((("fc_hz", "h_m"), "loss_db"), (("fc_hz", "loss_db"), "h_m")):
        groups: dict[tuple[float, float], list[dict[str, Any]]] = {}
        for g in geos:
            groups.setdefault((g[key[0]], g[key[1]]), []).append(g)
        for grp in groups.values():
            t = [x["tau"] for x in sorted(grp, key=lambda x: x[var])]
            rising = all(a < b for a, b in pairwise(t))
            falling = all(a > b for a, b in pairwise(t))
            if not (rising if var == "loss_db" else falling):
                raise SystemExit(f"case_tables: tau_half is not monotone in {var}")
    v["numEaGeometries"] = str(len(geos))
    v["numEaTauLambdaVMin"] = dec(min(g["tau_lv"] for g in geos), 2)
    v["numEaTauLambdaVMax"] = dec(max(g["tau_lv"] for g in geos), 2)
    f_lo = min(g["fc_hz"] for g in geos)
    t_lo = [g["tau"] for g in geos if g["fc_hz"] == f_lo]
    v["numEaTauHalfMinS"], v["numEaTauHalfMaxS"] = dec(min(t_lo), 3), dec(max(t_lo), 3)
    p1 = next(g for g in geos if g["geometry"] == primary[0])
    nop = [g for g in geos
           if (g["fc_hz"], g["h_m"], g["loss_db"]) == (p1["fc_hz"], p1["h_m"], 0.0)]
    if len(nop) != 1:
        raise SystemExit("case_tables: no unpartitioned twin of the primary E-A geometry")
    v["numEaTauLvNoPart"] = dec(nop[0]["tau_lv"], 2)
    tables["tableF_eageo.tex"] = render_table_ea(geos, primary=primary, speed=dec(speed, 0),
                                                 n_batches=v["numEaBatches"], dec=dec)
