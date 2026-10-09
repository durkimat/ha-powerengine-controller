"""Engine v2, change after the first live evening (docs/plans/engine-v2.md section 18a): the price of grid charging
above the comfort band's top, the cost of reversing, a leg running to its step's end, and "learned" revaluing only when
it matters. The closed-loop check of the evening is in test_engine_v2_evening.py."""

from dataclasses import replace
from datetime import timedelta

import pytest
from test_engine_v2_execute import EXPORTING, _timeline, limits, lines, s0, step
from test_engine_v2_observe import T0, forecast, segment, value_result
from test_engine_v2_value import FACTS, NO_COMFORT, cheap_then_peak, limits_for, seg, solve
from test_engine_v2_value import step as vstep

from pe_core.engine_v2 import triggers, value
from pe_core.engine_v2.execute import Executor
from pe_core.engine_v2.settings import SECTIONS, SETTINGS, SettingsError, V2Settings, catalogue, parse_v2
from pe_core.engine_v2.types import CHARGE, EXPORT, HOLD, TimelineItem
from pe_core.engine_v2.value import CHARGE_K, DISCHARGE_K, HOLD_K, NONE_K, switch_cost

TOP = replace(NO_COMFORT, top_up_cost_p=2.0)             # 90% top, 2p per kWh charged above it, no other cost


# ---- 1. the top-up price ------------------------------------------------------------------------
def test_the_settings_exist_in_the_catalogue_and_their_sections():
    cat = catalogue()
    rows = {r["key"]: r for r in cat["settings"]}
    assert rows["top_up_cost_p"]["default"] == 5.0 and (rows["top_up_cost_p"]["min"], rows["top_up_cost_p"]["max"]) \
        == (0, 20)
    assert rows["top_up_cost_p"]["unit"] == "p/kWh" and rows["top_up_cost_p"]["label"] == \
        "Grid charging above the comfort band"
    assert "reversal_cost_p" not in rows and rows["switch_cost_p"]["default"] == 2.0
    sections = {s["key"]: s["keys"] for s in cat["sections"]}
    assert "top_up_cost_p" in sections["comfort"] and "switch_cost_p" in sections["response"]
    assert cat["values"]["top_up_cost_p"] == 5.0 and cat["values"]["switch_cost_p"] == 2.0
    listed = [k for _, _, keys in SECTIONS for k in keys]
    assert sorted(listed) == sorted(SETTINGS) and len(listed) == len(set(listed))     # every setting in one section


def test_the_comfort_top_is_seeded_from_v1s_arbitrage_top_until_saved():
    assert parse_v2({}, safety={"arbitrage_max_soc": 85}).comfort_high_soc == 85
    assert parse_v2({"comfort_high_soc": 92}, safety={"arbitrage_max_soc": 85}).comfort_high_soc == 92
    assert parse_v2({}, safety={}).comfort_high_soc == 90
    with pytest.raises(SettingsError):                                 # the band must stay valid (low below high)
        parse_v2({}, safety={"arbitrage_max_soc": 10})


def test_a_charge_pays_the_top_up_only_on_the_grid_energy_above_the_top():
    cap = FACTS.capacity_kwh
    # from 85%: a half-hour at 4.8 kW stores 2.28 kWh (12.7 points), 7.7 of them above 90%
    e0 = 0.85 * cap
    end, cost, fl = vstep(e0, CHARGE, settings=TOP, load=0.0, imp=6.99)
    above = end - 0.9 * cap
    assert above == pytest.approx(0.0767 * cap, rel=0.01)
    assert fl["comfort_p"] == pytest.approx(2.0 * above / 0.95)             # grid kWh drawn for the part above
    assert cost == pytest.approx(fl["cash_p"] + fl["comfort_p"])
    # entirely below the top: nothing; entirely above: every kWh drawn
    assert vstep(0.6 * cap, CHARGE, settings=TOP, load=0.0, imp=6.99)[2]["comfort_p"] == 0.0
    e_hi = 0.92 * cap
    end, _, fl = vstep(e_hi, CHARGE, settings=TOP, load=0.0, imp=6.99)
    assert fl["comfort_p"] == pytest.approx(2.0 * (end - e_hi) / 0.95)
    # no top-up when the price is 0, on a sale, on a hold
    assert vstep(e0, CHARGE, settings=NO_COMFORT, load=0.0, imp=6.99)[2]["comfort_p"] == 0.0
    for mode in (HOLD, EXPORT):
        assert vstep(0.95 * cap, mode, settings=TOP, load=0.0)[2]["comfort_p"] == 0.0


def test_the_top_up_is_not_charged_on_sun():
    cap = FACTS.capacity_kwh
    e0 = 0.91 * cap
    _, _, plain = vstep(e0, CHARGE, settings=TOP, load=0.0, imp=6.99)
    _, _, sunny = vstep(e0, CHARGE, settings=TOP, solar=1.0, load=0.0, imp=6.99)
    share = sunny["grid_to_battery_kwh"] / plain["grid_to_battery_kwh"]
    assert 0.3 < share < 0.6                                           # 1 kWh of what goes in is sun
    assert sunny["comfort_p"] == pytest.approx(plain["comfort_p"] * share, rel=0.01)


def test_a_charge_that_stops_at_the_top_pays_no_top_up():
    cap = FACTS.capacity_kwh
    e0 = 0.85 * cap
    _, _, fl = vstep(e0, CHARGE, settings=TOP, load=0.0, imp=6.99, end_kwh=0.9 * cap)
    assert fl["comfort_p"] == pytest.approx(0.0, abs=1e-9) and fl["share_of_segment"] < 1.0
    _, _, half = vstep(e0, CHARGE, settings=TOP, load=0.0, imp=6.99, end_kwh=0.95 * cap)
    assert half["comfort_p"] == pytest.approx(2.0 * 0.05 * cap / 0.95, rel=0.01)    # priced from where it ends


def test_the_top_up_keeps_a_charge_at_the_top_unless_it_saves_more_than_it_costs():
    # a cheap stretch, then a dear one that needs more than the battery holds: every kWh is used
    segs = [seg(i, imp=6.99) for i in range(8)] + [seg(8 + i, imp=20.0, load=2.2) for i in range(8)]

    def peak(settings, soc=30.0):
        vr = solve(segs, soc, settings=settings)
        return max((it.level_end for it in vr.timeline if it.mode == CHARGE), default=soc)

    assert peak(NO_COMFORT) > 99                                        # nothing against filling up
    assert peak(TOP) > 99                                               # 2p a kWh: the last fill still saves ~11p
    assert peak(replace(NO_COMFORT, top_up_cost_p=20.0)) <= 90.5        # 20p a kWh up there costs more than it saves


def test_the_top_up_is_part_of_the_comfort_figure():
    segs = cheap_then_peak(n_cheap=6, n_peak=3, n_after=2)
    vr = solve(segs, 60.0, settings=replace(NO_COMFORT, top_up_cost_p=2.0))
    assert vr.comfort_given_up_p is not None and vr.comfort_given_up_p >= 0.0
    assert solve(segs, 60.0, settings=NO_COMFORT).comfort_given_up_p is None


def test_a_buy_line_above_the_top_includes_the_top_up():
    segs = [seg(i, imp=6.99) for i in range(6)] + [seg(6 + i, imp=30.28, load=1.2) for i in range(6)]
    vr = solve(segs, 85.0, settings=TOP, lim=limits_for)
    at = segs[0].start
    low = value.lines(vr, at, 60.0, 6.99, 15.0, FACTS, TOP)
    high = value.lines(vr, at, 92.0, 6.99, 15.0, FACTS, TOP)
    assert low.buy_line_p == pytest.approx(6.99 / 0.95)
    assert high.buy_line_p == pytest.approx((6.99 + 2.0) / 0.95)


# ---- 2. the cost of changing mode -----------------------------------------------------------------------------------
def test_every_change_costs_the_same_and_staying_costs_nothing():
    for a in (NONE_K, HOLD_K, CHARGE_K, DISCHARGE_K):
        for b in (NONE_K, HOLD_K, CHARGE_K, DISCHARGE_K):
            assert switch_cost(a, b, 2.0) == (0.0 if a == b else 2.0)
    assert switch_cost(CHARGE_K, DISCHARGE_K, 0.0) == 0.0


def test_a_detour_through_hold_never_costs_less_than_the_direct_change():
    kinds = (NONE_K, HOLD_K, CHARGE_K, DISCHARGE_K)
    for a in kinds:
        for b in kinds:
            for c in kinds:
                assert switch_cost(a, c, 1.5) <= switch_cost(a, b, 1.5) + switch_cost(b, c, 1.5)


def test_a_saved_reversal_setting_from_an_earlier_release_is_ignored_not_an_error():
    from pe_core.engine_v2.settings import parse_v2
    s = parse_v2({"reversal_cost_p": 0.6, "top_up_cost_p": 4})
    assert s.switch_cost_p == 2.0 and s.top_up_cost_p == 4


def test_the_executor_counts_a_change_at_its_own_price():
    obs = type("Obs", (), {"net_load_kw": 0.5})()
    ln = lines(11.0, floor=0.0)                                         # 3.25p under the sale line
    near = lines(14.2, floor=68.0)                                      # 0.05p under it, and only 2 points to sell
    lim, lim_near = limits(EXPORTING), limits(EXPORTING, floor=68.0)
    ex = Executor(V2Settings(min_dwell_s=0, switch_cost_p=2.0))
    big = ex._pick_for(EXPORT, lim, ln, 70.0, FACTS, None)
    small = ex._pick_for(EXPORT, lim_near, near, 70.0, FACTS, None)
    assert ex._worth_the_change(CHARGE, big, lim, ln, 70.0, FACTS, obs)             # 41p against 2 x 2p
    assert not ex._worth_the_change(CHARGE, small, lim_near, near, 70.0, FACTS, obs)
    assert ex._worth_the_change(HOLD, small, lim_near, near, 70.0, FACTS, obs) is False     # 2 x 2p still too dear
    cheap = Executor(V2Settings(min_dwell_s=0, switch_cost_p=0.0))
    assert cheap._worth_the_change(CHARGE, small, lim_near, near, 70.0, FACTS, obs)


def test_the_programme_adds_the_change_cost_to_a_turn_from_a_charge_to_a_sale():
    cands = [(10.0, CHARGE, 1.0), (9.0, EXPORT, 1.0)]
    assert value._pick(cands, CHARGE_K, 2.0) == pytest.approx(10.0)                # 9 + 2 loses to staying at 10
    assert value._pick(cands, CHARGE_K, 0.5) == pytest.approx(9.5)                 # a small cost lets it turn



# ---- 3. a leg runs to its step's end --------------------------------------------------------------
BOTH = dict(target=100.0, floor=0.0)


def _running(ex, mode_value_ln, lim=None):
    step(ex, 0, level=70, ln=mode_value_ln, lim=lim or limits(EXPORTING))
    return ex


def test_a_charge_in_progress_is_not_turned_into_a_sale_when_the_plan_flips_mid_leg():
    ex = Executor(s0())
    ms, *_ = step(ex, 0, level=70, ln=lines(30.0, target=100.0), lim=limits(EXPORTING))
    assert ms.mode == CHARGE
    both = lines(10.0, **BOTH)                                         # now buying and selling both pay
    vr = value_result(T0, forecast(T0, [segment(T0)]), timeline=_timeline(EXPORT))      # a revaluation says: sell
    for sec in (10, 20, 30, 40):
        ms, *_ = step(ex, sec, level=70 + sec / 10, ln=both, lim=limits(EXPORTING), vr=vr)
        assert ms.mode == CHARGE, sec
    # the same moment from idle: the plan's sale starts
    ms, *_ = step(Executor(s0()), 0, level=70, ln=both, lim=limits(EXPORTING), vr=vr)
    assert ms.mode == EXPORT


def test_a_sale_in_progress_is_not_turned_into_a_charge_when_the_plan_flips_mid_leg():
    ex = Executor(s0())
    ms, *_ = step(ex, 0, level=70, ln=lines(8.0, floor=40.0), lim=limits(EXPORTING))
    assert ms.mode == EXPORT
    both = lines(10.0, target=100.0, floor=40.0)
    vr = value_result(T0, forecast(T0, [segment(T0)]), timeline=_timeline(CHARGE))
    for sec in (10, 20, 30):
        ms, *_ = step(ex, sec, level=70 - sec / 10, ln=both, lim=limits(EXPORTING), vr=vr)
        assert ms.mode == EXPORT, sec


def test_the_leg_ends_when_it_no_longer_pays_at_all():
    ex = Executor(s0())
    step(ex, 0, level=70, ln=lines(30.0, target=100.0), lim=limits(EXPORTING))
    vr = value_result(T0, forecast(T0, [segment(T0)]), timeline=_timeline(EXPORT))
    # the value rose above the sale line: selling no longer pays, and the buy line is long passed: the charge goes on
    ms, *_ = step(ex, 10, level=70, ln=lines(30.0, target=100.0, floor=None), lim=limits(EXPORTING), vr=vr)
    assert ms.mode == CHARGE
    # the lines' target is reached: it must end (a sale pays, so the sale comes)
    ms, *_ = step(ex, 20, level=70, ln=lines(8.0, target=70.0, floor=0.0), lim=limits(EXPORTING), vr=vr)
    assert ms.mode == EXPORT


def test_an_urgent_event_ends_a_leg_at_once():
    from test_engine_v2_execute import ev
    ex = Executor(s0())
    step(ex, 0, level=70, ln=lines(30.0, target=100.0), lim=limits(EXPORTING))
    vr = value_result(T0, forecast(T0, [segment(T0)]), timeline=_timeline(EXPORT))
    both = lines(10.0, **BOTH)
    ms, *_ = step(ex, 10, level=70, ln=both, lim=limits(EXPORTING), vr=vr)
    assert ms.mode == CHARGE                                           # without one the leg goes on
    ms, *_ = step(ex, 20, level=70, ln=both, lim=limits(EXPORTING), vr=vr, events=(ev("settings", 20),))
    assert ms.mode == EXPORT                                           # an urgent event lets the plan's mode through


def test_the_leg_goes_on_to_the_end_of_its_plan_step_and_then_the_plan_says():
    ex = Executor(s0())
    item = TimelineItem(mode=CHARGE, start=T0 - timedelta(minutes=5), end=T0 + timedelta(minutes=25), level_start=60.0,
                        level_end=80.0, until="until 80%", reason="x")
    vr = value_result(T0, forecast(T0, [segment(T0)]), timeline=(item,))
    ex._plan_target, ex._plan_sell = 80.0, None
    assert ex._leg_going(CHARGE, 70.0, T0)                              # short of the step's end
    assert not ex._leg_going(CHARGE, 79.95, T0)                         # reached it (within the level epsilon)
    assert ex._leg_going(EXPORT, 70.0, T0)                              # no sale step covers now: nothing ends it early
    ex._plan_sell = 55.0
    assert ex._leg_going(EXPORT, 60.0, T0) and not ex._leg_going(EXPORT, 55.05, T0)
    assert ex._plan_leg_end(vr, T0, CHARGE) == 80.0 and ex._plan_leg_end(vr, T0, EXPORT) is None


def test_consecutive_plan_steps_of_the_same_mode_are_one_leg():
    items = (TimelineItem(mode=EXPORT, start=T0 - timedelta(minutes=5), end=T0 + timedelta(minutes=25),
                          level_start=90.0, level_end=80.0, until="x", reason="x"),
             TimelineItem(mode=EXPORT, start=T0 + timedelta(minutes=25), end=T0 + timedelta(minutes=55),
                          level_start=80.0, level_end=66.0, until="x", reason="x"),
             TimelineItem(mode=CHARGE, start=T0 + timedelta(minutes=55), end=T0 + timedelta(minutes=85),
                          level_start=66.0, level_end=90.0, until="x", reason="x"))
    vr = value_result(T0, forecast(T0, [segment(T0)]), timeline=items)
    assert Executor._plan_leg_end(vr, T0, EXPORT) == 66.0


def test_a_sale_in_progress_goes_down_to_where_the_plan_ends_it():
    ex = Executor(s0())
    ex.mode = EXPORT
    ex._plan_sell = 45.0
    assert ex._sell_floor(limits(EXPORTING), lines(8.0, floor=60.0), staying=True) == 45.0
    assert ex._sell_floor(limits(EXPORTING), lines(8.0, floor=60.0), staying=False) == 60.0
    lim = limits(EXPORTING, floor=50.0)
    assert ex._sell_floor(lim, lines(8.0, floor=60.0), staying=True) == 50.0         # never under the floor


# ---- 4. "learned" revalues only when it matters -----------------------------------------------------
SIG = ((18.0, 0.95, 4.8, 4.8, (), (), 12.0, 6.0, 7.4), 30.0)


def _sig(cap=18.0, eta=0.95, days=30.0, taper=()):
    return ((cap, eta, 4.8, 4.8, taper, (), 12.0, 6.0, 7.4), days)


def test_a_rounding_change_in_a_learned_fact_is_not_a_change():
    assert not triggers.learned_moved(SIG, SIG)
    assert not triggers.learned_moved(SIG, _sig(eta=0.9505))             # 0.05%
    assert not triggers.learned_moved(SIG, _sig(cap=18.1))               # 0.56%
    assert not triggers.learned_moved(SIG, _sig(days=30.4))              # under a day


def test_a_real_change_is_one():
    assert triggers.learned_moved(SIG, _sig(eta=0.96))                   # 1.05%
    assert triggers.learned_moved(SIG, _sig(cap=17.5))
    assert triggers.learned_moved(SIG, _sig(days=31.0))
    assert triggers.learned_moved(SIG, _sig(days=None))                  # the profile went away
    assert triggers.learned_moved(SIG, _sig(taper=((95, 0.5),)))         # a taper appeared
    assert triggers.learned_moved(_sig(taper=((95, 0.5),)), _sig(taper=((95, 0.3),)))


def test_the_first_look_is_not_a_change():
    assert not triggers.learned_moved(None, SIG)


def test_a_slow_drift_adds_up_against_the_last_reported_figure():
    ref, moved = SIG, 0
    for i in range(1, 40):                                             # 0.1% a step; the reference moves on a change
        new = _sig(cap=18.0 * (1 - 0.001 * i))
        if triggers.learned_moved(ref, new):
            moved += 1
            ref = new
    assert moved == 3                                                   # about every 1%
