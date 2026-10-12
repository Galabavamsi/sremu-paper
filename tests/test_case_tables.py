"""Closed-form E-B2 timing of tools/case_tables.py (Section VII-A, Appendix F).

The probabilities that Section VII-A compares with the measured PER: exact rational values for
asap and timed commands, the J = 0 formula and a numerical check of the tick-based integral,
and the uniform-tick-phase premise.
"""

from __future__ import annotations

from fractions import Fraction as F

import pytest
from case_tables import (
    eb2_closed_form,
    p_latency_exceeds,
    p_tick_exceeds,
    tick_phase_grid,
)

pytestmark = pytest.mark.unit

LAT0 = F(2, 10**4)  # 0.2 ms
T_PRE = F(256, 10**6)  # 64 symbols x 4 samples at 1 MS/s
BODY = F(2304, 10**6)
TICK = F(2, 10**3)


def test_asap_and_timed_closed_form_values() -> None:
    # asap, guard 0.5 ms, J = 1 ms: P(0.2 ms + U ms > 0.756 ms) = 0.444
    assert eb2_closed_form("asap", F(1, 2000), F(1, 1000), None, LAT0, T_PRE, TICK) == F(111, 250)
    # timed, lead 2 ms, guard 0.5 ms, J = 3 ms: (3.2 - 2.506)/3
    got = eb2_closed_form("timed", F(1, 2000), F(3, 1000), F(1, 500), LAT0, T_PRE, TICK)
    assert got == F(694, 3000)
    # on time: lead above the worst latency exposes nothing
    assert eb2_closed_form("timed", F(1, 2000), F(1, 1000), F(1, 500), LAT0, T_PRE, TICK) == 0
    # no jitter: a step function of the fixed latency
    assert p_latency_exceeds(F(1, 10**4), LAT0, F(0)) == 1
    assert p_latency_exceeds(F(3, 10**4), LAT0, F(0)) == 0


@pytest.mark.parametrize("guard", [F(1, 10**4), F(1, 2000), F(1, 1000)])
def test_tick_based_without_jitter_is_one_minus_ratio(guard: F) -> None:
    want = 1 - (guard + T_PRE - LAT0) / TICK
    assert eb2_closed_form("tick-based", guard, F(0), None, LAT0, T_PRE, TICK) == want


@pytest.mark.parametrize(("guard", "jit"), [(F(1, 2000), F(3, 10**4)), (F(1, 10**4), F(1, 1000)),
                                            (F(1, 1000), F(3, 1000))])
def test_tick_based_integral_matches_quadrature(guard: F, jit: F) -> None:
    x = guard + T_PRE
    n = 4000  # midpoint rule over U; the integrand is piecewise linear and bounded
    approx = sum(min(1.0, max(0.0, float((TICK - (x - LAT0 - jit * F(2 * k + 1, 2 * n)))
                                         / TICK))) for k in range(n)) / n
    assert float(p_tick_exceeds(x, LAT0, jit, TICK)) == pytest.approx(approx, abs=1e-6)


def test_tick_phases_of_the_guard_starts_are_uniform() -> None:
    assert tick_phase_grid(F(1, 2000), BODY, TICK, 2000) == F(4, 10**6)
    assert tick_phase_grid(F(1, 1000), BODY, TICK, 2000) == F(8, 10**6)
    with pytest.raises(SystemExit):  # 2001 packets visit one phase once more
        tick_phase_grid(F(1, 2000), BODY, TICK, 2001)
