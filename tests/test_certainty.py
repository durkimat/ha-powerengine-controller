from datetime import datetime, timedelta, timezone

import pytest

from pe_core.certainty import PRIOR, Certainty, expected_price, group, outcome
from pe_core.forecast import build_slots

UTC = timezone.utc
T = datetime(2026, 9, 20, 1, 0, tzinfo=UTC)


def rec(start, status, car=0.0, confirmed=False, seen_h=5, run=None):
    """`car` is kWh drawn; the unbroken charge defaults to 10 minutes per kWh (a real charge) unless `run` is given."""
    run = car * 10 if run is None else run
    return {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(), "status": status,
            "car_kwh": car, "charging_min": run, "longest_min": run, "confirmed": confirmed,
            "first_seen": (start - timedelta(hours=seen_h)).isoformat()}


def test_outcomes():
    assert outcome(rec(T, "done", car=3)) == 1
    assert outcome(rec(T, "done", confirmed=True)) == 1
    assert outcome(rec(T, "done")) == 0
    assert outcome(rec(T, "done", car=0.01, run=1.2)) == 0            # a blip: the car woke for a moment
    assert outcome(rec(T, "done", car=0.4, run=2.0)) == 1             # the shortest charge that counts
    assert outcome(rec(T, "done", car=0.4, run=3.0), min_charge_min=4.0) == 0     # a stricter setting
    assert outcome({**rec(T, "done"), "charging_min": 5.0, "longest_min": None}) == 1   # old record: total time
    assert outcome(rec(T, "cut_short")) == 0.5
    assert outcome(rec(T, "cancelled")) == 0
    assert outcome(rec(T, "planned")) is None


def test_groups():
    assert group(T, T - timedelta(hours=5)) == ("overnight", "ahead")
    assert group(T.replace(hour=14), T.replace(hour=13)) == ("daytime", "short notice")


def test_prior_with_no_history():
    c = Certainty({})
    assert c.overall == PRIOR and c.score(T, None) == PRIOR


def test_learns_but_is_damped():
    hist = {str(i): rec(T + timedelta(days=i), "done", car=4) for i in range(8)}
    hist.update({f"d{i}": rec(T.replace(hour=14) + timedelta(days=i), "cancelled", seen_h=1) for i in range(8)})
    c = Certainty(hist)
    night = c.score(T + timedelta(days=30), T + timedelta(days=29))
    day_short = c.score(T.replace(hour=14) + timedelta(days=30), T.replace(hour=13, minute=30) + timedelta(days=30))
    assert 0.8 < night < 1 and 0 < day_short < 0.4
    assert c.summary()["slots"] == 16


def test_expected_price():
    assert expected_price(0.07, 0.30, 0.8) == pytest.approx(0.116)


def test_future_smart_slot_priced_by_certainty():
    from tests_support import readings_with_slot
    r, slot_at = readings_with_slot()
    hist = {str(i): rec(slot_at - timedelta(days=i + 1), "cancelled") for i in range(20)}
    slots = build_slots(r, [], None, UTC, certainty=Certainty(hist, UTC))
    s = next(s for s in slots if s.start == slot_at)
    assert s.smart_slot and s.certainty < 0.2
    assert s.slot_price == pytest.approx(0.07) and s.price > 0.25


def test_estimate_does_not_copy_yesterdays_smart_slot():
    from tests_support import readings_two_days
    r, when = readings_two_days()
    slots = build_slots(r, [], None, UTC, min_h=60, horizon_h=60)
    s = next(s for s in slots if s.start == when + timedelta(days=1))
    assert s.price_estimated and s.price == pytest.approx(0.30)


def test_relisted_running_slot_is_a_continuation_not_cut_short():
    from datetime import datetime, timezone

    from pe_core.readings import Window
    from pe_core.slots import SlotTracker
    t = datetime(2026, 9, 26, 17, 10, tzinfo=timezone.utc)
    tr = SlotTracker(None)
    w1 = Window(t, t.replace(hour=23), -30.0)
    tr.update(t, [w1], [], True, 7000, 60)
    later = t.replace(minute=31)
    w2 = Window(t.replace(minute=30), t.replace(hour=23), -28.0)
    tr.update(later, [w2], [], True, 7000, 60)
    rec = tr.slots[t.isoformat()]
    assert rec["status"] == "done" and rec["continued"] and rec["end"] == w2.start.isoformat()


def test_old_cut_short_records_that_carried_on_count_as_delivered():
    from pe_core.certainty import Certainty
    recs = {"a": {"start": "2026-09-26T18:10:00+01:00", "end": "2026-09-27T04:00:00+01:00", "status": "cut_short",
                  "ended": "2026-09-26T17:31:27+00:00", "car_kwh": 2.4, "charging_min": 20.0, "confirmed": False},
            "b": {"start": "2026-09-26T18:30:00+01:00", "end": "2026-09-27T04:00:00+01:00", "status": "planned",
                  "car_kwh": 0.0, "confirmed": False}}
    c = Certainty(recs)
    assert c.total == 1.0 and c.n == 1


def test_plan_shows_the_tariff_price_not_the_weighted_one():
    from datetime import datetime, timedelta, timezone

    from pe_core.forecast import Slot
    from pe_core.planner import Params, make_plan, plan_entity_states
    t0 = datetime(2026, 9, 26, 22, 0, tzinfo=timezone.utc)
    slots = [Slot(t0 + timedelta(minutes=30 * i), 0.097 if i < 2 else 0.3028, 0.15, load_kwh=0.3,
                  smart_slot=i < 2, certainty=0.88 if i < 2 else None, slot_price=0.0699 if i < 2 else None)
             for i in range(8)]
    plan = make_plan(slots, 50.0, Params(), t0)
    attrs = plan_entity_states(plan)["plan"][1]
    assert attrs["series"]["price_p"][:2] == [6.99, 6.99]
    text = " ".join(w["price"] + " " + w["reason"] for w in attrs["windows"])
    assert "9.7p" not in text


def run_rec(start, halves, status="done", ran=None, **kw):
    """A window of `halves` half-hours; a cut-short one stopped after `ran` half-hours."""
    r = {**rec(start, status, **kw), "end": (start + timedelta(minutes=30 * halves)).isoformat()}
    if ran is not None:
        r["ended"] = (start + timedelta(minutes=30 * ran - 5)).isoformat()
    return r


def test_the_hold_chance_counts_the_later_half_hours_a_started_window_kept():
    c = Certainty({})
    assert c.hold() == PRIOR                                    # nothing known: the prior, like any other group
    hist = {"a": run_rec(T, 7),                                # ran its full 3.5 h: 6 later half-hours held of 6
            "b": run_rec(T + timedelta(days=1), 7, "cut_short", ran=3),     # three begun: 2 of 6 later held
            "c": run_rec(T + timedelta(days=2), 1),                         # one half-hour: no later ones
            "d": run_rec(T + timedelta(days=3), 5, "cancelled")}            # never started: not counted
    c = Certainty(hist)
    assert (c.held, c.later) == (8, 12)
    assert c.hold() == pytest.approx((8 + c.overall * 4) / (12 + 4), abs=1e-3)
    assert c.summary()["running"] == {"later_half_hours": 12, "certainty": c.hold()}


def test_a_window_that_was_re_listed_and_carried_on_held():
    a = run_rec(T, 4, "cut_short", ran=2)
    b = run_rec(T + timedelta(minutes=55), 4)                   # EDF listed it again from the current half-hour
    c = Certainty({"a": a, "b": b})
    assert c.held == 3 + 3 and c.later == 3 + 3                 # the cut one carried on, so it held in full


def test_many_windows_that_all_held_make_a_later_half_hour_near_certain():
    hist = {str(i): run_rec(T + timedelta(days=i), 6) for i in range(30)}
    assert Certainty(hist).hold() > 0.95
