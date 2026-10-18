"""The highest-price rule steps aside, for as long as it takes, before an announced grid event (eventcharge.py)."""
from dataclasses import replace
from datetime import timedelta

from test_engine_v2_value import FACTS, seg, t

from pe_core.engine_v2 import eventcharge, rules
from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.types import SELF_USE, Forecast

EVENT_AT = 34                                         # 17:00 on the test day: segment 34


def _fc(event_len=2, n=44):
    ev = {"event": True, "event_p": 115.0}
    segs = [seg(i, imp=28.84, load=0.5, **(ev if EVENT_AT <= i < EVENT_AT + event_len else {})) for i in range(n)]
    return Forecast(made_at=segs[0].start, segments=tuple(segs))


def _path(start_pct, now, per_hour=-3.0, n=96):
    return {"start": now.isoformat(), "step_min": 15, "mid": [round(start_pct + per_hour * i / 4, 1) for i in range(n)]}


def _wins(level, event_len=2, now_seg=26, per_hour=-3.0):
    now = t(now_seg)
    return eventcharge.windows(_fc(event_len), _path(level, now, per_hour), now, FACTS, 12.0, 100.0), now


def test_an_hour_long_event_needs_about_five_kwh_from_the_battery():
    fc = _fc(2)
    kwh = eventcharge.event_draw_kwh(fc.segments, EVENT_AT, EVENT_AT + 1, FACTS)
    assert 4.5 < kwh < 6.5


def test_nothing_changes_when_the_plan_already_reaches_the_level():
    wins, _ = _wins(95.0, per_hour=-1.0)
    assert wins == {}


def test_the_window_opens_just_before_the_latest_safe_start_not_when_the_event_is_announced():
    wins, now = _wins(40.0)                           # announced at 13:00 for 17:00, 40% falling 3 points an hour
    ((start, target),) = wins.values()
    assert 40 < target < 60                          # the level the event needs: floor + its draw + the margin
    assert t(EVENT_AT) in wins
    assert start > now + timedelta(hours=2)           # not hours early
    assert start < t(EVENT_AT) - timedelta(minutes=30)


def test_a_longer_event_opens_the_window_earlier():
    short, _ = _wins(40.0, event_len=2)
    long, _ = _wins(40.0, event_len=4)
    assert long[t(EVENT_AT)][0] < short[t(EVENT_AT)][0]
    assert long[t(EVENT_AT)][1] > short[t(EVENT_AT)][1]


def test_too_little_time_means_starting_now():
    wins, now = _wins(15.0, event_len=4, now_seg=31, per_hour=0.0)     # announced 90 min ahead, battery nearly empty
    assert wins[t(EVENT_AT)][0] == now


def test_an_event_that_has_started_or_is_over_gets_no_window():
    now = t(EVENT_AT + 1)
    assert eventcharge.windows(_fc(2), _path(30.0, now), now, FACTS, 12.0, 100.0) == {}


def test_a_window_that_has_opened_stays_open():
    now = t(EVENT_AT - 3)
    held = {t(EVENT_AT): (t(EVENT_AT - 4), 50.0)}     # opened half an hour ago
    assert eventcharge.latch({}, held, now) == held                       # the plan reaches the level: charge finishes
    later = {t(EVENT_AT): (t(EVENT_AT - 2), 48.0)}
    assert eventcharge.latch(later, held, now)[t(EVENT_AT)] == (t(EVENT_AT - 4), 50.0)
    assert eventcharge.latch({}, held, t(EVENT_AT)) == {}                 # the event has begun: dropped


def test_only_the_segments_inside_the_window_are_marked():
    fc = _fc(2)
    marked = eventcharge.mark(fc, {t(EVENT_AT): (t(EVENT_AT - 3), 50.0)})
    flags = [sg.event_charge_to for sg in marked.segments]
    assert [i for i, f in enumerate(flags) if f is not None] == [EVENT_AT - 3, EVENT_AT - 2, EVENT_AT - 1]
    assert flags[EVENT_AT - 1] == 50.0


def test_the_highest_price_rule_lets_a_charge_through_only_where_marked():
    st = V2Settings(charge_max_import_p=15.0)
    plain = seg(10, imp=28.84)
    assert SELF_USE in rules.limits_for(plain, FACTS, st).allowed
    assert "charge" not in rules.limits_for(plain, FACTS, st).allowed
    marked = rules.limits_for(replace(plain, event_charge_to=50.0), FACTS, st)
    assert "charge" in marked.allowed and marked.ceiling_soc == 50.0      # and only up to the level the event needs


def test_a_saved_late_events_setting_is_ignored_not_an_error():
    from pe_core.engine_v2.settings import parse_v2
    saved = {"late_events": True, "late_events_per_week": 3, "late_event_hours": 2, "reserve_soc": 15}
    s = parse_v2(saved, {}, {}, None)
    assert s.reserve_soc == 15 and not hasattr(s, "late_events")


def test_settings_retired_in_0_9_156_are_ignored_not_an_error():
    from pe_core.engine_v2.settings import RETIRED, SETTINGS, parse_v2
    gone = ("replan_on_level", "terminal_value", "terminal_value_p", "reentry_levy_p", "corridor_cost_p",
            "max_segment_min", "forecast_change_pct", "drift_kwh", "band_exit_min", "revalue_coalesce_s",
            "max_value_age_min", "deadline_grace_min", "debounce_s", "level_step_kwh")
    assert set(gone) <= RETIRED and not set(gone) & set(SETTINGS)
    s = parse_v2({**{k: 1 for k in gone}, "reserve_soc": 15})
    assert s.reserve_soc == 15 and not any(hasattr(s, k) for k in gone)
