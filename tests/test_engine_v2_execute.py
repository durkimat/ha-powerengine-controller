"""Engine v2, layer 5: the mode automaton (docs/plans/engine-v2.md section 8). The decision itself is the plan's
(`value.decide`, tested in test_engine_v2_value.py and test_engine_v2_one_decision.py); here it is replaced by a fixed
answer, so these tests are about what the executor does with it: carry it out to its level, the sentence, the forced
modes, the limits, the deadline and the exits."""

import json
from datetime import timedelta

import pytest
from test_engine_v2_observe import T0, forecast, segment, value_result

from pe_core import decide as v1d
from pe_core.engine_v2 import execute
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


class Decides:
    """Stands in for `value.decide`: `answer` is a decision dict, or a function of the call's keyword arguments
    (`soc`, `prev`, `last_run`, `import_p`, ...) returning one; it is asked again when the executor asks again."""

    def __init__(self):
        self.answer = {"mode": SELF_USE}
        self.calls = []

    def set(self, mode, stop=None, rest=SELF_USE):
        self.answer = {"mode": mode, "stop": stop, "rest": rest}

    def __call__(self, vr, t, soc, lim, facts, settings, prev, leg_from=None, used=0, last_run=None, import_p=None,
                 export_p=None):
        kw = dict(t=t, soc=soc, prev=prev, leg_from=leg_from, used=used, last_run=last_run, import_p=import_p,
                  export_p=export_p)
        self.calls.append(kw)
        got = self.answer(**kw) if callable(self.answer) else self.answer
        if isinstance(got, str):
            got = {"mode": got}
        return {"stop": None, "rest": SELF_USE, "until": (T0 + timedelta(hours=2)).timestamp(), "end": soc, **got}


@pytest.fixture(autouse=True)
def decides(monkeypatch):
    d = Decides()
    monkeypatch.setattr(execute, "decide", d)
    return d


def ev(kind, sec=0, text="x"):
    return Event(T0 + timedelta(seconds=sec), kind, text)


# ---- charging ----------------------------------------------------------------------------------
def test_carries_out_the_decisions_charge_to_its_level_and_says_why(decides):
    decides.set(CHARGE, 88.0)
    ex = Executor(V2Settings())
    ms, dec, new, changed = step(ex, 0, level=50, ln=lines(30.0, imp=6.99))
    assert changed and ms.mode == CHARGE and ms.target_soc == 88.0 and ms.rule == "v2_value"
    assert dec.action == v1d.GRID_CHARGE and dec.rule == "v2_value" and dec.target_soc == 88.0
    assert dec.label_target_soc == 88.0 and dec.details["engine"] == "v2" and "reached" not in dec.details
    assert "6.99p" in ms.why and "30.0p" in ms.why and "88%" in ms.why
    assert dec.reason[0].islower()                       # reads as v1's sentences do
    assert dec.sentence(passive=True).startswith("Would grid-charge to 88%")
    assert decides.calls[0]["prev"] is None and decides.calls[0]["import_p"] == 6.99     # the live rate goes in


def test_the_decision_stands_until_something_changes_and_is_asked_again_when_it_does(decides):
    decides.set(CHARGE, 88.0)
    ex = Executor(V2Settings())
    ln = lines(30.0, imp=6.99)
    step(ex, 0, level=50, ln=ln)
    step(ex, 10, level=51, ln=ln)
    step(ex, 20, level=52, ln=ln)
    assert len(decides.calls) == 1                       # nothing changed: it is carried out, not decided again
    step(ex, 30, level=53, ln=lines(30.0, imp=7.5))      # a new live rate
    assert len(decides.calls) == 2 and decides.calls[1]["import_p"] == 7.5 and decides.calls[1]["prev"] == CHARGE
    step(ex, 40, level=54, ln=lines(30.0, imp=7.5), lim=limits(ceiling=90.0))             # new limits
    assert len(decides.calls) == 3
    step(ex, 50, level=55, ln=lines(30.0, imp=7.5), lim=limits(ceiling=90.0),
         vr=value_result(T0 + timedelta(seconds=50), vr_default().forecast))              # a new plan
    assert len(decides.calls) == 4
    step(ex, 3600 * 3, level=56, ln=lines(30.0, imp=7.5), lim=limits(ceiling=90.0),
         vr=value_result(T0 + timedelta(seconds=50), vr_default().forecast))              # its "until" has passed
    assert len(decides.calls) == 5


def test_reaching_the_level_ends_the_charge_at_once_and_asks_for_the_next_decision(decides):       # B3
    def answer(last_run=None, **kw):
        return {"mode": SELF_USE} if last_run == CHARGE else {"mode": CHARGE, "stop": 88.0}
    decides.answer = answer
    ex = Executor(V2Settings())
    ln = lines(30.0, target=88.0)
    step(ex, 0, level=80, ln=ln)
    ms, dec, new, changed = step(ex, 10, level=87.8, ln=ln)
    assert ms.mode == CHARGE and not changed                      # still short of it
    ms, dec, new, changed = step(ex, 20, level=88.0, ln=ln)
    assert changed and ms.mode != CHARGE
    assert [e.kind for e in new] == ["level_edge"] and ms.chosen_by == "level"
    last = decides.calls[-1]
    assert last["last_run"] == CHARGE and last["used"] == 1 and last["prev"] == CHARGE   # the plan's next step, now
    for sec, level in ((200, 87.5), (210, 86.0)):                  # the reading dips: the decision stands
        ms, *_ = step(ex, sec, level=level, ln=ln)
        assert ms.mode != CHARGE


def test_a_level_within_the_tolerance_of_the_target_counts_as_reached(decides):
    """The filter closes on the reading but never reaches it: 99.99999999% at a target of 100% used to keep the charge
    going for hours (dull day, 00:02 to 09:30)."""
    decides.answer = lambda last_run=None, **kw: {"mode": SELF_USE if last_run else CHARGE, "stop": 100.0}
    ex = Executor(V2Settings())
    ms, *_ = step(ex, 0, level=90, ln=lines(30.0))
    assert ms.mode == CHARGE
    ms, dec, new, changed = step(ex, 600, level=99.99999999, ln=lines(30.0))
    assert changed and ms.mode != CHARGE and [e.kind for e in new] == ["level_edge"]


def test_the_charge_target_is_held_at_the_ceiling(decides):
    decides.set(CHARGE, 95.0)
    ms, *_ = step(Executor(V2Settings()), 0, level=50, ln=lines(30.0), lim=limits(ceiling=90.0))
    assert ms.mode == CHARGE and ms.target_soc == 90.0


def test_charge_power_is_the_cap_in_watts_when_below_the_batterys_limit(decides):
    decides.set(CHARGE, 88.0)
    ex = Executor(V2Settings())
    ms, dec, *_ = step(ex, 0, level=50, ln=lines(30.0), lim=limits(charge_cap_kw=2.4))
    assert ms.power_w == 2400.0 and dec.power_w == 2400.0 and "2.4 kW" in ms.why
    ms, dec, *_ = step(Executor(V2Settings()), 0, ln=lines(30.0))
    assert dec.power_w is None


# ---- idle time, limits and flip-flops ---------------------------------------------------------
def test_idle_time_is_the_idle_mode_the_decision_names(decides):
    decides.set(HOLD)
    ms, *_ = step(Executor(V2Settings()), 0, ln=lines(9.0))
    assert ms.mode == HOLD and "the grid runs the house" in ms.why
    decides.set(SELF_USE)
    ms, *_ = step(Executor(V2Settings()), 0, ln=lines(5.0))
    assert ms.mode == SELF_USE and "covers the house" in ms.why


def test_a_mode_no_longer_allowed_ends_at_once(decides):
    decides.set(SELF_USE)
    ex = Executor(V2Settings())
    step(ex, 0, ln=lines(6.0))
    ms, *_ = step(ex, 10, ln=lines(6.0), lim=limits(allowed={HOLD, CHARGE}, rule="car"))
    assert ms.mode == HOLD


def test_a_decision_for_a_mode_the_rules_do_not_allow_falls_back_to_the_idle_mode(decides):
    decides.set(EXPORT, 40.0, rest=HOLD)
    ms, *_ = step(Executor(V2Settings()), 0, level=70, ln=lines(5.0, floor=40.0))      # export is not allowed
    assert ms.mode == HOLD
    decides.set(EXPORT, 40.0, rest=SELF_USE)
    ms, *_ = step(Executor(V2Settings()), 0, level=70, ln=lines(5.0, floor=40.0), lim=limits({SELF_USE, CHARGE}))
    assert ms.mode == SELF_USE


def test_flip_flops_are_counted_inside_ten_minutes_only(decides):
    decides.answer = lambda import_p=None, **kw: HOLD if import_p > 8 else SELF_USE
    ex = Executor(V2Settings())
    step(ex, 0, ln=lines(6.0, imp=7.0))
    step(ex, 60, ln=lines(9.0, imp=9.0))
    assert not ex.flip_flop_now
    step(ex, 120, ln=lines(6.0, imp=7.0))
    assert ex.flip_flop_now
    step(ex, 1000, ln=lines(9.0, imp=9.0))
    step(ex, 2000, ln=lines(6.0, imp=7.0))
    assert not ex.flip_flop_now


# ---- sun ---------------------------------------------------------------------------------------
def test_spare_sun_is_said_to_be_stored_in_self_use(decides):          # B17
    decides.set(SELF_USE)
    ms, *_ = step(Executor(V2Settings()), 0, ln=lines(20.0), events=[ev("short_to_sun")])
    assert ms.mode == SELF_USE and "Spare sun goes into the battery" in ms.why


def test_spare_sun_is_said_to_be_exported_in_hold(decides):             # B18
    decides.set(HOLD)
    ms, *_ = step(Executor(V2Settings()), 0, ln=lines(10.0), events=[ev("short_to_sun")])
    assert ms.mode == HOLD and "not worth storing" in ms.why


def test_a_shortfall_returns_to_the_house_sentence(decides):
    decides.set(SELF_USE)
    ex = Executor(V2Settings())
    step(ex, 0, ln=lines(10.0), events=[ev("short_to_sun")])
    ms, *_ = step(ex, 10, ln=lines(5.0), events=[ev("sun_to_short", 10)])
    assert ms.mode == SELF_USE and "covers the house" in ms.why


# ---- export ------------------------------------------------------------------------------------
def test_export_runs_to_the_decisions_level_then_stops(decides):                           # B13
    decides.answer = lambda last_run=None, **kw: {"mode": SELF_USE} if last_run else {"mode": EXPORT, "stop": 40.0}
    lim = limits(allowed=NORMAL | {EXPORT})
    ex = Executor(V2Settings())
    ln = lines(5.0, exp=15.0, floor=40.0)             # sell line 14.25p
    ms, dec, *_ = step(ex, 0, level=70, ln=ln, lim=lim)
    assert ms.mode == EXPORT and ms.target_soc == 40.0 and dec.action == v1d.EXPORT
    assert "15.00p" in ms.why and "5.0p" in ms.why
    ms, dec, new, changed = step(ex, 10, level=39.9, ln=ln, lim=lim)
    assert changed and ms.mode != EXPORT and [e.kind for e in new] == ["level_edge"]


def test_a_sale_never_goes_below_the_floor_even_if_the_decision_says_so(decides):
    decides.set(EXPORT, 5.0)
    ms, *_ = step(Executor(V2Settings()), 0, level=70, ln=lines(5.0), lim=limits(NORMAL | {EXPORT}, floor=12.0))
    assert ms.mode == EXPORT and ms.target_soc == 12.0


def test_a_sale_at_the_floor_ends_when_the_filtered_level_only_approaches_it(decides):
    decides.answer = lambda last_run=None, **kw: {"mode": SELF_USE} if last_run else {"mode": EXPORT, "stop": None}
    ex = Executor(V2Settings())
    ln = lines(5.0, floor=0.0)
    ms, *_ = step(ex, 0, level=50, ln=ln, lim=limits(EXPORTING))
    assert ms.mode == EXPORT
    ms, *_ = step(ex, 600, level=12.00000001, ln=ln, lim=limits(EXPORTING))
    assert ms.mode != EXPORT


# ---- deadlines (B22) ---------------------------------------------------------------------------
def test_a_deadline_raises_an_event_once_and_does_not_stop_the_charge(decides):                    # B22
    decides.set(CHARGE, 88.0)
    item = TimelineItem(CHARGE, T0, T0 + timedelta(hours=1), 40.0, 88.0, "until 88%", "reason")
    vr = vr_default(timeline=[item])
    ex = Executor(V2Settings())
    ln = lines(30.0, target=88.0)
    ms, *_ = step(ex, 0, level=40, ln=ln, vr=vr)
    assert ms.deadline == T0 + timedelta(minutes=70)
    ms, dec, new, changed = step(ex, 3500, level=70, ln=ln, vr=vr)
    assert new == () and ms.mode == CHARGE
    ms, dec, new, changed = step(ex, 4300, level=75, ln=ln, vr=vr)       # 71 minutes in
    assert [e.kind for e in new] == ["deadline"] and new[0].revalue and ms.mode == CHARGE and not changed
    ms, dec, new, changed = step(ex, 4400, level=76, ln=ln, vr=vr)
    assert new == () and ms.mode == CHARGE


def test_a_new_value_result_with_a_later_end_moves_the_deadline_and_arms_it_again(decides):
    decides.set(CHARGE, 88.0)
    item = TimelineItem(CHARGE, T0, T0 + timedelta(hours=1), 40.0, 88.0, "until 88%", "reason")
    ex = Executor(V2Settings())
    ln = lines(30.0, target=88.0)
    step(ex, 0, level=40, ln=ln, vr=vr_default(timeline=[item]))
    ms, dec, new, _ = step(ex, 4300, level=75, ln=ln, vr=vr_default(timeline=[item]))
    assert [e.kind for e in new] == ["deadline"]
    later = TimelineItem(CHARGE, T0, T0 + timedelta(hours=2), 40.0, 88.0, "until 88%", "reason")
    vr2 = value_result(T0 + timedelta(seconds=4310), vr_default().forecast, timeline=[later])
    ms, dec, new, _ = step(ex, 4310, level=75, ln=ln, vr=vr2)
    assert new == () and ms.deadline == T0 + timedelta(minutes=130)


def test_the_exits_of_a_charge_are_listed_with_expected_times(decides):
    decides.set(CHARGE, 88.0)
    item = TimelineItem(CHARGE, T0, T0 + timedelta(hours=2), 40.0, 88.0, "until 88%", "reason")
    segs = [segment(T0 + timedelta(minutes=30 * i), import_p=7.0 if i < 2 else 30.0) for i in range(-2, 12)]
    vr = value_result(T0, forecast(T0, segs), timeline=[item])
    ex = Executor(V2Settings())
    ms, *_ = step(ex, 0, level=40, ln=lines(30.0, target=88.0), vr=vr)
    kinds = {e.kind: e for e in ms.exits}
    assert kinds["level"].text == "The battery reaches 88%" and kinds["level"].expected_at == T0 + timedelta(hours=2)
    assert "price" in kinds and "car" in kinds and "deadline" in kinds


def test_the_expected_time_of_a_level_exit_is_never_earlier_than_the_power_allows(decides):
    # 9 Oct 2026: "reaches 32% at 18:00" shown at 81% and 4.95 kW, because the plan's step for the sale ended at 18:00
    # while the live sale runs to its level. 49 points of 18 kWh is nearly two hours.
    decides.set(EXPORT, 32.0)
    item = TimelineItem(EXPORT, T0, T0 + timedelta(minutes=18), 81.0, 73.0, "until 73%", "reason")
    segs = [segment(T0 + timedelta(minutes=30 * i), import_p=7.0) for i in range(-2, 12)]
    vr = value_result(T0, forecast(T0, segs), timeline=[item])
    ex = Executor(V2Settings())
    ms, *_ = step(ex, 0, level=81, ln=lines(10.0, floor=32.0), vr=vr, lim=limits(allowed=NORMAL | {EXPORT}))
    level = next(e for e in ms.exits if e.kind == "level")
    hours = (level.expected_at - T0).total_seconds() / 3600
    assert ms.mode == EXPORT and 1.5 < hours < 2.2


# ---- forced modes ------------------------------------------------------------------------------
def test_a_grid_event_forces_the_event_mode_with_no_minimum_time():
    ex = Executor(V2Settings())
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
    ex = Executor(V2Settings())
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
    car = [e for e in ms.exits if e.kind == "car"]                       # what ends the hold is the car stopping
    assert [e.text for e in car] == ["The car stops charging"]


def test_with_the_car_charging_a_worthwhile_charge_still_goes_ahead(decides):
    decides.set(CHARGE, 88.0)
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


def test_the_decisions_details_never_use_the_v1_latch_key(decides):
    decides.answer = lambda last_run=None, **kw: {"mode": HOLD} if last_run else {"mode": CHARGE, "stop": 88.0}
    ex = Executor(V2Settings())
    for sec, level in ((0, 50), (10, 88)):
        _, dec, *_ = step(ex, sec, level=level, ln=lines(30.0, target=88.0))
        assert "reached" not in dec.details and dec.details["mode"] in (CHARGE, HOLD, SELF_USE)


def test_the_reason_keeps_a_supplier_name_capitalised():
    lim = limits(allowed=(), forced=EVENT, floor=11.0, rule="event")
    _, dec, *_ = step(Executor(V2Settings()), 0, ln=lines(6.0), lim=lim)
    assert dec.reason.startswith("Axle event")


def test_state_is_json_safe_and_a_restored_executor_carries_on(decides):
    decides.set(CHARGE, 88.0)
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


EXPORTING = NORMAL | {EXPORT}


def test_a_charge_remembers_the_level_it_began_at_and_tells_the_decision(decides):
    """The charge or sale running has a minimum length between the level bands (`value.leg_band`); the decision needs
    to know where it began."""
    decides.set(CHARGE, 88.0)
    ex = Executor(V2Settings())
    step(ex, 0, level=60, ln=lines(30.0, imp=6.99))
    assert ex.leg_from == 60
    step(ex, 60, level=64, ln=lines(30.0, imp=7.1))                          # asked again, mid-charge
    assert decides.calls[-1]["leg_from"] == 60 and decides.calls[-1]["prev"] == CHARGE
    assert Executor(V2Settings(), ex.state()).leg_from == 60                 # kept across a restart
    decides.set(SELF_USE)
    step(ex, 120, level=70, ln=lines(30.0, imp=7.2))
    assert ex.leg_from is None                                               # forgotten when the charge ends


def test_a_forced_charge_ends_at_its_ceiling_the_same_way():
    ex = Executor(V2Settings())
    lim = limits(set(), forced=FREE, rule="free")
    ms, *_ = step(ex, 0, level=90, ln=lines(5.0), lim=lim)
    assert ms.mode == FREE
    ms, *_ = step(ex, 600, level=99.99999999, ln=lines(5.0), lim=lim)
    assert ms.mode == HOLD
