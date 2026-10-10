"""The smooth level penalty in the plan's value calculation (engine_v2/value.py, engine-v2-level-penalty.md)."""

import math
from datetime import datetime, timedelta, timezone

import pytest

from pe_core.engine_v2 import value
from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.types import BatteryFacts, Limits, Segment, Spread

CAP = 18.0


@pytest.fixture
def on(monkeypatch):
    """The curves as the docs describe them: top from 90% (rate 10p/kWh/h at 100%, doubling every 2 points), bottom
    from 70% (rate 2p at 15%, doubling every 5)."""
    monkeypatch.setattr(value, "LEVEL_TOP_START_SOC", 90.0)
    monkeypatch.setattr(value, "LEVEL_TOP_RATE_AT_FULL_P", 10.0)
    monkeypatch.setattr(value, "LEVEL_TOP_DOUBLING_PTS", 2.0)
    monkeypatch.setattr(value, "LEVEL_BOT_START_SOC", 70.0)
    monkeypatch.setattr(value, "LEVEL_BOT_RATE_AT_FLOOR_P", 2.0)
    monkeypatch.setattr(value, "LEVEL_BOT_ANCHOR_SOC", 15.0)
    monkeypatch.setattr(value, "LEVEL_BOT_DOUBLING_PTS", 5.0)
    value._pen_cache.clear()
    yield
    value._pen_cache.clear()


def test_off_by_default():
    assert value._level_penalty(CAP) is None


def kwh(pct):
    return pct / 100 * CAP


def test_rate_is_zero_in_the_middle_and_hits_its_anchors(on):
    P = value._level_penalty(CAP)
    assert value._dphi(P, kwh(80)) == 0.0 and value._phi(P, kwh(80)) == 0.0
    assert value._dphi(P, kwh(90)) == pytest.approx(0.0, abs=1e-6)  # starts at zero: no kink in the cost
    assert value._dphi(P, kwh(100)) == pytest.approx(10.0, rel=0.01)  # the rate at 100% is the dial
    assert abs(value._dphi(P, kwh(15))) == pytest.approx(2.0, rel=0.02)  # and at the reserve for the bottom one
    assert value._dphi(P, kwh(70)) == pytest.approx(0.0, abs=1e-6)


def test_the_rate_doubles_every_d_points_beyond_the_start(on):
    P = value._level_penalty(CAP)
    # r(s) + A = A * 2^(x/d): so (r(96)+A)/(r(94)+A) = 2 for d = 2
    a = 10.0 / (2.0**5 - 1.0)
    assert (value._dphi(P, kwh(96)) + a) / (value._dphi(P, kwh(94)) + a) == pytest.approx(2.0, rel=0.01)


def test_cost_is_convex_and_its_slope_is_the_rate(on):
    P = value._level_penalty(CAP)
    h = 0.01
    for pct in (92, 95, 98, 40, 20):
        e = kwh(pct)
        slope = (value._phi(P, e + h) - value._phi(P, e - h)) / (2 * h)
        assert slope == pytest.approx(value._dphi(P, e), rel=0.03, abs=1e-3)
    assert value._dphi(P, kwh(40)) < 0 < value._dphi(P, kwh(95))  # the bottom curve falls as the level rises
    ladder = [value._phi(P, kwh(x)) for x in range(90, 101)]
    assert all(b > a for a, b in zip(ladder, ladder[1:], strict=False))
    assert all(d2 >= -1e-9 for d2 in [ladder[i + 2] - 2 * ladder[i + 1] + ladder[i] for i in range(len(ladder) - 2)])


def test_a_part_way_step_is_priced_from_the_level_it_reaches(on):
    P = value._level_penalty(CAP)
    dt, e0, ef, f = 0.5, kwh(90), kwh(96), 0.4
    exact = value._pen_partial(P, dt, e0, ef, f)
    # numerically: level climbs linearly for f*dt then holds
    n = 4000
    tot = 0.0
    for i in range(n):
        t = (i + 0.5) / n * dt
        e = e0 + (ef - e0) * min(1.0, t / (f * dt))
        tot += value._phi(P, e) * dt / n
    assert exact == pytest.approx(tot, rel=0.01)
    assert exact < f * value._phi(P, ef) * dt + (1 - f) * value._phi(P, ef) * dt  # less than "at the top all the time"


def seg(hours=0.5):
    t0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    return Segment(
        start=t0,
        end=t0 + timedelta(hours=hours),
        import_p=10.0,
        export_p=15.0,
        solar_kwh=Spread(0, 0, 0),
        load_kwh=Spread(0.3, 0.3, 0.3),
    )


def test_a_segment_prices_the_penalty_in_its_comfort_figure(on):
    facts = BatteryFacts()
    lim = Limits(
        allowed=frozenset({"self_use", "hold", "charge"}),
        forced=None,
        floor_soc=12.0,
        ceiling_soc=100.0,
        rule="normal",
        reason="",
    )
    S = value._make(seg(), facts, V2Settings(comfort_cost_p=0.0, top_up_cost_p=0.0), lim)
    assert S.pen is not None
    held_low = value._phys(S, kwh(80), "hold", 0.3, 10.0)
    held_high = value._phys(S, kwh(97), "hold", 0.3, 10.0)
    assert held_low[10] == 0.0 and held_high[10] > 0.0
    assert held_high[2] == pytest.approx(held_high[10])  # comfort = the penalty alone
    assert held_high[1] == pytest.approx(held_low[1] + held_high[10] + (0.0))  # same cash, plus the penalty


def test_off_leaves_the_physics_untouched():
    facts = BatteryFacts()
    lim = Limits(
        allowed=frozenset({"self_use", "hold", "charge"}),
        forced=None,
        floor_soc=12.0,
        ceiling_soc=100.0,
        rule="normal",
        reason="",
    )
    S = value._make(seg(), facts, V2Settings(), lim)
    assert S.pen is None
    res = value._phys(S, kwh(95), "charge", 0.3, 10.0)
    assert res[10] == 0.0 and len(res) == 11
    assert math.isfinite(res[1])


def test_the_slope_used_for_a_part_way_stop_matches_the_cost(on, monkeypatch):
    monkeypatch.setattr(value, "PEN_RES", 5000)  # a table finer than the steps of the check below
    value._pen_cache.clear()
    P = value._level_penalty(CAP)
    dt = 0.5
    for e0, d_e in ((kwh(90), kwh(8)), (kwh(30), kwh(10)), (kwh(60), -kwh(30)), (kwh(98), -kwh(6))):
        for f in (0.2, 0.5, 0.8):
            h = 1e-2
            num = (
                value._pen_partial(P, dt, e0, e0 + (f + h) * d_e, f + h)
                - value._pen_partial(P, dt, e0, e0 + (f - h) * d_e, f - h)
            ) / (2 * h)
            assert value._dpen_partial(P, dt, e0, d_e, f) == pytest.approx(num, rel=0.02, abs=0.02)
