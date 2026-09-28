import dataclasses
from datetime import datetime, timedelta, timezone

from fixtures import CONFIG, NOW, STATES, get_state

from pe_core import gridcheck
from pe_core.readings import read

T = datetime(2026, 9, 27, 22, 0, tzinfo=timezone.utc)


def test_battery_state_bands():
    assert gridcheck.battery_state(-4800) == "charging"
    assert gridcheck.battery_state(4700) == "discharging"
    assert gridcheck.battery_state(100) == "idle"
    assert gridcheck.battery_state(900) is None
    assert gridcheck.battery_state(None) is None


def test_disagreement_while_charging_is_reported():
    gc = gridcheck.GridCheck()
    for i in range(30):
        t = T + timedelta(minutes=i)
        gc.add("2026-09-27", t, -4850, 7070, 5500, t)          # charging: inverter meter reads high
        gc.add("2026-09-27", t, 4770, -4480, -4500, t)         # discharging: they agree
    s = gc.summary()
    assert s["charging"]["difference_w"] == 1570
    assert abs(s["discharging"]["difference_w"]) < 50
    assert "charging" in s["verdict"] and "disagree" in s["verdict"]
    assert "discharging:" not in s["verdict"]


def test_stale_reference_and_missing_values_are_skipped():
    gc = gridcheck.GridCheck()
    assert gc.add("d", T, -4000, 6000, 5000, T - timedelta(minutes=10)) is None
    assert gc.add("d", T, -4000, None, 5000, T) is None
    assert gc.add("d", T, 900, 6000, 5000, T) is None
    assert gc.summary()["verdict"].startswith("Collecting")


def test_old_days_are_dropped():
    gc = gridcheck.GridCheck()
    for d in ("2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27"):
        gc.add(d, T, 0, 500, 480, T)
    assert sorted(gc.days) == ["2026-09-25", "2026-09-26", "2026-09-27"]


def test_reference_meter_is_read_with_its_time():
    ref = {"grid_power_reference": {"entity": "sensor.myenergi_x_power_grid"}}
    cfg = dataclasses.replace(CONFIG, inputs={**CONFIG.inputs, **ref})
    states = {**STATES, "sensor.myenergi_x_power_grid": {"state": "5400", "attributes": {"unit_of_measurement": "W"},
                                                         "last_updated": "2026-09-27T21:59:30+00:00"}}
    r = read(cfg, get_state(states), NOW)
    assert r.grid_ref_power == 5400
    assert r.grid_ref_at == datetime(2026, 9, 27, 21, 59, 30, tzinfo=timezone.utc)
    assert read(CONFIG, get_state(), NOW).grid_ref_power is None      # unmapped: no cross-check


def _with_ref(features=None, ref_age_s=20, ref_w=5880):
    ref = {"grid_power_reference": {"entity": "sensor.myenergi_x_power_grid"}}
    cfg = dataclasses.replace(CONFIG, inputs={**CONFIG.inputs, **ref},
                              features={**CONFIG.features, **(features or {})})
    stamp = (NOW - timedelta(seconds=ref_age_s)).isoformat()
    states = {**STATES, "sensor.myenergi_x_power_grid": {"state": str(ref_w),
                                                         "attributes": {"unit_of_measurement": "W"},
                                                         "last_reported": stamp}}
    return read(cfg, get_state(states), NOW), read(CONFIG, get_state(), NOW)


def test_check_meter_replaces_grid_and_corrects_house():
    r, plain = _with_ref()
    assert r.grid_source == "check meter" and r.grid_power == 5880
    assert r.grid_power_inverter == plain.grid_power
    delta = plain.grid_power - 5880
    assert r.house_power_raw == max(0.0, plain.house_power_raw - delta)


def test_stale_check_meter_or_switched_off_leaves_the_inverter_meter():
    stale, plain = _with_ref(ref_age_s=600)
    assert stale.grid_source == "inverter" and stale.grid_power == plain.grid_power
    off, _ = _with_ref(features={"use_check_meter": False})
    assert off.grid_source == "inverter" and off.house_power_raw == plain.house_power_raw
    assert off.grid_ref_power == 5880                                    # still read, for the cross-check


def test_history_correction_by_half_hour():
    from pe_core.forecast import meter_corrected
    a, b, c = NOW, NOW + timedelta(minutes=30), NOW + timedelta(minutes=60)
    house = {a: 1520.0, b: 0.0, c: 600.0}
    fixed, which = meter_corrected(house, {a: 6810.0, b: -4420.0}, {a: 5880.0, b: -3760.0})
    assert fixed[a] == 590.0 and fixed[b] == 660.0 and fixed[c] == 600.0   # selling: the house read 0, really ~0.66 kW
    assert which == {a, b}
