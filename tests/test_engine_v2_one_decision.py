"""Engine v2: one decision for the plan and the executor. `value._choose` is the decision; the expected timeline is it
stepped forward over the forecast (`value._walk`) and the executor makes the same call from the live state
(`value.decide`). These tests are about that agreement, not about which mode is right on a given day (the tests in
test_engine_v2_value.py are)."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from pe_core.config import parse_config
from pe_core.engine_v2 import execute
from pe_core.engine_v2 import value as V
from pe_core.engine_v2.execute import Executor
from pe_core.engine_v2.settings import RETIRED, SETTINGS, V2Settings, catalogue, parse_v2
from pe_core.engine_v2.types import (
    CHARGE,
    EXPORT,
    HOLD,
    SELF_USE,
    BatteryFacts,
    Forecast,
    Limits,
    Observation,
    Segment,
    Spread,
)

UTC = timezone.utc
T0 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
FACTS = BatteryFacts()                      # 18 kWh, 95% each way, 4.8 kW, floor 12%
NO_COMFORT = V2Settings(range_firmness="off", switch_cost_p=0.0)


@pytest.fixture(autouse=True)
def _zero_terminal_value(monkeypatch):
    monkeypatch.setattr(V, "TERMINAL_FIXED_P", 0.0)


def t(i):
    return T0 + timedelta(minutes=30 * i)


def seg(i, imp=30.28, exp=15.0, load=0.5):
    sp = lambda x: Spread(x, x, x, (0.25, 0.5, 0.25))                   # noqa: E731
    return Segment(start=t(i), end=t(i + 1), import_p=imp, export_p=exp, solar_kwh=sp(0.0), load_kwh=sp(load))


def fc_of(segs):
    return Forecast(made_at=segs[0].start, segments=tuple(segs))


def limits_for(sg, arbitrage=False):
    allowed = {SELF_USE, HOLD, CHARGE} | ({EXPORT} if arbitrage else set())
    return Limits(allowed=frozenset(allowed), forced=None, floor_soc=12.0, ceiling_soc=100.0)


def solve(segs, soc=50.0, settings=NO_COMFORT, lim=limits_for, running=None, leg_from=None):
    return V.solve(fc_of(segs), soc, FACTS, settings, lim, segs[0].start, "test", UTC, running=running,
                   leg_from=leg_from)


def night_and_day(n_cheap=12, n_dear=20):
    return [seg(i, imp=6.99) for i in range(n_cheap)] + [seg(n_cheap + i, imp=28.84) for i in range(n_dear)]


def cheap_then_peak(n_cheap, n_peak, load=0.5):
    return [seg(i, imp=6.99) for i in range(n_cheap)] + [seg(n_cheap + i, imp=30.28, load=load) for i in range(n_peak)]


# ---- the plan and the executor agree -------------------------------------------------------------------------
@pytest.mark.parametrize("segs, soc, arbitrage", [
    (night_and_day(), 12.0, False),                                         # a cheap night, then a dear day
    (night_and_day(), 70.0, False),                                         # already full enough: idle
    (cheap_then_peak(1, 2), 12.0, False),                                   # a charge that stops part-way
    ([seg(i, imp=6.99, exp=15.0, load=0.3) for i in range(12)], 100.0, True),    # a sale pays
    ([seg(i, imp=30.28, load=1.0) for i in range(8)], 12.0, False),         # dear all day, nothing to buy at
])
def test_the_executors_call_is_the_first_decision_of_the_plan(segs, soc, arbitrage):
    lim = (lambda sg: limits_for(sg, arbitrage=True)) if arbitrage else limits_for
    vr = solve(segs, soc, lim=lim)
    got = V.decide(vr, segs[0].start, soc, vr.limits[0], FACTS, NO_COMFORT, None)
    assert got is not None and got["mode"] == vr.timeline[0].mode
    run = got["mode"] in (CHARGE, EXPORT)
    assert (got["stop"] is not None) == run
    if run:                                                                 # the level the plan's first item ends at
        assert got["stop"] == pytest.approx(vr.timeline[0].level_end, abs=0.7)


def test_the_executor_steps_the_same_mode_as_the_plans_first_item():
    segs = night_and_day()
    vr = solve(segs, 12.0)
    ln = V.lines(vr, segs[0].start, 12.0, 6.99, 15.0, FACTS, NO_COMFORT)
    obs = Observation(now=segs[0].start, level_reported=12.0, level_filtered=12.0, net_load_kw=0.5,
                      car_charging=False, data_ok=True)
    ms, dec, _, changed = Executor(NO_COMFORT).step(segs[0].start, obs, vr.limits[0], ln, vr, (), FACTS)
    assert changed and ms.mode == vr.timeline[0].mode == CHARGE
    assert ms.target_soc == pytest.approx(vr.timeline[0].level_end, abs=0.7)
    assert dec.target_soc == ms.target_soc


def test_the_executor_calls_the_one_decision_function():
    assert execute.decide is V.decide


# ---- a run goes to its level, across segments ----------------------------------------------------------------
def test_a_charge_runs_to_its_level_across_segments_as_one_stretch():
    segs = night_and_day()
    vr = solve(segs, 12.0)
    first = vr.timeline[0]
    assert first.mode == CHARGE and first.start == t(0)
    assert first.end - first.start > timedelta(minutes=30)                  # more than one segment, one item
    assert first.until == f"until {first.level_end:.0f}%"
    assert vr.timeline[1].start == first.end and vr.timeline[1].mode != CHARGE     # then it rests, not another charge
    # and the executor, a segment later, goes on to the same level rather than deciding afresh
    got = V.decide(vr, t(1) + timedelta(minutes=10), 30.0, vr.limits[1], FACTS, NO_COMFORT, CHARGE)
    assert got["mode"] == CHARGE and got["stop"] == pytest.approx(first.level_end, abs=0.7)
    assert got["until"] >= t(3).timestamp()                                 # the decision stands across segments


def _walk_records(segs, soc, kind="mid"):
    core = V._backward(fc_of(segs), FACTS, NO_COMFORT, limits_for, segs[0].start, V.LEVEL_STEP_KWH,
                       V.COARSE_STEP_KWH)
    recs, _ = V._forward(core, soc / 100 * FACTS.capacity_kwh, kind)
    return recs


def test_a_run_that_reaches_its_level_part_way_through_a_segment_is_followed_by_the_next_decision_there():
    segs = night_and_day()
    recs = _walk_records(segs, 12.0)
    multi = [r for r in recs if len(r["pieces"]) > 1]
    assert multi, "the charge stops inside a segment, so that segment has more than one piece"
    r = multi[0]
    modes = [p[0] for p in r["pieces"]]
    assert modes[0] == CHARGE and modes[1] in (HOLD, SELF_USE)
    pieces = r["pieces"]
    assert pieces[0][2] == pytest.approx(pieces[1][1])                      # the pieces follow on, with no gap
    assert pieces[0][1] == 0.0 and pieces[-1][2] == 1.0                     # and fill the segment
    assert pieces[0][4] == pytest.approx(pieces[1][3])                      # the level carries on from one to the next
    assert len(pieces) <= V.MAX_PIECES
    # the record keeps no old keys
    assert not {"f", "e_full", "e_hold", "rest", "corridor"} & set(r)
    # the timeline shows the charge and the rest that follows it in the same half-hour
    vr = solve(segs, 12.0)
    assert vr.timeline[0].mode == CHARGE and vr.timeline[1].start == vr.timeline[0].end
    assert vr.timeline[0].end < t(vr_index_of(vr.timeline[0].end) + 1)


def vr_index_of(when):
    return int((when - T0).total_seconds() // 1800)


def test_idle_time_is_decided_again_in_each_segment_and_a_run_ends_by_its_level_not_the_clock():
    recs = _walk_records(night_and_day(), 12.0)
    first_idle = next(i for i, r in enumerate(recs) if r["pieces"][0][0] != CHARGE)
    assert first_idle >= 2                                                  # the charge took several segments
    assert all(r["pieces"][0][0] == CHARGE for r in recs[:first_idle])


# ---- the live rates --------------------------------------------------------------------------------------------
def test_the_live_import_rate_prices_the_current_segment():
    segs = [seg(i, imp=30.28, load=1.0) for i in range(8)]                  # the forecast says 30p all day
    vr = solve(segs, 12.0)
    lim = vr.limits[0]
    plan = V.decide(vr, t(0), 12.0, lim, FACTS, NO_COMFORT, None)
    assert plan["mode"] != CHARGE                                           # nothing to gain buying at 30p for 30p
    live = V.decide(vr, t(0), 12.0, lim, FACTS, NO_COMFORT, None, import_p=5.0, export_p=15.0)
    assert live["mode"] == CHARGE and live["stop"] > 12.0                   # but the live rate is 5p: buy now
    again = V.decide(vr, t(0), 12.0, lim, FACTS, NO_COMFORT, None, import_p=30.28, export_p=15.0)
    assert again["mode"] == plan["mode"]


def test_the_live_rate_replaces_the_slot_odds_for_the_segment_now():
    sg = replace(seg(0, imp=30.28, load=1.0), slot_prob=0.7, slot_import_p=6.99)
    segs = [sg] + [seg(i, imp=30.28, load=1.0) for i in range(1, 8)]
    vr = solve(segs, 12.0)
    # the live rate is the dear one: the slot is not running, so it is not counted on whatever the odds say
    dear = V.decide(vr, t(0), 12.0, vr.limits[0], FACTS, NO_COMFORT, None, import_p=30.28, export_p=15.0)
    assert dear["mode"] != CHARGE
    # the live rate is the slot's: certain now
    slot = V.decide(vr, t(0), 12.0, vr.limits[0], FACTS, NO_COMFORT, None, import_p=6.99, export_p=15.0)
    assert slot["mode"] == CHARGE


# ---- a moment left of the segment ------------------------------------------------------------------------------
def test_under_a_minute_left_of_a_segment_it_decides_for_the_next_one():
    segs = cheap_then_peak(3, 6)                                            # cheap to 01:30, dear after
    vr = solve(segs, 12.0)
    lim = vr.limits[2]
    early = V.decide(vr, t(3) - timedelta(seconds=90), 12.0, lim, FACTS, NO_COMFORT, None)
    assert early["mode"] == CHARGE and early["until"] <= t(3).timestamp()   # still the cheap segment's call
    sliver = V.decide(vr, t(3) - timedelta(seconds=30), 12.0, lim, FACTS, NO_COMFORT, None)
    assert sliver["mode"] != CHARGE                                         # the next (dear) segment's call
    assert sliver["until"] == t(4).timestamp()                              # standing to the end of that segment
    assert sliver == V.decide(vr, t(3), 12.0, lim, FACTS, NO_COMFORT, None)
    assert V.SLIVER_S == 60


def test_decide_says_nothing_outside_the_forecast_or_without_curves():
    segs = cheap_then_peak(3, 6)
    vr = solve(segs, 12.0)
    assert V.decide(vr, t(0) - timedelta(minutes=1), 12.0, vr.limits[0], FACTS, NO_COMFORT, None) is None
    assert V.decide(vr, t(9), 12.0, vr.limits[0], FACTS, NO_COMFORT, None) is None
    assert V.decide(replace(vr, vk=()), t(0), 12.0, vr.limits[0], FACTS, NO_COMFORT, None) is None


# ---- a run that reached its level starts again only to go further ------------------------------------------------
def test_a_run_that_just_reached_its_level_is_not_started_again_in_the_same_segment():
    segs = night_and_day()
    vr = solve(segs, 12.0)
    stop = vr.timeline[0].level_end
    now = vr.timeline[0].end - timedelta(minutes=1)
    got = V.decide(vr, now, stop, vr.limits[0], FACTS, NO_COMFORT, CHARGE, used=1, last_run=CHARGE)
    assert got["mode"] != CHARGE
    # a point and more further to go: it may start again
    more = V.decide(vr, now, stop - 2 * V.RESTART_PCT, vr.limits[0], FACTS, NO_COMFORT, CHARGE, used=1, last_run=CHARGE)
    assert more["mode"] == CHARGE


# ---- a charge keeps a minimum length between the level bands --------------------------------------------------------
def test_a_leg_runs_its_length_in_the_executors_call_too():
    bands = replace(NO_COMFORT, range_low_soc=70, range_high_soc=90, range_firmness="firm", min_run_pct=20)
    segs = [seg(i, imp=30.28) for i in range(8)]                            # dear all day: nothing is worth charging
    vr = solve(segs, 62.0, settings=bands, running=CHARGE, leg_from=60.0)
    going = V.decide(vr, t(0), 62.0, vr.limits[0], FACTS, bands, CHARGE, leg_from=60.0)
    assert going["mode"] == CHARGE and going["stop"] == pytest.approx(80.0, abs=1.0)        # 60% and the 20-point span
    assert V.decide(vr, t(0), 62.0, vr.limits[0], FACTS, bands, None)["mode"] != CHARGE     # not running: no leg
    done = V.decide(vr, t(0), 82.0, vr.limits[0], FACTS, bands, CHARGE, leg_from=60.0)
    assert done["mode"] != CHARGE                                                          # its length is run


# ---- retired settings --------------------------------------------------------------------------------------------
RETIRED_NOW = ("executor", "price_band_p", "level_band_pct", "min_dwell_s", "reversal_levy_p")


def test_the_retired_settings_in_a_saved_block_are_ignored_without_error():
    assert set(RETIRED_NOW) <= RETIRED
    assert not set(RETIRED_NOW) & set(SETTINGS)
    saved = {"executor": "plan", "price_band_p": 1.5, "level_band_pct": 2.0, "min_dwell_s": 120,
             "reversal_levy_p": 3.0, "reserve_soc": 15}
    got = parse_v2(saved)
    assert got == parse_v2({"reserve_soc": 15}) and got.reserve_soc == 15
    for key in RETIRED_NOW:
        assert not hasattr(got, key)
        with pytest.raises(TypeError):
            V2Settings(**{key: 1})
    assert not {r["key"] for r in catalogue()["settings"]} & set(RETIRED_NOW)


def test_the_retired_settings_in_a_whole_config_are_dropped_too():
    raw = {"operation": {"mode": "passive"}, "system": {"engine": "v2", "control_method": "ram_remote"},
           "engine_v2": {"executor": "lines", "min_dwell_s": 60, "reserve_soc": 20},
           "v2_profiles": {"mine": {"price_band_p": 1.0, "reversal_levy_p": 2.0, "switch_cost_p": 2}}}
    cfg = parse_config(raw)
    assert cfg.engine_v2.reserve_soc == 20 and cfg.v2_profiles == {"mine": {"switch_cost_p": 2}}
    assert not any(hasattr(cfg.engine_v2, k) for k in RETIRED_NOW)
