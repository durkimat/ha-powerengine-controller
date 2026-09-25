from datetime import datetime, timedelta, timezone

import pytest
from fixtures import BST, CONFIG, NOW, STATES, get_state

from pe_core.forecast import (
    DEFAULT_LOAD_W,
    LoadProfile,
    build_load_profile,
    build_slots,
    half_hour_means,
    parse_history,
    slot_start,
)
from pe_core.readings import Window, read

UTC = timezone.utc


def test_slot_start_aligns():
    assert slot_start(datetime(2026, 9, 22, 17, 58, tzinfo=BST)) == datetime(2026, 9, 22, 16, 30, tzinfo=UTC)


def test_half_hour_means_time_weighted():
    t = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    samples = [(t, 1000.0), (t + timedelta(minutes=15), 3000.0)]
    means = half_hour_means(samples, t + timedelta(minutes=30))
    assert means[t] == pytest.approx(2000.0)


def test_half_hour_means_needs_coverage():
    t = datetime(2026, 9, 22, 10, 25, tzinfo=UTC)
    assert half_hour_means([(t, 500.0)], t + timedelta(minutes=5)) == {}


def test_load_profile_subtracts_car_and_weights_recent_days():
    now = datetime(2026, 9, 22, 0, 0, tzinfo=UTC)
    house, car = [], []
    # 14 weekdays of 1 kW at 18:00-18:30 UTC, but a 7 kW car on the most recent day
    for d in range(1, 15):
        t = now - timedelta(days=d) + timedelta(hours=18)
        house += [(t, 1000.0), (t + timedelta(minutes=30), 0.0)]
    t = now - timedelta(days=1) + timedelta(hours=18)
    house += [(t, 8000.0)]
    car += [(t, 7000.0), (t + timedelta(minutes=30), 0.0)]
    prof = build_load_profile(house, car, now, UTC, subtract_car=True)
    probe = now + timedelta(hours=18)
    key_w = prof.expected_w(probe, UTC)
    assert 900 < key_w < 1100          # car removed; ~1 kW house
    assert prof.days > 1


def test_profile_falls_back():
    assert LoadProfile().expected_w(NOW, BST) == DEFAULT_LOAD_W


def test_parse_history_handles_units_and_junk():
    rows = [{"state": "1.5", "last_changed": "2026-09-22T10:00:00+00:00", "attributes": {"unit_of_measurement": "kW"}},
            {"state": "unavailable", "last_changed": "2026-09-22T10:01:00+00:00"}]
    assert parse_history(rows) == [(datetime(2026, 9, 22, 10, 0, tzinfo=UTC), 1500.0)]


def test_build_slots_from_readings():
    r = read(CONFIG, get_state(), NOW)
    r.axle_start = slot_start(NOW) + timedelta(hours=2)       # aligned 2-hour event = 4 slots
    r.axle_end = slot_start(NOW) + timedelta(hours=4)
    solar = [{"period_start": (slot_start(NOW) + timedelta(minutes=30 * i)).isoformat(), "pv_estimate": 2.0}
             for i in range(4)]
    slots = build_slots(r, solar, None, BST)
    assert slots[0].start == slot_start(NOW)
    assert 24 * 2 <= len(slots) <= 48 * 2
    assert slots[1].solar_kwh == 1.0
    assert sum(s.axle for s in slots) == 4
    assert any(s.smart_slot for s in slots)
    known_end = max(w.end for w in r.rates)
    assert all(not s.price_estimated for s in slots if s.start < known_end)


def test_prices_beyond_published_are_estimated_from_yesterday():
    r = read(CONFIG, get_state(), NOW)
    r.rates = [Window(slot_start(NOW) + timedelta(minutes=30 * i), slot_start(NOW) + timedelta(minutes=30 * (i + 1)),
                      0.1 + i / 1000) for i in range(4)]
    slots = build_slots(r, [], None, BST, min_h=26)
    later = [s for s in slots if s.price_estimated]
    assert later and later[48 - 4].price == pytest.approx(0.1)   # 24 h after the first known slot


def test_unused_imports():
    assert STATES


def test_half_hour_means_with_no_samples():
    assert half_hour_means([], datetime(2026, 9, 22, 10, 0, tzinfo=UTC)) == {}


def test_load_profile_with_no_car_history():
    now = datetime(2026, 9, 22, 0, 0, tzinfo=UTC)
    house = [(now - timedelta(days=1, hours=-18), 900.0), (now - timedelta(days=1, hours=-19), 900.0)]
    prof = build_load_profile(house, [], now, UTC)          # car never charged: must not crash
    assert prof.watts
