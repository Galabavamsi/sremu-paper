# E-A pre-registration: signaling delay in closed-loop binary RIS control

Status: written before any E-A sweep was run with sremu (WP11, 10 Oct 2026). The lead
commits this file before the E-A results are committed, so its git timestamp precedes them;
the user deposits it with the V-LAB protocol (human gate H-6) before the paper is posted.

Disclosure: the author of this file knew the [PILOT] numbers of DESIGN.md section 11 (E-A
pilot `tsui_check.py`: a scalar re-implementation with the AP on the user line for the RIS
hops and a hold-without-latency update rule). Those numbers are sizing aids only; nothing
below is tuned to them, and the hypotheses are DESIGN.md's, copied verbatim. What this file
adds is the operational definition of every quantity and the decision rules, fixed before
the data exist.

## 1. Scenario (SIM geometry)

Template `sremu/scenarios/studies/ea_corridor.yaml`; every sweep point is derived from it by
`sremu/experiments/ea.py` (sweep specs `paper/sweeps/ea_*.yaml`).

* User line: x in [0, l], l = 10 m, y = 0, z = 0. The MU follows a 1-D random waypoint
  (Bettstetter, no pause) at v = 1 m/s, legs keyed by `mobility/mu` from the scenario seed
  20261010 (one trajectory realization, shared by every controller and every geometry:
  paired comparisons). Mean movement period l/(3v) = 3.333 s.
* RIS: a line of N cells at y = h facing the user line, cell width = length = k = lambda/4,
  N = floor(l/k) (133 at 1 GHz, 320 at 2.4 GHz), `on_off` alphabet (OFF absorbing, ON 1 at
  0 deg), element granularity, K1 kernel with `cell_model: point`.
* AP at (0, -d0, 0), d0 = 0.5 m (documented deviation of V-WCL: offset perpendicular to the
  user line, on the side away from the RIS). P_T = 0 dBm, isotropic antennas.
* A0 (reproduction): no partition, f_c = 1 GHz, h in {1, 2, 4, 6} m.
* A1 (research regime): a partition polygon in the plane y = -0.25 m crossing every AP-MU
  LOS segment, transmission loss L_p = 20 dB, h in {2, 4} m, f_c in {1, 2.4} GHz;
  sensitivity L_p in {10, 30} dB at h = 2 m (both carriers). sremu applies the partition
  factor to the LOS path only (DESIGN.md 5.2); RIS hops are unattenuated.

## 2. Controllers and timing

* Command latency 1 ms (fixed) for every controller; a command issued at barrier t_b takes
  effect at t_b + 1 ms (RIS-time convention of the batch path).
* `RisAbsent`: RIS disabled (counterfactual baseline).
* `Urap(p = 0.2)`: a fresh random activation vector every tau_SUI (period = tau_SUI).
* `WclHeuristic(L = 4, M = 3)` (K = 24 candidates, phasor phases) and `ExactOptimum`, each
  wrapped in `DelayedController(tau_SUI, report_delay = 0)`: at t_b = m tau_SUI the
  controller reads the MU position at t_b and looks its decision up on a 1 cm position grid
  (`GridChannel` built from the scene model at x = 0, 0.01, ..., 10 m). Oracle geometry: the
  controller's channel model is the scene model (labelled so).
* `ProbeBandit` (SHOULD): UCB1 (c = 1) over a 16-entry codebook of exact optima at
  x_i = (i + 1/2) l/16, i = 0..15; reward = windowed received power (window = dwell/2 before
  each barrier, normalized by the largest power seen). Its signalling interval is its
  decision period: dwell = max(20 ms, tau_SUI) (20 ms is its native dwell; a power report
  cannot reach it more often than every tau_SUI).
* tau_SUI in {0.01, 0.03, 0.1, 0.3, 1, 3, 10} s.
* References for the retention ratio (ExactOptimum, same 1 cm lookup):
  * P(0): the decision for the MU's current position at every evaluation piece, no latency
    (the tau_SUI -> 0 limit);
  * P_stale: at every piece of batch b, the decision for the MU position at the same offset
    in batch (b + 10) mod 20, i.e. a configuration optimal for an independent position.

## 3. Evaluation (batch path)

* Narrowband coefficient h(t) = h_direct(x(t)) + sum_n Z[s_n(t), n](x(t)) from the scene
  model; received power P_T |h|^2. Evaluated on a 5 ms grid split at every command switch
  time (left-point rule of `sremu.engine.batch.run_batch`). The channel at x(t) is the scene
  model sampled on a 1 mm grid and interpolated complex-linearly (vectorized evaluation);
  its agreement with `run_batch` is checked and reported on a sub-window.
* Duration: 20 non-overlapping batches of 333 333 steps of 5 ms (1666.665 s, i.e. 500 mean
  movement periods) = 33 333.3 s per configuration.
* Per batch b: time-averaged received power P_b. Absolute power: mean of P_b with the
  Student-t 95 % interval of the 20 batch means (in linear power, quoted in dBm).
  Gain over `RisAbsent`: g_b = 10 log10(P_b / P_b^absent) (paired, same trajectory); quoted
  as the mean of g_b with its Student-t 95 % interval (19 d.o.f.).
* Retention (linear power): rho(tau) = (P(tau) - P_stale)/(P(0) - P_stale).
  tau_half: the smallest tau at which rho, interpolated linearly in rho and linearly in
  log10 tau between adjacent sweep points, falls to 0.5. If rho(0.01 s) < 0.5 it is
  left-censored (< 0.01 s); if rho stays >= 0.5 up to 10 s it is right-censored (> 10 s).
  Point estimate: from the powers of the full run. 95 % CI: Student-t interval (19 d.o.f.)
  of log10 tau_half,b over the 20 per-batch estimates (each from that batch's P_b(tau),
  P_b(0), P_b,stale), transformed back. A censored batch estimate makes the CI open on that
  side (reported as such).

## 4. Hypotheses (DESIGN.md section 11, verbatim) and decision rules

* **H-A1.** With retention rho(tau) = (P(tau) - P_stale)/(P(0) - P_stale) in linear power,
  the half-retention delay tau_half of `ExactOptimum` at 1 GHz, h = 2 lies in [0.1, 1] s,
  i.e. >= 3x shorter than the mean movement period l/(3v) = 3.33 s.
  Primary geometry: A1, L_p = 20 dB, f_c = 1 GHz, h = 2 m.
  *Supported* if the 95 % CI of tau_half lies inside [0.1, 1] s; *not supported* if the CI
  does not intersect [0.1, 1] s; *inconclusive* otherwise.
* **H-A2.** tau_half scales with lambda/v: tau_half(2.4 GHz)/tau_half(1 GHz) in [0.30, 0.55]
  (prediction 1/2.4 = 0.42).
  Primary geometry: A1, L_p = 20 dB, h = 2 m (h = 4 m reported as secondary, not decided).
  Ratio point estimate from the full-run tau_half values; 95 % CI from the Student-t interval
  of the 20 paired per-batch log10 ratios. *Supported* if the CI lies inside [0.30, 0.55];
  *not supported* if the CI does not intersect it; *inconclusive* otherwise.
* **H-A3 (SHOULD).** `ProbeBandit` beats `Urap` at every tau_SUI >= 0.3 s. Report the
  absolute dB.
  Primary geometry: A1, L_p = 20 dB, f_c = 1 GHz, h = 2 m (other A1 geometries reported).
  For each tau in {0.3, 1, 3, 10} s: Delta_tau = g(ProbeBandit) - g(Urap) in dB, paired per
  batch, Student-t 95 % CI. *Supported* if the lower bound of Delta_tau is > 0 at all four
  tau; *not supported* if the upper bound is < 0 at one or more tau; *inconclusive*
  otherwise. If `ProbeBandit` is cut (DESIGN.md 14.4 item 5), H-A3 is reported as not run.
* No multiplicity correction; every interval and every outcome (including null and
  negative) is reported.

## 5. Reporting

Absolute received power (dBm) and gains over `RisAbsent` (dB) with 95 % CIs for every
controller, tau_SUI and geometry; retention ratios only alongside absolute values. A0 is
presented as "under the area-tied kernel the value is X" (no statement about the letter's
Eq. (2)). All numbers enter the paper through pipeline-generated macros.

## 6. IQ consistency subset (VA-1/W5; a consistency check, not a finding)

1 GHz, h = 2 m, L_p = 20 dB, tau_SUI in {0.1, 1} s, {ExactOptimum, Urap}, 1000 s each,
10 kS/s tone; the controller runs in an external process in barrier mode and an external RX
application estimates power over 10 ms windows. Acceptance: |IQ - batch| <= 0.02 dB with
noise off (paired trajectory, same controller decisions); with noise on, within the 95 % CI.

## 7. Amendment 1 (10 Oct 2026, before the full E-A sweep was run)

Change: section 3's evaluation rule. Each piece of the 5 ms grid (split at every switch time)
is evaluated at its **midpoint** instead of its start (the left-point rule of
`sremu.engine.batch.run_batch`). Hypotheses, quantities and decision rules are unchanged.

Reason (found in the IQ-consistency smoke test, 20 s, ExactOptimum, tau_SUI = 0.1 s, noise
off): the left-point rule samples each piece that starts at a switch at the switch instant,
where a freshly optimized configuration is at its best, so it overestimates the time-averaged
power. Measured against the served IQ run: left point on the 5 ms grid +0.095 dB, left point
on a 1 ms grid +0.018 dB (bias proportional to the step), midpoint on the 5 ms grid
-0.002 dB. `run_batch` (left point, 5 ms) is still run and reported in the IQ consistency
subset.

Disclosure: before this amendment an end-to-end pipeline test of the sweep and aggregation
code was run on 200 s per point (20 batches of 2 s, i.e. 0.6 % of the pre-registered
duration; left-point rule; geometries A1 20 dB h = 2 m at 1 and 2.4 GHz) and its printed
tau_half, ratio and decisions were seen. That test is not a result and is not reported; the
pre-registered sweep is run in full after this amendment.
