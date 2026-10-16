"""The cost-to-go of every segment is made convex in the level (`value.CONVEX_COST_TO_GO`).

A segment is one mode (a charge or a sale may stop part-way and rest), so the cost of the energy left in the battery has
gaps where no mix of modes reaches a level. Each gap is a concave kink; the segment before copies it back and the kinks
build into a comb, which showed as the value of a stored kWh going up and down with the level (the sawtooth of
October 2026). Over a half-hour the controller can share time between modes, and the lower convex envelope is what
that costs. The value of a stored kWh, the slope, then falls as the level rises, and it no longer depends on the
level step."""
import math

import pytest

from pe_core.engine_v2 import value as V
from pe_core.engine_v2.settings import V2Settings
from test_engine_v2_value import cheap_then_peak, solve


def test_the_envelope_fills_a_gap_with_a_straight_line_and_never_rises_above_the_points():
    v = [10.0, 9.0, 8.5, 8.4, 3.0, 2.0, 1.0, 0.5]                   # a kink at index 3-4: not convex
    out = V._convex_from(v, 0)
    assert all(o <= x + 1e-12 for o, x in zip(out, v, strict=True))
    slopes = [b - a for a, b in zip(out, out[1:], strict=False)]
    assert all(b >= a - 1e-12 for a, b in zip(slopes, slopes[1:], strict=False))     # slope never falls: convex
    assert out[0] == v[0] and out[-1] == v[-1]


def test_a_convex_row_is_unchanged_and_levels_under_the_floor_are_left_alone():
    v = [50.0, 40.0, 31.0, 23.0, 16.0, 10.0, 5.0, 1.0]
    assert V._convex_from(v, 0) == pytest.approx(v)
    odd = [99.0, 0.0, 7.0, 9.0, 8.0, 3.0, 2.0, 1.5]                  # the first two entries are unreachable
    out = V._convex_from(odd, 2)
    assert out[:2] == odd[:2]
    assert out[2] == odd[2] and out[-1] == odd[-1]


def _rises(vr, floor_pct=12.0):
    """The total rise of the value of a stored kWh with the level, over the reachable levels, summed over the curves."""
    total = 0.0
    for row in vr.lam:
        lo = math.ceil(floor_pct / 100 * (len(row) - 1))
        total += sum(max(0.0, b - a) for a, b in zip(row[lo:], row[lo + 1:], strict=False))
    return total


def test_the_value_of_a_stored_kwh_never_rises_with_the_level(monkeypatch):
    segs = cheap_then_peak(20, 4, 4)
    monkeypatch.setattr(V, "CONVEX_COST_TO_GO", False)
    before = _rises(solve(segs, 40.0, settings=V2Settings()))
    monkeypatch.setattr(V, "CONVEX_COST_TO_GO", True)
    after = _rises(solve(segs, 40.0, settings=V2Settings()))
    assert before > 1.0                                              # it was a comb
    assert after < 1e-6


def test_the_plan_cost_does_not_depend_on_the_level_step(monkeypatch):
    segs = cheap_then_peak(20, 4, 4)
    cost = {}
    for step in (0.1, 0.05):
        st = V2Settings(level_step_kwh=step)
        cost[step] = solve(segs, 40.0, settings=st).cost_expected_p
    assert cost[0.1] == pytest.approx(cost[0.05], rel=0.03)
