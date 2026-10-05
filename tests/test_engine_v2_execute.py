"""Engine v2, layer 5: the mode automaton (docs/plans/engine-v2.md section 8)."""

import json
from datetime import timedelta

from test_engine_v2_observe import T0, forecast, segment, value_result

from pe_core import decide as v1d
from pe_core.engine_v2.execute import Executor
from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.types import (
    CHARGE,
    EVENT,
    EXPORT,
    FREE,
    HOLD,
    SELF_USE,
    BatteryFacts,
    Event,
    Limits,
    Lines,
    Observation,
    TimelineItem,
)

FACTS = BatteryFacts()
NORMAL = frozenset({SELF_USE, HOLD, CHARGE})


def lines(value, imp=7.0, exp=15.0, target=None, floor=None, eta=0.95):
    return Lines(value_p=value, buy_line_p=imp / eta, sell_line_p=exp * eta, use_line_p=imp * eta,
                 store_sun_line_p=exp / eta, import_p=imp, export_p=exp, charge_target_soc=target,
                 sell_floor_soc=floor)


def limits(allowed=NORMAL, forced=None, floor=12.0, ceiling=100.0, rule="normal", reason="", **kw):
    return Limits(allowed=frozenset(allowed), forced=forced, floor_soc=floor, ceiling_soc=ceiling, rule=rule,
                  reason=reason, **kw)


def obs_at(t, level=50.0, data_ok=True, net=0.5, car=False, missing=()):
    return Observation(now=t, level_reported=level, level_filtered=level, net_load_kw=net, car_charging=car,
                       data_ok=data_ok, missing=tuple(missing))


def vr_default(timeline=()):
    segs = [segment(T0 + timedelta(minutes=30 * i)) for i in range(-2, 12)]
    return value_result(T0, forecast(T0, segs), timeline=timeline)


def step(ex, sec, level=50.0, ln=None, lim=None, vr="default", events=(), **ob):
    t = T0 + timedelta(seconds=sec)
    vr = vr_default() if vr == "default" else vr
    return ex.step(t, obs_at(t, level, **ob), lim or limits(), ln, vr, tuple(events), FACTS)


def s0(**kw):
    return V2Settings(**{"min_dwell_s": 0, **kw})


def ev(kind, sec=0, text="x"):
    return Event(T0 + timedelta(seconds=sec), kind, text)


# ---- charging ----------------------------------------------------------------------------------
def test_charges_when_a_stored_kwh_is_worth_more_than_the_buy_line_up_to_the_target():
    ex = Executor(V2Settings())
    ms, dec, new, changed = step(ex, 0, level=50, ln=lines(30.0, imp=6.99, target=88.0))
    assert changed and ms.mode == CHARGE and ms.target_soc == 88.0 and ms.rule == "v2_value"
    assert dec.action == v1d.GRID_CHARGE and dec.rule == "v2_value" and dec.target_soc == 88.0
    assert dec.label_target_soc == 88.0 and dec.details["engine"] == "v2" and "reached" not in dec.details
    assert "6.99p" in ms.why and "30.0p" in ms.why and "88%" in ms.why
    assert dec.reason[0].islower()                       # reads as v1's sentences do
    assert dec.sentence(passive=True).startswith("Would grid-charge to 88%")


def test_never_charges_unless_the_value_beats_the_import_price_after_losses():
    for imp in [5.0 + 0.5 * i for i in range(60)]:
        for value in [0.0 + 1.5 * j for j in range(45)]:
            ex = Executor(V2Settings())
            ms, *_ = step(ex, 0, level=50, ln=lines(value, imp=imp, target=90.0))
            if ms.mode == CHARGE:
                assert value > imp / 0.95 + 0.5 - 1e-9, (imp, value)


def test_no_charge_without_a_target_even_when_the_value_is_high():
    ms, *_ = step(Executor(V2Settings()), 0, ln=lines(30.0, target=None))
    assert ms.mode != CHARGE


def test_reaching_the_target_ends_the_charge_at_once_and_the_dip_does_not_restart_it():       # B3
    ex = Executor(V2Settings())
    ln = lines(30.0, target=88.0)
    step(ex, 0, level=80, ln=ln)
    ms, dec, new, changed = step(ex, 10, level=87.9, ln=ln)
    assert ms.mode == CHARGE and not changed                      # still short of it
    ms, dec, new, changed = step(ex, 20, level=88.0, ln=ln)       # within the minimum time: the level exit is not held
    assert changed and ms.mode != CHARGE
    assert [e.kind for e in new] == ["level"] and ms.chosen_by == "level"
    for sec, level in ((200, 87.5), (210, 87.2)):                  # the reading dips a point: no restart
        ms, *_ = step(ex, sec, level=level, ln=ln)
        assert ms.mode != CHARGE
    ms, *_ = step(ex, 220, level=86.9, ln=ln)                      # a point below the target: it may restart
    assert ms.mode == CHARGE


def test_a_charge_whose_price_is_no_longer_worth_it_ends_at_once():
    ex = Executor(V2Settings())
    step(ex, 0, level=50, ln=lines(30.0, imp=6.99, target=88.0))
    ms, dec, new, changed = step(ex, 15, level=51, ln=lines(30.0, imp=40.0, target=88.0))
    assert changed and ms.mode != CHARGE


def test_the_charge_target_is_held_at_the_ceiling():
    ex = Executor(V2Settings())
    ms, *_ = step(ex, 0, level=50, ln=lines(30.0, target=95.0), lim=limits(ceiling=90.0))
    assert ms.mode == CHARGE and ms.target_soc == 90.0


def test_charge_power_is_the_cap_in_watts_when_below_the_batterys_limit():
    ex = Executor(V2Settings())
    ms, dec, *_ = step(ex, 0, level=50, ln=lines(30.0, target=88.0), lim=limits(charge_cap_kw=2.4))
    assert ms.power_w == 2400.0 and dec.power_w == 2400.0 and "2.4 kW" in ms.why
    ms, dec, *_ = step(Executor(V2Settings()), 0, ln=lines(30.0, target=88.0))
    assert dec.power_w is None


# ---- hysteresis and minimum time (B10) ---------------------------------------------------------
def test_price_band_is_a_dead_zone_either_side_of_the_use_line():
    ex = Executor(s0())
    # use line 6.65p, band 0.5: Self-use starts below 6.15 and stops above 7.15
    assert step(ex, 0, ln=lines(6.4))[0].mode == HOLD
    assert step(ex, 10, ln=lines(6.0))[0].mode == SELF_USE
    assert step(ex, 20, ln=lines(6.9))[0].mode == SELF_USE
    assert step(ex, 30, ln=lines(7.3))[0].mode == HOLD
    assert step(ex, 40, ln=lines(6.9))[0].mode == HOLD


def test_minimum_time_blocks_an_opportunity_change_until_it_has_passed():
    ex = Executor(V2Settings(min_dwell_s=120))
    assert step(ex, 0, ln=lines(6.0))[0].mode == SELF_USE
    ms, dec, new, changed = step(ex, 60, ln=lines(9.0))           # Hold is better, but too soon
    assert ms.mode == SELF_USE and not changed
    assert step(ex, 119, ln=lines(9.0))[0].mode == SELF_USE
    ms, dec, new, changed = step(ex, 121, ln=lines(9.0))
    assert ms.mode == HOLD and changed


def test_an_urgent_event_is_never_held_back_by_the_minimum_time():
    ex = Executor(V2Settings(min_dwell_s=120))
    assert step(ex, 0, ln=lines(6.0))[0].mode == SELF_USE
    ms, *_ = step(ex, 30, ln=lines(30.0, target=88.0), events=[ev("car_start", 30)])
    assert ms.mode == CHARGE and ms.chosen_by == "car_start"


def test_a_non_urgent_event_is_held_back():
    ex = Executor(V2Settings(min_dwell_s=120))
    step(ex, 0, ln=lines(6.0))
    ms, *_ = step(ex, 30, ln=lines(30.0, target=88.0), events=[ev("price", 30)])
    assert ms.mode == SELF_USE


def test_a_mode_no_longer_allowed_ends_at_once():
    ex = Executor(V2Settings(min_dwell_s=120))
    step(ex, 0, ln=lines(6.0))
    ms, *_ = step(ex, 10, ln=lines(6.0), lim=limits(allowed={HOLD, CHARGE}, rule="car"))
    assert ms.mode == HOLD


def test_flip_flops_are_counted_inside_ten_minutes_only():
    ex = Executor(s0())
    step(ex, 0, ln=lines(6.0))
    step(ex, 60, ln=lines(9.0))
    assert not ex.flip_flop_now
    step(ex, 120, ln=lines(6.0))
    assert ex.flip_flop_now
    step(ex, 1000, ln=lines(9.0))
    step(ex, 2000, ln=lines(6.0))
    assert not ex.flip_flop_now


# ---- sun ---------------------------------------------------------------------------------------
def test_spare_sun_is_stored_when_the_battery_is_worth_more_than_exporting_it():          # B17
    ex = Executor(s0())
    ms, *_ = step(ex, 0, ln=lines(20.0), events=[ev("short_to_sun")])
    assert ms.mode == SELF_USE and "Spare sun goes into the battery" in ms.why


def test_spare_sun_is_exported_when_it_is_not_worth_storing():                           # B18
    ex = Executor(s0())
    ms, *_ = step(ex, 0, ln=lines(10.0), events=[ev("short_to_sun")])
    assert ms.mode == HOLD and "not worth storing" in ms.why


def test_a_shortfall_returns_to_the_use_comparison():
    ex = Executor(s0())
    step(ex, 0, ln=lines(10.0), events=[ev("short_to_sun")])
    ms, *_ = step(ex, 10, ln=lines(5.0), events=[ev("sun_to_short", 10)])
    assert ms.mode == SELF_USE and "covers the house" in ms.why


# ---- export ------------------------------------------------------------------------------------
def test_export_runs_to_the_sell_floor_then_stops():                                      # B13
    lim = limits(allowed=NORMAL | {EXPORT})
    ex = Executor(V2Settings())
    ln = lines(5.0, exp=15.0, floor=40.0)             # sell line 14.25p
    ms, dec, *_ = step(ex, 0, level=70, ln=ln, lim=lim)
    assert ms.mode == EXPORT and ms.target_soc == 40.0 and dec.action == v1d.EXPORT
    assert "15.00p" in ms.why and "5.0p" in ms.why
    ms, dec, new, changed = step(ex, 10, level=39.9, ln=ln, lim=lim)
    assert changed and ms.mode != EXPORT and [e.kind for e in new] == ["level"]


def test_export_needs_arbitrage_to_be_allowed():
    ms, *_ = step(Executor(V2Settings()), 0, level=70, ln=lines(5.0, floor=40.0))
    assert ms.mode != EXPORT


# ---- deadlines (B22) ---------------------------------------------------------------------------
def test_a_deadline_raises_an_event_once_and_does_not_stop_the_charge():                    # B22
    item = TimelineItem(CHARGE, T0, T0 + timedelta(hours=1), 40.0, 88.0, "until 88%", "reason")
    vr = vr_default(timeline=[item])
    ex = Executor(V2Settings(deadline_grace_min=10))
    ln = lines(30.0, target=88.0)
    ms, *_ = step(ex, 0, level=40, ln=ln, vr=vr)
    assert ms.deadline == T0 + timedelta(minutes=70)
    ms, dec, new, changed = step(ex, 3500, level=70, ln=ln, vr=vr)
    assert new == () and ms.mode == CHARGE
    ms, dec, new, changed = step(ex, 4300, level=75, ln=ln, vr=vr)       # 71 minutes in
    assert [e.kind for e in new] == ["deadline"] and new[0].revalue and ms.mode == CHARGE and not changed
    ms, dec, new, changed = step(ex, 4400, level=76, ln=ln, vr=vr)
    assert new == () and ms.mode == CHARGE


def test_a_new_value_result_with_a_later_end_moves_the_deadline_and_arms_it_again():
    item = TimelineItem(CHARGE, T0, T0 + timedelta(hours=1), 40.0, 88.0, "until 88%", "reason")
    ex = Executor(V2Settings(deadline_grace_min=10))
    ln = lines(30.0, target=88.0)
    step(ex, 0, level=40, ln=ln, vr=vr_default(timeline=[item]))
    ms, dec, new, _ = step(ex, 4300, level=75, ln=ln, vr=vr_default(timeline=[item]))
    assert [e.kind for e in new] == ["deadline"]
    later = TimelineItem(CHARGE, T0, T0 + timedelta(hours=2), 40.0, 88.0, "until 88%", "reason")
    vr2 = value_result(T0 + timedelta(seconds=4310), vr_default().forecast, timeline=[later])
    ms, dec, new, _ = step(ex, 4310, level=75, ln=ln, vr=vr2)
    assert new == () and ms.deadline == T0 + timedelta(minutes=130)


def test_the_exits_of_a_charge_are_listed_with_expected_times():
    item = TimelineItem(CHARGE, T0, T0 + timedelta(hours=1), 40.0, 88.0, "until 88%", "reason")
    segs = [segment(T0 + timedelta(minutes=30 * i), import_p=7.0 if i < 2 else 30.0) for i in range(-2, 12)]
    vr = value_result(T0, forecast(T0, segs), timeline=[item])
    ex = Executor(V2Settings())
    ms, *_ = step(ex, 0, level=40, ln=lines(30.0, target=88.0), vr=vr)
    kinds = {e.kind: e for e in ms.exits}
    assert kinds["level"].text == "The battery reaches 88%" and kinds["level"].expected_at == T0 + timedelta(hours=1)
    assert "price" in kinds and "car" in kinds and "deadline" in kinds


# ---- forced modes ------------------------------------------------------------------------------
def test_a_grid_event_forces_the_event_mode_with_no_minimum_time():
    ex = Executor(V2Settings(min_dwell_s=600))
    step(ex, 0, ln=lines(6.0))
    lim = limits(allowed=(), forced=EVENT, floor=11.0, rule="event", discharge_cap_kw=3.0)
    ms, dec, new, changed = step(ex, 5, level=40, ln=lines(6.0), lim=lim)
    assert changed and ms.mode == EVENT and dec.action == v1d.FORCE_DISCHARGE and dec.power_w == 3000.0
    assert dec.rule == "v2_event" and "pays 115p" in ms.why and "11%" in ms.why and ms.target_soc == 11.0


def test_a_grid_event_stops_at_the_hard_floor_plus_margin_and_holds_to_the_end():            # B14a
    ex = Executor(V2Settings())
    lim = limits(allowed=(), forced=EVENT, floor=11.0, rule="event")
    ms, *_ = step(ex, 0, level=30.0, ln=lines(6.0), lim=lim)
    assert ms.mode == EVENT
    ms, dec, new, changed = step(ex, 600, level=11.0, ln=lines(6.0), lim=lim)
    assert ms.mode == HOLD and dec.rule == "v2_event_floor" and changed
    assert [e.kind for e in new] == ["reserve"]
    ms, *_ = step(ex, 700, level=12.5, ln=lines(6.0), lim=lim)         # the sun lifts it: still holding
    assert ms.mode == HOLD
    ms, *_ = step(ex, 800, level=12.5, ln=lines(6.0), lim=limits())    # the event is over
    assert ms.mode != EVENT and not ex.event_floor_hit


def test_free_power_charges_to_the_ceiling_then_holds():
    ex = Executor(V2Settings())
    lim = limits(allowed=(), forced=FREE, rule="free")
    ms, dec, *_ = step(ex, 0, level=60, ln=lines(6.0), lim=lim)
    assert ms.mode == FREE and dec.action == v1d.GRID_CHARGE and dec.target_soc == 100.0
    ms, *_ = step(ex, 10, level=100.0, ln=lines(6.0), lim=lim)
    assert ms.mode == HOLD and "reached 100%" in ms.why
    ms, *_ = step(ex, 20, level=99.5, ln=lines(6.0), lim=lim)          # inside the level band: still holding
    assert ms.mode == HOLD
    ms, *_ = step(ex, 30, level=98.5, ln=lines(6.0), lim=lim)
    assert ms.mode == FREE


def test_override_modes_are_forced_and_say_why():                                         # B19
    reason = "Manual override: Hold until 14:00"
    ex = Executor(V2Settings(min_dwell_s=600))
    step(ex, 0, ln=lines(6.0))
    ms, dec, *_ = step(ex, 5, ln=lines(6.0), lim=limits(allowed={HOLD}, forced=HOLD, rule="override", reason=reason))
    assert ms.mode == HOLD and dec.rule == "v2_override" and "Manual override" in ms.why
    ms, dec, *_ = step(ex, 6, ln=lines(6.0), lim=limits(allowed={EXPORT}, forced=EXPORT, rule="override",
                                                       reason=reason, discharge_cap_kw=2.0))
    assert ms.mode == EXPORT and dec.power_w == 2000.0


def test_override_self_use_holds_at_the_reserve_and_lets_go_a_band_above_it():
    ex = Executor(V2Settings())
    lim = limits(allowed={SELF_USE}, forced=SELF_USE, floor=20.0, rule="override", reason="Manual override")
    assert step(ex, 0, level=40, ln=lines(6.0), lim=lim)[0].mode == SELF_USE
    ms, dec, *_ = step(ex, 10, level=20.0, ln=lines(6.0), lim=lim)
    assert ms.mode == HOLD and dec.rule == "v2_override_reserve" and ex.reserve_latched
    assert step(ex, 20, level=20.6, ln=lines(6.0), lim=lim)[0].mode == HOLD
    assert step(ex, 30, level=21.5, ln=lines(6.0), lim=lim)[0].mode == SELF_USE


def test_override_charge_stops_at_the_ceiling_with_the_level_band():
    ex = Executor(V2Settings())
    lim = limits(allowed={CHARGE}, forced=CHARGE, ceiling=90.0, rule="override", reason="Manual override: charge")
    assert step(ex, 0, level=50, ln=lines(6.0), lim=lim)[0].mode == CHARGE
    ms, *_ = step(ex, 10, level=90.0, ln=lines(6.0), lim=lim)
    assert ms.mode == HOLD
    assert step(ex, 20, level=89.4, ln=lines(6.0), lim=lim)[0].mode == HOLD
    assert step(ex, 30, level=88.9, ln=lines(6.0), lim=lim)[0].mode == CHARGE


# ---- the rules' restrictions -------------------------------------------------------------------
def test_with_the_car_charging_the_battery_holds_and_never_feeds_it():                     # B7
    lim = limits(allowed={HOLD, CHARGE}, rule="car", reason="The car is charging")
    ex = Executor(V2Settings())
    ms, dec, *_ = step(ex, 0, ln=lines(3.0), lim=lim, car=True)          # the battery would cover the house
    assert ms.mode == HOLD and dec.rule == "v2_car" and "car is charging" in ms.why


def test_with_the_car_charging_a_worthwhile_charge_still_goes_ahead():
    lim = limits(allowed={HOLD, CHARGE}, rule="car")
    ms, *_ = step(Executor(V2Settings()), 0, ln=lines(30.0, target=88.0), lim=lim, car=True)
    assert ms.mode == CHARGE


def test_at_the_reserve_nothing_discharges_and_one_event_is_raised():                      # B14
    lim = limits(allowed={HOLD, CHARGE}, rule="reserve", reason="The battery is at its 12% reserve")
    ex = Executor(V2Settings())
    ms, dec, new, _ = step(ex, 0, level=12, ln=lines(3.0), lim=lim)
    assert ms.mode == HOLD and dec.rule == "v2_reserve" and [e.kind for e in new] == ["reserve"]
    assert ex.reserve_latched
    ms, dec, new, _ = step(ex, 10, level=12, ln=lines(3.0), lim=lim)
    assert new == ()
    step(ex, 20, level=30, ln=lines(3.0), lim=limits())
    assert not ex.reserve_latched


# ---- data and start ----------------------------------------------------------------------------
def test_missing_data_gives_self_use_even_during_a_grid_event():                          # B12
    lim = limits(allowed=(), forced=EVENT, floor=11.0, rule="event")
    ex = Executor(V2Settings())
    step(ex, 0, level=40, ln=lines(6.0), lim=lim)
    ms, dec, new, changed = step(ex, 600, level=40, ln=None, lim=lim, data_ok=False, missing=["import rate"])
    assert ms.mode == SELF_USE and dec.rule == "v2_data_missing" and changed
    assert "import rate" in ms.why and dec.action == v1d.SELF_USE


def test_without_a_value_result_yet_it_is_self_use_and_starting():
    ms, dec, *_ = step(Executor(V2Settings()), 0, ln=None, vr=None)
    assert ms.mode == SELF_USE and dec.rule == "v2_starting"


def test_a_forced_event_does_not_wait_for_the_first_value_result():
    lim = limits(allowed=(), forced=EVENT, floor=11.0, rule="event")
    ms, *_ = step(Executor(V2Settings()), 0, ln=None, vr=None, lim=lim)
    assert ms.mode == EVENT


def test_the_decisions_details_never_use_the_v1_latch_key():
    ex = Executor(V2Settings())
    for sec, level in ((0, 50), (10, 88)):
        _, dec, *_ = step(ex, sec, level=level, ln=lines(30.0, target=88.0))
        assert "reached" not in dec.details and dec.details["mode"] in (CHARGE, HOLD, SELF_USE)


def test_the_reason_keeps_a_supplier_name_capitalised():
    lim = limits(allowed=(), forced=EVENT, floor=11.0, rule="event")
    _, dec, *_ = step(Executor(V2Settings()), 0, ln=lines(6.0), lim=lim)
    assert dec.reason.startswith("Axle event")


def test_state_is_json_safe_and_a_restored_executor_carries_on():
    item = TimelineItem(CHARGE, T0, T0 + timedelta(hours=1), 40.0, 88.0, "until 88%", "reason")
    vr = vr_default(timeline=[item])
    ex = Executor(V2Settings())
    ln = lines(30.0, target=88.0)
    ms, *_ = step(ex, 0, level=50, ln=ln, vr=vr)
    st = json.loads(json.dumps(ex.state()))
    ex2 = Executor(V2Settings(), st)
    ms2 = ex2.mode_state()
    assert ms2.mode == CHARGE and ms2.since == ms.since and ms2.target_soc == 88.0 and ms2.lines == ln
    assert [e.kind for e in ms2.exits] == [e.kind for e in ms.exits]
    ms3, dec, new, changed = step(ex2, 10, level=51, ln=ln, vr=vr)
    assert ms3.mode == CHARGE and not changed


def test_a_running_charge_follows_the_timelines_end_level_when_the_lines_target_creeps_up():
    first = TimelineItem(CHARGE, T0, T0 + timedelta(minutes=30), 12.0, 60.0, "until 60%", "r")
    second = TimelineItem(CHARGE, T0 + timedelta(minutes=30), T0 + timedelta(minutes=90), 60.0, 100.0,
                          "until 100%", "r")
    vr = vr_default(timeline=[first, second])
    ex = Executor(V2Settings())
    ms, *_ = step(ex, 0, level=12.0, ln=lines(30.0, target=23.0), vr=vr)
    assert ms.mode == CHARGE and ms.target_soc == 23.0                  # it starts on the lines
    ms, dec, new, changed = step(ex, 10, level=23.5, ln=lines(30.0, target=23.0), vr=vr)
    assert ms.mode == CHARGE and not changed and ms.target_soc == 100.0 and new == ()
    ms, *_ = step(ex, 20, level=23.5, ln=lines(30.0, target=None), vr=vr)      # a tie on the curve: it carries on
    assert ms.mode == CHARGE


def test_without_a_timeline_charge_the_lines_target_ends_it():
    ex = Executor(V2Settings())
    step(ex, 0, level=12.0, ln=lines(30.0, target=23.0))
    ms, dec, new, changed = step(ex, 10, level=23.5, ln=lines(30.0, target=23.0))
    assert changed and ms.mode != CHARGE


def test_charge_starts_when_the_plan_charges_in_this_stretch_even_on_a_tie():
    """A flat cheap night: the value equals the buy line (charging now or later costs the same), but the plan charges
    in this stretch, so the charge starts now (charge_now), not at the end of the cheap rate."""
    from datetime import datetime, timezone

    from pe_core.engine_v2.execute import Executor
    from pe_core.engine_v2.settings import V2Settings
    from pe_core.engine_v2.types import CHARGE, HOLD, SELF_USE, BatteryFacts, Limits, Lines, Observation

    now = datetime(2026, 10, 5, 22, 35, tzinfo=timezone.utc)
    ex = Executor(V2Settings(min_dwell_s=0))
    lim = Limits(allowed=frozenset({SELF_USE, HOLD, CHARGE}), floor_soc=12, ceiling_soc=100)
    obs = Observation(now=now, level_reported=40.0, level_filtered=40.0, net_load_kw=0.5, car_charging=False,
                      data_ok=True)
    tie = dict(value_p=7.36, buy_line_p=7.36, sell_line_p=14.25, use_line_p=6.64, store_sun_line_p=15.79,
               import_p=6.99, export_p=15.0)
    from test_engine_v2_observe import forecast, segment, value_result
    vr = value_result(now, forecast(now, [segment(now, import_p=6.99)]))
    mode, _, _, _ = ex.step(now, obs, lim, Lines(**tie), vr, (), BatteryFacts())
    assert mode.mode != CHARGE                     # a tie alone does not buy
    ex2 = Executor(V2Settings(min_dwell_s=0))
    ln = Lines(**tie, charge_target_soc=71.0, charge_now=True, run_target_soc=71.0)
    mode, decision, _, _ = ex2.step(now, obs, lim, ln, vr, (), BatteryFacts())
    assert mode.mode == CHARGE and decision.target_soc == 71.0
