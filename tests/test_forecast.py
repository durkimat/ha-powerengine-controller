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


def test_car_finished_mid_dispatch_frees_the_rest_of_it():
    from dataclasses import replace

    from pe_core.optimiser import optimise
    from pe_core.planner import Params
    r = read(CONFIG, get_state(), NOW)
    s0 = slot_start(NOW)
    r.dispatches = [Window(s0 - timedelta(hours=1), s0 + timedelta(hours=4)),       # running now
                    Window(s0 + timedelta(hours=20), s0 + timedelta(hours=22))]     # a later one
    r.ev_plug = "Charging"
    assert all(s.car_expected for s in build_slots(r, [], None, BST) if s.smart_slot)
    r.ev_plug = "EV Connected"                     # plugged in, not charging: finished
    r.ev_status = "Paused"                         # (charger not reporting the charge complete)
    slots = build_slots(r, [], None, BST)
    now_slots = [s for s in slots if s.smart_slot and s.start < s0 + timedelta(hours=4)]
    later = [s for s in slots if s.smart_slot and s.start >= s0 + timedelta(hours=20)]
    assert now_slots and not any(s.car_expected for s in now_slots) and all(s.car_kw == 0 for s in now_slots)
    assert later and all(s.car_expected for s in later)
    r.ev_status = "Completed"                      # the charger says the car is full: later slots are free too
    slots = build_slots(r, [], None, BST)
    assert not any(s.car_expected for s in slots if s.smart_slot)
    r.ev_status = "Paused"
    r.ev_plug = "EV Disconnected"
    assert not any(s.car_expected for s in build_slots(r, [], None, BST) if s.smart_slot)
    # the optimiser may sell in a smart slot the car has finished with
    cheap = [replace(s, price=0.0699, export=0.15, load_kwh=0.2, solar_kwh=0.0) for s in slots[:12]]
    free = [replace(s, smart_slot=True, car_expected=False) for s in cheap[:6]] + \
        [replace(s, price=0.3028) for s in cheap[6:]]
    res = optimise(free, 90.0, Params(arbitrage=True))
    assert "export" in res["actions"][:6]                # 15p out, refilled at 6.99p: worth it
    from pe_core.optimiser import _actions
    assert "export" in _actions(free[0], Params(arbitrage=True))
    busy = replace(free[0], car_expected=True)
    assert "export" not in _actions(busy, Params(arbitrage=True))


def test_overnight_car_slot_stays_in_the_band_until_the_final_top_up():
    from dataclasses import replace

    from pe_core.optimiser import slot_target
    from pe_core.planner import Params
    r = read(CONFIG, get_state(), NOW)
    s = replace(build_slots(r, [], None, BST)[0], smart_slot=True, price=0.0699, car_expected=True)
    p = Params(arbitrage=True)
    assert slot_target(s, p) == 90
    assert slot_target(replace(s, overnight=True), p) == 90                  # within the band overnight too
    assert slot_target(replace(s, overnight=True), p, final=True) == 100      # the final top-up before morning


def test_smart_slot_inside_the_overnight_window_is_not_discounted():
    from pe_core.certainty import Certainty
    r = read(CONFIG, get_state(), NOW)
    s0 = slot_start(NOW)
    r.dispatches = [Window(s0 + timedelta(hours=1), s0 + timedelta(hours=6))]
    cert = Certainty({})
    slots = build_slots(r, [], None, BST, certainty=cert, overnight=set(range(48)))     # all overnight
    smart = [s for s in slots if s.smart_slot]
    assert smart and all(s.slot_price is None and s.certainty is None for s in smart)
    slots = build_slots(r, [], None, BST, certainty=cert, overnight=set())
    assert all(s.certainty is not None for s in slots if s.smart_slot)


def test_equal_prices_charge_sooner_rather_than_at_the_end():
    from pe_core.forecast import Slot
    from pe_core.optimiser import optimise
    from pe_core.planner import Params
    s0 = slot_start(NOW)
    slots = [Slot(s0 + timedelta(minutes=30 * i), 0.0699 if i < 12 else 0.3028, 0.15, load_kwh=0.2,
                  overnight=i < 12) for i in range(20)]
    res = optimise(slots, 40.0, Params())
    first_charge = res["actions"].index("grid_charge")
    assert first_charge <= 1 and res["soc"][11] >= 99


def test_rest_of_a_running_dispatch_is_counted_at_its_price():
    from pe_core.certainty import Certainty
    r = read(CONFIG, get_state(), NOW)
    s0 = slot_start(NOW)
    r.dispatches = [Window(s0 - timedelta(minutes=30), s0 + timedelta(hours=2)),        # running now
                    Window(s0 + timedelta(hours=5), s0 + timedelta(hours=6))]           # not started
    slots = build_slots(r, [], None, BST, certainty=Certainty({}), overnight=set())
    running = [s for s in slots if s.smart_slot and s.start < s0 + timedelta(hours=2)]
    later = [s for s in slots if s.smart_slot and s.start >= s0 + timedelta(hours=5)]
    assert running and all(s.certainty is None for s in running)
    assert later and all(s.certainty is not None for s in later)


def test_idle_car_means_smart_slots_are_just_cheap_time():
    import dataclasses
    from datetime import timedelta

    from pe_core.forecast import build_slots
    from pe_core.readings import Readings, Window
    now = datetime(2026, 9, 28, 7, 41, tzinfo=timezone.utc)
    disp = [Window(now + timedelta(minutes=19), now + timedelta(hours=7), -45.5)]
    base = Readings(now=now, battery_soc=90, import_rate=0.30, export_rate=0.15, ev_plug="Waiting for EV",
                    ev_power=0, dispatches=disp)
    slots = build_slots(base, None, None, timezone.utc)
    assert any(s.smart_slot and s.car_expected for s in slots)
    idle = build_slots(dataclasses.replace(base, car_idle=True), None, None, timezone.utc)
    assert all(not s.car_expected for s in idle if s.smart_slot)


def _smart_slots(**kw):
    r = read(CONFIG, get_state(), NOW)
    return r, build_slots(r, [], None, BST, **kw)


def test_whole_house_on_leaves_smart_slot_prices_alone():
    _, base = _smart_slots()
    _, explicit = _smart_slots(whole_house=True)
    assert [(s.price, s.certainty, s.slot_price) for s in base] == [(s.price, s.certainty, s.slot_price)
                                                                     for s in explicit]


def test_whole_house_off_prices_smart_slots_at_the_standard_rate():
    r, on = _smart_slots()
    _, off = _smart_slots(whole_house=False)
    peak = max(w.value for w in r.rates)
    smart_off = [s for s in off if s.smart_slot]
    assert smart_off and all(s.price == pytest.approx(peak) for s in smart_off)
    assert [s.price for s in off if not s.smart_slot] == [s.price for s in on if not s.smart_slot]
    assert any(s.smart_slot and s.price < peak for s in on)              # (the cheap early-morning slots)
    assert all(s.car_expected == o.car_expected and s.car_kw == o.car_kw for s, o in zip(off, on, strict=True))


def test_whole_house_off_keeps_the_fixed_overnight_window_and_skips_certainty():
    class Sure:
        def score(self, *a, **k):
            return 0.5
    r = read(CONFIG, get_state(), NOW)
    tods = set(range(0, 10))                                             # half-hours 00:00-05:00 local
    weighted = build_slots(r, [], None, BST, certainty=Sure(), overnight=tods)
    off = build_slots(r, [], None, BST, certainty=Sure(), overnight=tods, whole_house=False)
    night = [s for s in off if s.smart_slot and s.overnight]
    assert night and all(s.price == 0.06993 for s in night)              # unchanged inside the fixed window
    day = [s for s in off if s.smart_slot and not s.overnight]
    assert day and all(s.certainty is None and s.slot_price is None for s in day)      # no weighting off-window
    assert any(s.smart_slot and not s.overnight and s.certainty == 0.5 for s in weighted)
