from datetime import datetime, timedelta, timezone

import pytest

from pe_core.certainty import PRIOR, Certainty, expected_price, group, outcome
from pe_core.forecast import build_slots

UTC = timezone.utc
T = datetime(2026, 9, 20, 1, 0, tzinfo=UTC)


def rec(start, status, car=0.0, confirmed=False, seen_h=5):
    return {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(), "status": status,
            "car_kwh": car, "confirmed": confirmed, "first_seen": (start - timedelta(hours=seen_h)).isoformat()}


def test_outcomes():
    assert outcome(rec(T, "done", car=3)) == 1
    assert outcome(rec(T, "done", confirmed=True)) == 1
    assert outcome(rec(T, "done")) == 0
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
