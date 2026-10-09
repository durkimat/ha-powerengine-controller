"""Engine v2, layer 3 (value.solve, value_at, lines, segment_step): the value of stored energy, on synthetic days."""

import itertools
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from pe_core.adapters.base import ForecastPoint
from pe_core.engine_v2 import forecast as F
from pe_core.engine_v2 import value as V
from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.types import (
    CHARGE,
    EVENT,
    EXPORT,
    FREE,
    HOLD,
    SELF_USE,
    BatteryFacts,
    Forecast,
    Limits,
    Segment,
    Situation,
    Spread,
    StepInput,
)
from pe_core.forecast import LoadProfile
from pe_core.readings import Readings, Window

UTC = timezone.utc
T0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
FACTS = BatteryFacts()                      # 18 kWh, 95% each way, 4.8 kW, floor 12%
NO_COMFORT = V2Settings(late_events=False, comfort_cost_p=0.0, top_up_cost_p=0.0,
                        terminal_value="fixed", terminal_value_p=0.0, switch_cost_p=0.0)
BUY = 6.99 / 0.95


def t(i):
    return T0 + timedelta(minutes=30 * i)


def spread(x, lo=None, hi=None, w=(0.25, 0.5, 0.25)):
    return Spread(x if lo is None else lo, x, x if hi is None else hi, w)


def seg(i, imp=30.28, exp=15.0, solar=0.0, load=0.5, **kw):
    sp_s = solar if isinstance(solar, Spread) else spread(solar)
    sp_l = load if isinstance(load, Spread) else spread(load)
    return Segment(start=t(i), end=t(i + 1), import_p=imp, export_p=exp, solar_kwh=sp_s, load_kwh=sp_l, **kw)


def fc_of(segs):
    return Forecast(made_at=segs[0].start, segments=tuple(segs))


def limits_for(sg, arbitrage=False):
    allowed = {SELF_USE, HOLD, CHARGE} | ({EXPORT} if arbitrage else set())
    forced = EVENT if sg.event else FREE if sg.free else sg.manual
    return Limits(allowed=frozenset(allowed), forced=forced, floor_soc=12.0, ceiling_soc=100.0)


def solve(segs, soc=50.0, settings=NO_COMFORT, facts=FACTS, lim=limits_for, now=None):
    return V.solve(fc_of(segs), soc, facts, settings, lim, now or segs[0].start, "test", UTC)


def cheap_then_peak(n_cheap=20, n_peak=3, n_after=4, load=0.5, **kw):
    """Cheap import, then a dear stretch the house needs the battery for."""
    segs = [seg(i, imp=6.99, **kw) for i in range(n_cheap)]
    segs += [seg(n_cheap + i, imp=30.28, load=load) for i in range(n_peak + n_after)]
    return segs


# --- physics -------------------------------------------------------------------------------------------------------
def step(e, mode, sg=None, solar=0.0, load=0.5, imp=30.28, lim=None, settings=NO_COMFORT, facts=FACTS, **kw):
    sg = sg or seg(0, imp=imp)
    return V.segment_step(e, mode, sg, solar, load, imp, facts, settings, lim or limits_for(sg), **kw)


def test_hold_exports_a_surplus_and_self_use_stores_it():
    end, cost, fl = step(9.0, HOLD, solar=2.0, load=0.5)
    assert end == pytest.approx(9.0)
    assert fl["export_kwh"] == pytest.approx(1.5) and fl["import_kwh"] == 0
    assert cost == pytest.approx(-1.5 * 15.0)
    end, cost, fl = step(9.0, SELF_USE, solar=2.0, load=0.5)
    assert end == pytest.approx(9.0 + 1.5 * 0.95) and fl["export_kwh"] == pytest.approx(0)
    assert cost == pytest.approx(0.0)
    # a shortfall: Hold buys it, Self-use takes it from the battery
    end, cost, fl = step(9.0, HOLD, solar=0.0, load=0.5)
    assert end == 9.0 and cost == pytest.approx(0.5 * 30.28) and fl["import_kwh"] == pytest.approx(0.5)
    end, cost, fl = step(9.0, SELF_USE, solar=0.0, load=0.5)
    assert end == pytest.approx(9.0 - 0.5 / 0.95) and cost == 0 and fl["battery_to_house_kwh"] == pytest.approx(0.5)


def test_charge_takes_grid_energy_at_the_charge_limit_with_losses():
    end, cost, fl = step(5.0, CHARGE, load=0.5, imp=6.99)
    into = 4.8 * 0.5                               # kWh drawn into the battery at full power
    assert end == pytest.approx(5.0 + into * 0.95)
    assert fl["import_kwh"] == pytest.approx(0.5 + into) and cost == pytest.approx((0.5 + into) * 6.99)
    assert fl["grid_to_battery_kwh"] == pytest.approx(into)


def test_export_and_event_pay_what_they_pay():
    sg = seg(0, imp=30.28, event=True, event_p=115.0)
    end, cost, fl = step(9.0, EVENT, sg=sg, load=0.5)
    out = 4.8 * 0.5
    assert end == pytest.approx(9.0 - out / 0.95)
    assert fl["event_export_kwh"] == pytest.approx(out - 0.5)          # the house takes its half kWh first
    assert cost == pytest.approx(-(out - 0.5) * 115.0)
    end, cost, fl = step(9.0, EXPORT, load=0.5)
    assert cost == pytest.approx(-(out - 0.5) * 15.0) and end == pytest.approx(9.0 - out / 0.95)


def test_wear_counts_house_and_sales():
    st = replace(NO_COMFORT, wear_house_p=2.0, wear_sale_p=3.0)
    _, cost, _ = step(9.0, SELF_USE, settings=st, load=0.5)
    assert cost == pytest.approx(2.0 * 0.5)
    _, cost, fl = step(9.0, EXPORT, settings=st, load=0.5)
    out = 2.4
    assert cost == pytest.approx(-(out - 0.5) * 15.0 + 2.0 * 0.5 + 3.0 * (out - 0.5))


def test_physics_stays_between_floor_and_ceiling():
    lim = Limits(allowed=frozenset({SELF_USE, HOLD, CHARGE, EXPORT}), floor_soc=20.0, ceiling_soc=80.0)
    sg = seg(0)
    levels, modes = (0.0, 2.0, 3.6, 7.0, 14.4, 16.0, 18.0), (SELF_USE, HOLD, CHARGE, EXPORT)
    for e, mode, solar, load in itertools.product(levels, modes, (0.0, 3.0), (0.2, 3.0)):
        end, cost, fl = V.segment_step(e, mode, sg, solar, load, 30.28, FACTS, NO_COMFORT, lim)
        assert -1e-9 <= end <= 18.0 + 1e-9
        if mode in (EXPORT,) or (mode == SELF_USE and load > solar):
            assert end >= min(e, 0.2 * 18.0) - 1e-9                       # never discharged below the floor
        if mode == CHARGE:
            assert end <= max(e, 0.8 * 18.0) + 1e-9                        # never charged past the ceiling
        assert fl["import_kwh"] >= -1e-9 and fl["export_kwh"] >= -1e-9


def test_free_power_charges_to_full_regardless_of_the_ceiling():
    sg = seg(0, imp=0.0, free=True)
    lim = Limits(allowed=frozenset(), forced=FREE, ceiling_soc=80.0)
    end, cost, _ = V.segment_step(17.0, FREE, sg, 0.0, 0.2, 0.0, FACTS, NO_COMFORT, lim)
    assert end == pytest.approx(18.0) and cost == pytest.approx(0.0)


def test_taper_caps_slow_the_charge_and_fuse_headroom_limits_it():
    facts = replace(FACTS, taper=((90.0, 0.25),))
    end, _, fl = step(17.0, CHARGE, facts=facts, load=0.0, imp=7.0)
    assert fl["grid_to_battery_kwh"] == pytest.approx(4.8 * 0.25 * 0.5)
    lim = Limits(allowed=frozenset({CHARGE}), charge_cap_kw=1.0)
    _, _, fl = step(5.0, CHARGE, lim=lim, load=0.0)
    assert fl["grid_to_battery_kwh"] == pytest.approx(0.5)
    facts = replace(FACTS, bms_charge_kw=2.0)
    _, _, fl = step(5.0, CHARGE, facts=facts, load=0.0)
    assert fl["grid_to_battery_kwh"] == pytest.approx(1.0)
    facts = replace(FACTS, fuse_kw=5.0)                # house 4 kW leaves 1 kW for the battery
    _, _, fl = step(5.0, CHARGE, facts=facts, load=2.0)
    assert fl["grid_to_battery_kwh"] == pytest.approx(0.5)
    cold = seg(0, charge_factor=0.5)
    _, _, fl = V.segment_step(5.0, CHARGE, cold, 0.0, 0.0, 7.0, FACTS, NO_COMFORT, limits_for(cold))
    assert fl["grid_to_battery_kwh"] == pytest.approx(4.8 * 0.5 * 0.5)


def test_partial_charge_stops_at_the_end_level():
    end, cost, fl = step(5.0, CHARGE, load=0.0, imp=7.0, end_kwh=5.7)
    assert end == pytest.approx(5.7)
    assert fl["share_of_segment"] == pytest.approx(0.7 / (2.4 * 0.95))
    assert fl["grid_to_battery_kwh"] == pytest.approx(0.7 / 0.95)
    # export too, and an end level beyond what the segment can do is the whole segment
    end, _, _ = step(9.0, EXPORT, load=0.0, end_kwh=8.5)
    assert end == pytest.approx(8.5)
    end, _, _ = step(5.0, CHARGE, load=0.0, end_kwh=99.0)
    assert end == pytest.approx(5.0 + 2.4 * 0.95)


def test_comfort_cost_is_per_kwh_per_hour_outside_the_band():
    st = replace(NO_COMFORT, comfort_cost_p=0.3)
    sg = seg(0)
    lim = limits_for(sg)
    _, short, fl = V.segment_step(17.0, HOLD, sg, 0.0, 0.0, 30.28, FACTS, st, lim)     # 94.4%: 17 - 16.2 = 0.8 kWh over
    assert fl["comfort_p"] == pytest.approx(0.3 * 0.5 * 0.8)
    long = Segment(start=T0, end=T0 + timedelta(hours=8), import_p=30.28, export_p=15.0, solar_kwh=spread(0),
                   load_kwh=spread(0))
    _, long_cost, lf = V.segment_step(17.0, HOLD, long, 0.0, 0.0, 30.28, FACTS, st, lim)
    assert lf["comfort_p"] == pytest.approx(16 * fl["comfort_p"])                  # eight hours cost sixteen times
    _, _, inside = V.segment_step(10.0, HOLD, sg, 0.0, 0.0, 30.28, FACTS, st, lim)
    assert inside["comfort_p"] == 0.0
    _, _, low = V.segment_step(2.0, HOLD, sg, 0.0, 0.0, 30.28, FACTS, st, lim)      # 11%: 3.6 - 2.0 below the band
    assert low["comfort_p"] == pytest.approx(0.3 * 0.5 * 1.6)


# --- the value curve -----------------------------------------------------------------------------------------------
def test_cheap_night_before_a_dear_evening_gives_a_charge_target_where_the_curve_says():
    # five dear half-hours at 0.5 kWh need 2.5 kWh of house energy = 2.63 kWh stored, above the 12% floor
    segs = cheap_then_peak(n_cheap=20, n_peak=3, n_after=2)
    vr = solve(segs, soc=12.0)
    expected = (0.12 * 18 + 2.5 / 0.95) / 18 * 100                                  # 26.6%
    ln = V.lines(vr, segs[19].start, 12.0, 6.99, 15.0, FACTS, NO_COMFORT)
    assert ln.charge_target_soc == pytest.approx(expected, abs=1.0)
    assert ln.value_p == pytest.approx(30.28 * 0.95, abs=0.1)                       # the evening's price after losses
    assert ln.buy_line_p == pytest.approx(BUY) and ln.sell_line_p == pytest.approx(15 * 0.95)
    # above the target a stored kWh is worth nothing more (no value is put on what is left at the end): charging stops
    above = V.lines(vr, segs[19].start, expected + 3, 6.99, 15.0, FACTS, NO_COMFORT)
    assert above.charge_target_soc is None and above.value_p < 1.0
    # with the usual end-of-horizon value (a refill at the cheap price) the curve flattens at the buy line instead
    no_sale = segs[:-1] + [replace(segs[-1], export_p=0.0)]                      # nothing to sell at the end of it
    refill = solve(no_sale, soc=12.0, settings=replace(NO_COMFORT, terminal_value="refill"))
    flat = V.lines(refill, segs[19].start, expected + 3, 6.99, 15.0, FACTS, NO_COMFORT)
    assert flat.charge_target_soc is None and flat.value_p == pytest.approx(BUY, abs=0.1)
    # the timeline charges, and stops about there
    charge = next(it for it in vr.timeline if it.mode == CHARGE)
    assert charge.level_end == pytest.approx(expected, abs=1.0) and charge.until == f"until {charge.level_end:.0f}%"
    assert "6.99p" in charge.reason and "30.28p" in charge.reason and "stored kWh is worth" in charge.reason


def test_a_partial_charge_stops_between_grid_levels():
    segs = cheap_then_peak(n_cheap=1, n_peak=2, n_after=0)                           # 1.0 kWh of house to cover
    vr = solve(segs, soc=12.0)
    item = vr.timeline[0]
    assert item.mode == CHARGE
    full_at_full_power = 12.0 + 2.4 * 0.95 / 18 * 100                                # what a whole segment could add
    assert item.level_end < full_at_full_power - 1.0 and item.level_end > 12.0 + 1.0
    assert item.level_end == pytest.approx((0.12 * 18 + 1.0 / 0.95) / 18 * 100, abs=0.7)
    minutes = (item.end - item.start).total_seconds() / 60
    assert 0 < minutes < 30                                                          # charged for part of the half-hour
    assert vr.timeline[1].mode == HOLD and "target" in vr.timeline[1].reason


def test_a_sunny_day_ahead_lowers_the_night_charge():
    def day(sun):
        segs = [seg(i, imp=6.99) for i in range(10)]
        segs += [seg(10 + i, imp=30.28, solar=sun if 2 <= i <= 9 else 0.0) for i in range(30)]
        return segs

    dull, sunny = day(0.0), day(2.0)
    t_dull = V.lines(solve(dull, 12.0), dull[9].start, 12.0, 6.99, 15.0, FACTS, NO_COMFORT).charge_target_soc
    t_sunny = V.lines(solve(sunny, 12.0), sunny[9].start, 12.0, 6.99, 15.0, FACTS, NO_COMFORT).charge_target_soc
    assert t_dull == pytest.approx(100.0)                                            # the day needs all it can hold
    assert t_sunny is None or t_sunny < t_dull - 20


def test_a_grid_event_raises_the_value_before_it():
    base = [seg(i, imp=15.0) for i in range(30)]
    ev = [seg(i, imp=15.0) if not 20 <= i < 26 else seg(i, imp=15.0, event=True, event_p=115.0) for i in range(30)]
    before = t(10)
    plain, boosted = solve(base, 50.0), solve(ev, 50.0)
    v_plain, v_event = V.value_at(plain, before, 50.0), V.value_at(boosted, before, 50.0)
    # with time to buy more at 15p, a stored kWh is worth what it costs to put there: the buy line, not the event's pay
    assert v_plain == pytest.approx(15 * 0.95, abs=0.1)
    assert v_event == pytest.approx(15 / 0.95, abs=0.1) and v_event > v_plain + 1
    ln = V.lines(boosted, before, 30.0, 15.0, 15.0, FACTS, NO_COMFORT)
    assert ln.charge_target_soc is not None and ln.charge_now  # a run of one price: charge early, same cost
    assert not ln.charge_now or ln.run_target_soc == pytest.approx(ln.charge_target_soc)
    assert V.lines(boosted, before, 97.0, 15.0, 15.0, FACTS, NO_COMFORT).charge_now is False
    # where nothing more can be bought (no grid charging allowed) the value rises towards the event's pay
    no_charge = lambda s: Limits(allowed=frozenset({SELF_USE, HOLD}), forced=EVENT if s.event else None)   # noqa: E731
    stuck = solve(ev, 50.0, lim=no_charge)
    assert V.value_at(stuck, before, 50.0) == pytest.approx(115 * 0.95, abs=3)
    assert V.value_at(solve(base, 50.0, lim=no_charge), before, 50.0) < 20
    ev_item = next(it for it in boosted.timeline if it.mode == EVENT)
    assert ev_item.start == t(20) and ev_item.end == t(26) and "115p" in ev_item.reason
    assert any(it.until.startswith("until the") and "event at" in it.until for it in boosted.timeline)
    # a charge is worth it once the event is near and the time left to charge is short
    late = V.lines(boosted, t(19), 20.0, 15.0, 15.0, FACTS, NO_COMFORT)
    assert late.charge_target_soc is not None and late.value_p > late.buy_line_p


def day_segments(sun_spread=False, slot=False):
    segs = []
    for i in range(48):
        imp = 6.99 if (i < 10 or i >= 46) else 30.28
        sun = (Spread(0.0, 1.0, 1.8) if sun_spread else spread(1.0)) if 20 <= i <= 30 else spread(0.0)
        load = Spread(0.3, 0.5, 0.8) if sun_spread else spread(0.5)
        kw = {"slot_prob": 0.7, "slot_import_p": 6.99} if slot and i in (36, 37, 38) else {}
        segs.append(seg(i, imp=imp, solar=sun, load=load, **kw))
    return segs


def rises(vr, above_soc=12.0, tol=0.05):
    start = int(round(above_soc / 100 * (len(vr.lam[0]) - 1))) + 1       # above the floor: below it a kWh can't be used
    return [(k, i, b - a) for k, row in enumerate(vr.lam) for i, (a, b) in enumerate(zip(row[start:], row[start + 1:],
                                                                                          strict=False))
            if b - a > tol]


def mostly_falls(vr, share=0.02, worst=15.0):
    bumps = rises(vr, tol=0.5)
    return len(bumps) < share * len(vr.lam) * (len(vr.lam[0]) - 26) and max((x[2] for x in bumps), default=0) < worst


def test_lam_falls_with_level_in_normal_cases():
    nosun = [replace(sg, solar_kwh=spread(0.0)) for sg in day_segments()]
    assert rises(solve(nosun, 40.0, settings=NO_COMFORT), tol=0.05) == []
    # a sunny middle of the day (store or sell the sun) can make bumps; with the comfort band on, holding a kWh
    # later is cheaper, so the curve also steps around the band's edges
    # the curve still falls with level almost everywhere
    sunny = solve(day_segments(), 40.0, settings=NO_COMFORT)
    assert mostly_falls(sunny, share=0.08)


def test_lam_with_spreads_and_slots_falls_almost_everywhere():
    # the mode is chosen before the sun and the house are known, so the curve may rise a little here and there
    vr = solve(day_segments(sun_spread=True, slot=True), 40.0, settings=V2Settings())
    assert mostly_falls(vr, share=0.08)
    assert all(v == v and abs(v) < 500 for row in vr.lam for v in row)


def test_two_price_outcomes_are_not_a_blended_price():
    # the slot (7p instead of 30p) fills a gap the battery cannot cover: it is certain, uncertain or absent
    def build(p):
        segs = [seg(i, imp=30.28, load=0.0) for i in range(5)]
        kw = {"slot_prob": p, "slot_import_p": 6.99} if p is not None else {}
        segs.append(seg(5, imp=30.28, load=0.0, **kw))
        segs += [seg(6 + i, imp=30.28, load=0.5) for i in range(6)]
        return segs

    now = t(0)
    soc = (0.12 * 18 + 1.5) / 18 * 100                                               # 1.5 kWh above the floor
    lam = {}
    for p in (None, 0.7, 1.0):
        vr = solve(build(p), soc)
        lam[p] = V.value_at(vr, now, soc)
    assert lam[None] == pytest.approx(30.28 * 0.95, abs=0.1)
    assert lam[1.0] == pytest.approx(BUY, abs=0.1)
    two = 0.7 * lam[1.0] + 0.3 * lam[None]
    assert lam[0.7] == pytest.approx(two, abs=0.3)
    blended = 0.7 * 6.99 + 0.3 * 30.28                                               # what v1 would plan for
    assert lam[0.7] < blended / 0.95 - 0.4


def test_comfort_cost_lowers_the_value_near_the_top_and_raises_it_near_the_bottom():
    segs = [seg(i, imp=20.0, load=0.4) for i in range(30)]       # needs about 82%: the top is never used
    on = solve(segs, 50.0, settings=replace(NO_COMFORT, comfort_cost_p=0.3))
    off = solve(segs, 50.0, settings=NO_COMFORT)
    assert V.value_at(off, t(0), 97.0) == pytest.approx(0.0, abs=0.01)
    assert V.value_at(on, t(0), 97.0) < -0.3                       # a kWh held up there costs something
    assert V.value_at(on, t(0), 15.0) > V.value_at(off, t(0), 15.0) + 0.3
    assert V.value_at(on, t(0), 55.0) == pytest.approx(V.value_at(off, t(0), 55.0), abs=0.5)


def test_a_short_stay_above_the_band_is_not_paid_for():
    # the battery is needed full within the hour: 7 half-hours of 2.2 kWh need 16.2 kWh stored above the floor, so
    # the target is above the 90% comfort edge, and the comfort cost (paid only for the short stay) does not stop it
    segs = [seg(0, imp=6.99), seg(1, imp=6.99), seg(2, imp=6.99)]
    segs += [seg(3 + i, imp=30.28, load=2.2) for i in range(7)] + [seg(10, imp=30.28, load=0.1)]
    st = replace(NO_COMFORT, comfort_cost_p=0.3)
    vr = solve(segs, 60.0, settings=st)
    peak = max(it.level_end for it in vr.timeline if it.mode == CHARGE)
    assert peak > 94.0
    assert vr.comfort_given_up_p is not None and 0.0 <= vr.comfort_given_up_p < 5.0
    assert solve(segs, 60.0, settings=NO_COMFORT).comfort_given_up_p is None


def test_comfort_given_up_is_the_cash_difference():
    # a cheap, sunny stretch that would fill the battery above the band for a long time
    segs = [seg(i, imp=7.0, load=0.2) for i in range(30)] + [seg(30 + i, imp=30.0, load=1.0) for i in range(6)]
    st = replace(NO_COMFORT, comfort_cost_p=2.0, comfort_high_soc=70.0)
    vr = solve(segs, 40.0, settings=st)
    assert vr.comfort_given_up_p is not None and vr.comfort_given_up_p >= 0.0


def test_forced_modes_are_the_only_choice_and_manual_override_fixes_the_mode():
    segs = [seg(i, imp=6.99) for i in range(6)] + [seg(6, imp=30.28, manual=HOLD)] + \
        [seg(7 + i, imp=30.28) for i in range(5)]
    vr = solve(segs, 50.0)
    held = [it for it in vr.timeline if it.start <= t(6) < it.end]
    assert held and held[0].mode == HOLD and "override" in held[0].reason.lower()


def test_events_and_export_sales_with_arbitrage():
    # a dear export window with a cheap refill after it: the battery sells down to a floor and stops
    segs = [seg(i, imp=6.99, exp=5.0) for i in range(2)] + [seg(2 + i, imp=30.28, exp=30.0) for i in range(4)] + \
        [seg(6 + i, imp=6.99, exp=5.0) for i in range(8)]
    lim = lambda s: limits_for(s, arbitrage=True)                       # noqa: E731
    vr = solve(segs, 90.0, lim=lim)
    sells = [it for it in vr.timeline if it.mode == EXPORT]
    assert sells and "Sell at 30p" in sells[0].reason
    ln = V.lines(vr, t(2), 90.0, 30.28, 30.0, FACTS, NO_COMFORT)
    assert ln.sell_floor_soc is not None and ln.sell_floor_soc < 90.0


def test_value_at_and_lines_without_a_forecast():
    vr = solve([seg(0)], 50.0)
    assert vr.timeline and V.value_at(vr, t(0), 50.0) >= 0
    empty = V.solve(Forecast(made_at=t(0), segments=()), 50.0, FACTS, NO_COMFORT, limits_for, t(0), "none", UTC)
    assert empty.lam == () and V.value_at(empty, t(0), 50.0) == 0.0
    ln = V.lines(empty, t(0), 50.0, 30.0, 15.0, FACTS, NO_COMFORT)
    assert ln.charge_target_soc is None and ln.buy_line_p == pytest.approx(30.0 / 0.95)


def test_value_result_shapes():
    segs = [seg(i, imp=6.99 if i < 8 else 30.28) for i in range(24)]
    vr = solve(segs, 30.0)
    assert len(vr.lam) == len(segs) and all(len(r) == len(vr.lam[0]) for r in vr.lam)
    assert vr.step_kwh == pytest.approx(0.09) and len(vr.lam[0]) == 201     # 200 levels: whole percents on a level
    p = vr.path
    assert p["step_min"] == 15 and len(p["mid"]) == len(p["low"]) == len(p["high"])
    assert p["mid"][0] == pytest.approx(30.0, abs=0.1)
    assert len(p["mid"]) == 12 * 4 + 1
    assert vr.cost_expected_p <= vr.cost_selfuse_p + 1e-6
    assert vr.timeline[0].start == segs[0].start and vr.timeline[-1].end == segs[-1].end
    assert all(a.end == b.start for a, b in zip(vr.timeline, vr.timeline[1:], strict=False))
    assert all(a.mode != b.mode for a, b in zip(vr.timeline, vr.timeline[1:], strict=False))


def test_value_and_policy_read_the_end_of_the_segment():
    # value_at inside a segment is the curve at that segment's end (the next one's start)
    segs = [seg(i, imp=6.99) for i in range(3)] + [seg(3, imp=30.28), seg(4, imp=30.28), seg(5, imp=30.28)]
    vr = solve(segs, 12.0)
    for k in range(5):
        row = vr.lam[k + 1]
        idx = int(round(0.12 * 18 / vr.step_kwh))
        assert V.value_at(vr, t(k) + timedelta(minutes=10), 12.0) == pytest.approx(row[idx], abs=0.2)


# --- timing --------------------------------------------------------------------------------------------------------
def real_input(now, days=3):
    rates = []
    for i in range(48 * days):
        s = T0 + timedelta(minutes=30 * i)
        h = i % 48
        cheap = h >= 47 or h < 11
        rates.append(Window(s, s + timedelta(minutes=30), 0.0699 if cheap else 0.3028))
    r = Readings(now=now, battery_soc=40, import_rate=0.3028, export_rate=0.15, rates=rates)
    r.dispatches = [Window(now + timedelta(hours=9, minutes=12), now + timedelta(hours=10, minutes=47), 0.0)]
    r.axle_start, r.axle_end = now + timedelta(hours=26), now + timedelta(hours=26, minutes=30)
    prof = LoadProfile({(wk, h): 600.0 + (400 if 34 <= h <= 42 else 0) for wk in (False, True) for h in range(48)}, 10)
    pts = []
    for d in range(3):
        for h in range(48):
            k = max(0.0, 1.5 * (1 - abs(h - 24) / 10))
            pts.append(ForecastPoint(T0 + timedelta(days=d, minutes=30 * h), k, 0.5 * k, 1.3 * k))
    return StepInput(now=now, readings=r, facts=FACTS, situation=Situation(active=True), tz=UTC, solar_points=pts,
                     load_profile=prof, overnight=set(range(0, 11)) | {47}, slot_certainty=lambda a, b: 0.7)


def test_a_48_hour_solve_is_fast_enough():
    now = datetime(2026, 10, 5, 14, 10, tzinfo=UTC)
    inp = real_input(now)
    st = V2Settings()
    fc = F.build(inp, st)
    assert (fc.segments[-1].end - fc.segments[0].start) > timedelta(hours=47)
    started = time.perf_counter()
    vr = V.solve(fc, 40.0, FACTS, st, limits_for, now, "backstop", UTC)
    elapsed = time.perf_counter() - started
    assert elapsed < 6.0, f"48 h solve took {elapsed:.1f} s"
    assert vr.calc_s == pytest.approx(elapsed, abs=0.3)
    assert vr.timeline and vr.comfort_given_up_p is not None
    ln = V.lines(vr, now, 40.0, 30.28, 15.0, FACTS, st)
    assert ln.value_p > 0 and V.value_at(vr, now + timedelta(hours=30), 50.0) > 0


def test_forecast_and_value_work_together_on_the_real_pipeline():
    now = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)
    inp = real_input(now)
    st = V2Settings()
    fc = F.build(inp, st)
    vr = V.solve(fc, 30.0, FACTS, st, limits_for, now, "start", UTC)
    modes = {it.mode for it in vr.timeline}
    assert EVENT in modes and SELF_USE in modes
    ev = next(it for it in vr.timeline if it.mode == EVENT)
    assert ev.start == now + timedelta(hours=26)
    assert "event" in ev.until or "event" in ev.reason


# --- charging early within a stretch of one price ---------------------------------------------------------------------
def night_and_day(n_cheap=12, n_dear=20):
    return [seg(i, imp=6.99) for i in range(n_cheap)] + [seg(n_cheap + i, imp=28.84) for i in range(n_dear)]


def test_charge_in_a_flat_night_starts_with_the_night_and_ends_at_the_same_level():
    segs = night_and_day()
    vr = solve(segs, soc=12.0)
    need = (0.12 * 18 + 20 * 0.5 / 0.95) / 18 * 100                     # 70.5%
    first = vr.timeline[0]
    assert first.mode == CHARGE and first.start == t(0)                  # from the first half-hour, not the last
    assert first.level_end == pytest.approx(need, abs=1.0) and first.until == f"until {first.level_end:.0f}%"
    assert vr.timeline[1].mode == HOLD and first.end < t(6)                # done in the first hours of the night
    # the same total as the plain policy, which charges late
    core_rows = [V._end_row(vr.lam, k) for k in range(len(segs))]
    plain = V._walk([V._make(sg, FACTS, NO_COMFORT, limits_for(sg)) for sg in segs], core_rows, vr.step_kwh,
                    0.12 * 18, "mid", NO_COMFORT.price_band_p, front=False)
    late_end = max(r["e1"] for r in plain if r["k"] < 12) / 18 * 100
    assert late_end == pytest.approx(first.level_end, abs=0.7)
    assert next(r for r in plain if r["mode"] == CHARGE)["k"] > 3        # the late plan really does start later
    # live, five minutes into the night: charge now, up to that level
    ln = V.lines(vr, t(0) + timedelta(minutes=5), 12.0, 6.99, 15.0, FACTS, NO_COMFORT)
    assert ln.charge_now is True
    assert ln.run_target_soc == pytest.approx(first.level_end, abs=0.7)
    assert ln.charge_target_soc == pytest.approx(ln.run_target_soc)
    # at the target the run has nothing more to do; during the day it is not asked
    done = V.lines(vr, t(3), need, 6.99, 15.0, FACTS, NO_COMFORT)
    assert done.charge_now is False
    day = V.lines(vr, t(14), 40.0, 28.84, 15.0, FACTS, NO_COMFORT)
    assert day.run_target_soc is None and day.charge_now is False
    assert V.run_target(vr, t(14), 40.0, 6.99, FACTS, NO_COMFORT) is None   # price no longer the one asked about


def test_a_tie_never_charges_to_the_ceiling():
    # tomorrow needs little, and the end-of-horizon value equals the refill price: ties are not bought
    segs = night_and_day(n_cheap=12, n_dear=2)
    segs[-1] = replace(segs[-1], export_p=0.0)                          # nothing to sell at the end of the look-ahead
    st = replace(NO_COMFORT, terminal_value="refill")
    vr = solve(segs, soc=12.0, settings=st)
    need = (0.12 * 18 + 2 * 0.5 / 0.95) / 18 * 100                      # 17.9%
    top = max(it.level_end for it in vr.timeline)
    assert top < need + 2.0
    ln = V.lines(vr, t(0) + timedelta(minutes=5), 12.0, 6.99, 15.0, FACTS, st)
    assert ln.charge_target_soc is None or ln.charge_target_soc < need + 2.0
    assert ln.run_target_soc is None or ln.run_target_soc < need + 2.0


def test_run_target_is_cheap_and_does_not_resolve():
    vr = solve(night_and_day(), soc=12.0)
    started = time.perf_counter()
    for _ in range(50):
        V.run_target(vr, t(2), 20.0, 6.99, FACTS, NO_COMFORT)
    assert (time.perf_counter() - started) / 50 < 0.05


# --- the cost of changing mode ---------------------------------------------------------------------------------------
def test_switch_cost_is_the_setting_for_any_change_and_nothing_for_no_change():
    sc = V.switch_cost
    assert sc("none", "none", 0.5) == 0 and sc("charge", "charge", 0.5) == 0
    assert sc("none", "charge", 0.5) == 0.5 and sc("charge", "discharge", 0.5) == 0.5
    assert sc("hold", "charge", 0.5) == 0.5 and sc("charge", "hold", 0.5) == 0.5
    assert sc("none", "discharge", 0.0) == 0


def test_a_dearer_change_gives_a_plan_with_fewer_changes_and_the_cost_is_in_the_expected_cost():
    """A flat cheap day with export above the buy line: sell and refill is worth about 6p a kWh a cycle, and every
    half-hour can host one. Changes cost, so the plan makes fewer of them."""
    segs = [seg(i, imp=6.99, exp=15.0, load=0.3) for i in range(40)]
    lim = lambda s: limits_for(s, arbitrage=True)                       # noqa: E731
    free = solve(segs, 100.0, settings=replace(NO_COMFORT, switch_cost_p=0.0), lim=lim)
    dear = solve(segs, 100.0, settings=replace(NO_COMFORT, switch_cost_p=20.0), lim=lim)
    changes = lambda vr: sum(1 for a, b in zip(vr.timeline, vr.timeline[1:], strict=False) if a.mode != b.mode)  # noqa: E731
    assert changes(free) > changes(dear)
    assert dear.cost_expected_p > free.cost_expected_p                  # changes cost something


def test_a_solve_with_the_switch_cost_stays_inside_the_time_budget():
    segs = [seg(i, imp=6.99 if i % 8 < 5 else 30.28, exp=15.0, load=0.4) for i in range(96)]
    lim = lambda s: limits_for(s, arbitrage=True)                       # noqa: E731
    t0 = time.perf_counter()
    solve(segs, 50.0, settings=replace(NO_COMFORT, switch_cost_p=0.5), lim=lim)
    assert time.perf_counter() - t0 < 3.0


def test_the_programmes_choice_now_is_the_first_timeline_mode_and_it_is_not_made_for_a_forced_segment():
    segs = [seg(i, imp=6.99, exp=15.0, load=0.3) for i in range(12)]
    lim = lambda s: limits_for(s, arbitrage=True)                       # noqa: E731
    st = replace(NO_COMFORT, switch_cost_p=0.5)
    vr = solve(segs, 100.0, settings=st, lim=lim)
    mode, share = V.choice_now(vr, segs[0].start, 100.0, 6.99, FACTS, st, None)
    assert mode == vr.timeline[0].mode == EXPORT and 0 < share <= 1
    # from the mode already running, going on costs nothing and a change costs: never a worse choice than the first
    again = V.choice_now(vr, segs[0].start, 100.0, 6.99, FACTS, st, EXPORT)
    assert again[0] == EXPORT
    ev = [replace(segs[0], event=True, event_p=115.0)] + segs[1:]
    vr2 = solve(ev, 100.0, settings=st, lim=lambda s: limits_for(s, arbitrage=True))
    assert V.choice_now(vr2, ev[0].start, 100.0, 6.99, FACTS, st, None) is None
    bare = replace(vr, vk=())
    assert V.choice_now(bare, segs[0].start, 100.0, 6.99, FACTS, st, None) is None


# --- a grid event no one has announced yet (late_events) ---------------------------------------------------
LATE = replace(NO_COMFORT, late_events=True, late_events_per_week=7.0, late_event_hours=2.0)


def _flat_day(n=24):
    return [seg(i, imp=30.28, load=0.4) for i in range(n)]


def test_a_possible_late_event_makes_a_stored_kwh_worth_more():
    plain = solve(_flat_day(), 60.0, settings=NO_COMFORT)
    late = solve(_flat_day(), 60.0, settings=LATE)
    row_p, row_l = plain.lam[0], late.lam[0]
    assert sum(row_l[30:120]) > sum(row_p[30:120]) + 10                       # worth more across the working levels


def test_late_events_off_changes_nothing():
    for off in (replace(LATE, late_events=False), replace(LATE, late_events_per_week=0.0),
                replace(LATE, events=False)):
        a, b = solve(_flat_day(), 60.0, settings=NO_COMFORT), solve(_flat_day(), 60.0, settings=off)
        assert a.lam == b.lam and a.cost_expected_p == b.cost_expected_p


def test_a_late_event_is_not_expected_in_the_next_half_hour_or_in_a_known_event():
    segs = _flat_day(4)
    segs[2] = replace(segs[2], event=True, event_p=100.0)
    core = V._backward(fc_of(segs), FACTS, LATE, limits_for, segs[0].start, 0.09, 0.09)
    assert core.segs[0].late_p == 0.0 and core.segs[2].late_p == 0.0         # the first half hour; a known event
    assert core.segs[1].late_p > 0.0 and core.segs[3].late_p > 0.0


def test_the_value_curve_with_late_events_still_falls_with_the_level():
    vr = solve(_flat_day(48), 40.0, settings=LATE)
    assert mostly_falls(vr, share=0.05, worst=5.0)


def daily(end_hh, load=0.3, cheap_hh=(range(0, 12), range(44, 48))):
    """`end_hh` half-hours from midnight: cheap 22:00 to 06:00 (6.99p), dear 06:00 to 22:00 (28.84p), no sun."""
    cheap = {i for r in cheap_hh for i in r}
    return [seg(i, imp=6.99 if i % 48 in cheap else 28.84, load=load) for i in range(end_hh)]


def _end_levels(vr, last=4):
    return [it.level_end for it in vr.timeline[-last:]]


def test_a_look_ahead_ending_in_the_cheap_window_keeps_what_the_dear_stretch_needs():
    # 8 Oct 2026: the look-ahead ended inside the cheap window and the last steps sold the battery to the floor, because
    # what was left was valued at the cheap refill price though only an hour of the window was left to refill in.
    # The 48 h look-ahead below ends at 05:00: one cheap hour is left (4.6 kWh refillable) and the 06:00 to 22:00
    # house load (9.6 kWh, 10.1 stored) must be there at 06:00, so about 12.8 - 4.6 = 8.2 kWh (45%) is kept.
    st = replace(NO_COMFORT, terminal_value="refill", reserve_soc=15.0)
    ar = lambda sg: limits_for(sg, arbitrage=True)                                    # noqa: E731
    vr = solve(daily(96 + 10), soc=70.0, settings=st, lim=ar)
    assert min(_end_levels(vr)) > 40.0
    tv, curve = V._terminal_curve(fc_of(daily(96 + 10)), FACTS, st, 0.18, 100)
    knee = next(k for k in range(1, 101) if curve[k - 1] - curve[k] < curve[0] - curve[1] - 1e-9)
    assert knee * 0.18 / 18 * 100 == pytest.approx(45.0, abs=2.0)
    assert tv == pytest.approx(6.99 / FACTS.eta_charge)              # above the knee: bought back at the cheap price
    # control: without the knee (the flat refill value) the same plan does dump to the floor
    flat = lambda fc, facts, settings, step, n: (6.99 / facts.eta_charge,        # noqa: E731
                                                [-6.99 / facts.eta_charge * i * step for i in range(n + 1)])
    orig, V._terminal_curve = V._terminal_curve, flat
    try:
        dumped = solve(daily(96 + 10), soc=70.0, settings=st, lim=ar)
    finally:
        V._terminal_curve = orig
    assert min(_end_levels(dumped)) < 20.0


def test_a_look_ahead_ending_in_the_cheap_window_with_time_to_refill_may_sell_down():
    # three hours of the window left refill 13.7 kWh, more than the dear stretch needs: selling down is right
    st = replace(NO_COMFORT, terminal_value="refill", reserve_soc=15.0)
    tv, curve = V._terminal_curve(fc_of(daily(96 + 6)), FACTS, st, 0.18, 100)
    refill_step = -6.99 / FACTS.eta_charge * 0.18
    assert curve[1] - curve[0] == pytest.approx(curve[100] - curve[99]) == pytest.approx(refill_step)


def test_a_look_ahead_ending_in_the_peak_does_not_dump_the_battery_at_its_end():
    # 7 Oct 2026 (0.9.119): the look-ahead ended 30 min into the 28.84p peak and the last step sold 25% down to the
    # floor. Nothing is refillable until 22:00, so the day's remaining house load (31 half hours, 9.3 kWh, 9.8 stored)
    # plus the reserve, about 12.5 kWh (69%), is worth the peak price and the plan keeps it.
    st = replace(NO_COMFORT, terminal_value="refill", reserve_soc=15.0)
    ar = lambda sg: limits_for(sg, arbitrage=True)                                    # noqa: E731
    vr = solve(daily(96 + 13), soc=70.0, settings=st, lim=ar)                         # ends 06:30
    assert min(_end_levels(vr)) > 60.0
    tv, curve = V._terminal_curve(fc_of(daily(96 + 13)), FACTS, st, 0.18, 100)
    assert tv == pytest.approx(max(6.99 / FACTS.eta_charge, 15.0 * FACTS.eta_discharge))   # the surplus can be sold


def test_the_value_of_energy_left_never_rises_with_the_level_and_a_flat_tariff_keeps_the_refill_price():
    st = replace(NO_COMFORT, terminal_value="refill", reserve_soc=15.0)
    for end in (96 + 5, 96 + 13, 96 + 30):
        tv, curve = V._terminal_curve(fc_of(daily(end)), FACTS, st, 0.18, 100)
        slopes = [curve[k] - curve[k + 1] for k in range(100)]
        assert all(a >= b - 1e-9 for a, b in zip(slopes, slopes[1:], strict=False)), end
    flat_tariff = [seg(i, imp=28.84, load=0.3) for i in range(96)]
    tv, curve = V._terminal_curve(fc_of(flat_tariff), FACTS, st, 0.18, 100)
    assert tv == pytest.approx(28.84 / FACTS.eta_charge) and curve[50] == pytest.approx(-tv * 50 * 0.18)
    fixed = replace(NO_COMFORT, terminal_value="fixed", terminal_value_p=12.0)
    got = V._terminal_curve(fc_of(daily(96 + 5)), FACTS, fixed, 0.18, 10)
    assert got == (12.0, [-12.0 * i * 0.18 for i in range(11)])


# --- the plan's first step follows a sale that is running (8 Oct 2026) ------------------------------------
RUNNING_SETTINGS = V2Settings(top_up_cost_p=4.0, reserve_soc=15.0)          # the owner's: reversal 3p, comfort band


def _cheap_night_then_dear():
    return [seg(i, imp=6.66 if i < 13 else 28.84, exp=15.0, load=0.3) for i in range(40)]


def _solve_running(segs, running):
    lim = lambda s: limits_for(s, arbitrage=True)                       # noqa: E731
    return V.solve(fc_of(segs), 63.0, FACTS, RUNNING_SETTINGS, lim, segs[0].start, "test", UTC, running=running)


def test_a_sale_that_is_running_is_not_turned_round_by_the_stretch_front_charge():
    # 8 Oct 2026, 21:30: selling from the battery at 15p, import 6.66p all night, 63%. The plan's first step was "charge
    # now": the stretch's charge is moved to its start, but the programme itself, coming from the sale, goes on selling
    # (choice_now), so the executor never followed the plan and every revaluation opened with the other direction.
    segs = _cheap_night_then_dear()
    assert _solve_running(segs, None).timeline[0].mode == CHARGE         # nothing running: the front charge stands
    vr = _solve_running(segs, EXPORT)
    assert vr.timeline[0].mode == EXPORT                                 # the sale goes on, as the executor will
    assert V.choice_now(vr, segs[0].start, 63.0, 6.66, FACTS, RUNNING_SETTINGS, EXPORT)[0] == vr.timeline[0].mode
    assert any(it.mode == CHARGE for it in vr.timeline)                  # and the night is still refilled


def test_only_a_running_sale_changes_the_plan_other_modes_keep_the_front_charge():
    segs = _cheap_night_then_dear()
    base = [(i.mode, i.level_end) for i in _solve_running(segs, None).timeline]
    for run in (CHARGE, HOLD, SELF_USE):
        assert [(i.mode, i.level_end) for i in _solve_running(segs, run).timeline] == base


def test_a_long_cheap_window_with_arbitrage_is_not_broken_up_by_holds():
    """Charge and sale both pay all night (6.66p in, 15p out). Resting in Hold between a charge and a sale used to
    cost less than turning round directly (0.6p against 3p), so the plan rested for a whole half-hour wherever the
    gain was marginal (8 Oct 2026). Every change now costs the same, so a hold is never a way round the cost."""
    facts = BatteryFacts(capacity_kwh=18, eta_charge=0.895, eta_discharge=0.943, max_charge_kw=4.78,
                         max_discharge_kw=4.95)
    segs = [seg(i, imp=6.66, exp=15.0, load=0.4) for i in range(23)] + [seg(23 + i, imp=28.84, load=0.5)
                                                                         for i in range(26)]
    lim = lambda s: limits_for(s, arbitrage=True)                       # noqa: E731
    vr = solve(segs, 50.0, settings=V2Settings(), facts=facts, lim=lim)
    window_end = segs[22].end
    long_holds = [it for it in vr.timeline if it.mode == HOLD and it.start < window_end
                  and (it.end - it.start) >= timedelta(minutes=15)]
    assert long_holds == []
