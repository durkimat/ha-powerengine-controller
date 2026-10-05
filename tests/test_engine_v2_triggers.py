"""Engine v2: when to re-check and when to revalue (docs/plans/engine-v2.md section 10)."""

import json
from datetime import timedelta, timezone

from test_engine_v2_observe import T0

from pe_core.engine_v2.settings import V2Settings
from pe_core.engine_v2.triggers import RECENT, Triggers
from pe_core.engine_v2.types import Event


def ev(kind, sec=0, text=None):
    return Event(T0 + timedelta(seconds=sec), kind, text or f"{kind} happened")


def at(sec):
    return T0 + timedelta(seconds=sec)


def test_an_urgent_event_revalues_at_once_and_says_why():
    t = Triggers(V2Settings())
    because = t.due(at(0), (ev("car_start"),), at(-60))
    assert because == "The car started charging"


def test_a_non_urgent_event_waits_for_the_batch_window_then_runs_once():
    t = Triggers(V2Settings(revalue_coalesce_s=10))
    assert t.due(at(0), (ev("prices_published"),), at(-60)) is None
    assert t.due(at(5), (ev("slots_changed", 5),), at(-60)) is None
    because = t.due(at(11), (), at(-60))
    assert because == "New prices published and Smart slots changed"
    assert t.due(at(30), (), at(-60)) is None                     # consumed


def test_an_urgent_event_takes_the_pending_ones_with_it():
    t = Triggers(V2Settings())
    t.due(at(0), (ev("drift"),), at(-60))
    because = t.due(at(3), (ev("override", 3),), at(-60))
    assert "manual override" in because and "drifted" in because


def test_events_that_do_not_revalue_never_trigger_one():
    t = Triggers(V2Settings())
    for kind in ("level", "price", "slot_start", "slot_end", "sun_to_short", "short_to_sun", "bms",
                 "mode_switch", "reserve", "data_missing"):
        assert t.due(at(0), (ev(kind),), at(-60)) is None
    assert t.due(at(100), (), at(-60)) is None


def test_more_than_two_reasons_are_counted_in_the_text():
    t = Triggers(V2Settings(revalue_coalesce_s=0))
    because = t.due(at(0), (ev("drift"), ev("forecast_update"), ev("slots_changed")), at(-60))
    assert because.endswith("(and 1 more)")


def test_the_backstop_runs_after_the_maximum_age_and_is_counted():
    t = Triggers(V2Settings(max_value_age_min=120))
    assert t.due(at(119 * 60), (), at(0)) is None
    because = t.due(at(120 * 60), (), at(0))
    assert because == "The backstop timer"
    t.note(at(120 * 60), (), True, False, 1.5)
    h = t.health(at(120 * 60))
    assert h["backstop"] == 1 and h["causes"] == {"backstop": 1} and h["revalues"] == 1


def test_no_backstop_without_a_first_value_time():
    assert Triggers(V2Settings()).due(at(10 ** 6), (), None) is None


def test_day_counts_and_recent_events():
    t = Triggers(V2Settings(revalue_coalesce_s=0))
    events = (ev("prices_published", 0), ev("price", 0))
    because = t.due(at(0), events, at(-60))
    assert because
    t.note(at(0), events, True, True, 2.4, flip_flop=True)
    t.note(at(30), (ev("deadline", 30),), False, False, None)
    h = t.health(at(40))
    assert h["revalues"] == 1 and h["mode_changes"] == 1 and h["flip_flops"] == 1 and h["deadlines_missed"] == 1
    assert h["causes"] == {"prices_published": 1} and h["longest_calc_s"] == 2.4
    effects = [(r["kind"], r["effect"]) for r in t.recent]
    assert effects == [("prices_published", "mode_change"), ("price", "recheck"), ("deadline", "revalue")]
    assert all(set(r) == {"at", "kind", "text", "effect"} for r in t.recent)


def test_a_mode_change_marks_the_most_urgent_event_of_the_tick():
    t = Triggers(V2Settings())
    t.note(at(0), (ev("price"), ev("car_start")), False, True, None)
    assert [r["effect"] for r in t.recent] == ["recheck", "mode_change"]


def test_recent_keeps_the_last_thirty_and_text_is_trimmed():
    t = Triggers(V2Settings())
    for i in range(50):
        t.note(at(i), (ev("price", i, "x" * 400),), False, False, None)
    assert len(t.recent) == RECENT and all(len(r["text"]) <= 120 for r in t.recent)
    assert t.recent[-1]["at"].endswith("+00:00")


def test_counts_start_again_on_a_new_local_day():
    t = Triggers(V2Settings())
    t.tz = timezone(timedelta(hours=1))
    t.note(at(0), (), True, True, 1.0)
    assert t.health(at(0))["revalues"] == 1
    assert t.health(at(24 * 3600))["revalues"] == 0


def test_a_revalue_the_engine_asked_for_is_counted_under_its_own_kind():
    t = Triggers(V2Settings())
    assert t.cause("retry", "Trying again") == "Trying again"
    t.note(at(0), (), True, False, 1.0)
    assert t.health(at(0))["causes"] == {"retry": 1}


def test_state_is_json_safe_and_restores_the_pending_batch():
    t = Triggers(V2Settings(revalue_coalesce_s=10))
    t.due(at(0), (ev("drift"),), at(-60))
    t.note(at(0), (ev("drift"),), False, False, None)
    t2 = Triggers(V2Settings(revalue_coalesce_s=10), json.loads(json.dumps(t.state())))
    assert t2.due(at(11), (), at(-60)) == "The house drifted from the forecast"
    assert len(t2.recent) == 1
