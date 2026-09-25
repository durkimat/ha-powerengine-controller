import dataclasses

from fixtures import AXLE_19_20, BST, CONFIG, NOW, STATES, S, get_state

from pe_core.readings import forecast_kwh, parse_time, parse_windows, read


def test_core_readings_and_conventions():
    r = read(CONFIG, get_state(), NOW)
    assert r.battery_soc == 71
    assert r.battery_power == 1040            # + = discharging
    assert r.grid_power == -240               # - = exporting (no invert needed)
    assert r.house_power == 1238
    assert r.solar_power == 96 + 50           # kW converted, plants summed
    assert r.solar_by_plant == {"main": 96, "garage": 50}
    assert r.problems == []


def test_invert_flips_sign():
    inverted = {**CONFIG.inputs, "grid_power": {"entity": "sensor.meter_power", "invert": True}}
    cfg = dataclasses.replace(CONFIG, inputs=inverted)
    assert read(cfg, get_state(), NOW).grid_power == 240


def test_car_is_removed_from_house_load():
    states = {**STATES, "sensor.car_power": S("7000", "W"), "sensor.house_load": S("8200", "W"),
              "sensor.plug": S("Charging")}
    r = read(CONFIG, get_state(states), NOW)
    assert r.house_power == 1200 and r.ev_state() == "charging"


def test_rates_and_next_change():
    r = read(CONFIG, get_state(), NOW)
    assert len(r.rates) == 96
    nxt = r.next_rate_change()          # peak now; cheap again from midnight
    assert nxt.value == 0.06993 and nxt.start.astimezone(BST).hour == 0


def test_dispatches():
    r = read(CONFIG, get_state(), NOW)
    assert r.current_dispatch() is None
    assert r.next_dispatch().start.astimezone(BST).hour == 21


def test_events_idle_when_unknown():
    r = read(CONFIG, get_state(), NOW)
    assert r.axle_state() == "idle" and r.free_state() == "none"


def test_axle_scheduled_and_active():
    later = {**STATES, **AXLE_19_20}
    assert read(CONFIG, get_state(later), NOW).axle_state() == "scheduled"
    now_on = {**later, "sensor.axle_active": S("on")}
    assert read(CONFIG, get_state(now_on), NOW).axle_state() == "active"


def test_missing_entities_are_reported_not_fatal():
    r = read(CONFIG, get_state({}), NOW)
    assert r.battery_soc is None and "battery_soc" in r.problems and r.solar_power is None


def test_helpers():
    assert parse_time("unknown") is None
    assert parse_time("2026-09-22T19:00:00+01:00").tzinfo is not None
    assert forecast_kwh([{"pv_estimate": 2.0}] * 4) == 4.0
    assert parse_windows([{"start": "bad"}, "junk"]) == []


def test_car_not_subtracted_when_house_load_excludes_it():
    cfg = dataclasses.replace(CONFIG, system={"house_load_includes_ev": False})
    states = {**STATES, "sensor.car_power": S("7000", "W"), "sensor.house_load": S("1200", "W")}
    r = read(cfg, get_state(states), NOW)
    assert r.house_power == 1200


def test_car_charging_comes_from_the_plug_status():
    from pe_core.readings import Readings
    def st(plug, power):
        return Readings(now=NOW, ev_plug=plug, ev_power=power).ev_state()
    assert st("Charging", 0) == "charging"               # plug status wins, even before the power sensor updates
    assert st("Waiting for EV", 7000) == "plugged_in"    # a stale power reading doesn't count
    assert st("Charge Complete", 0) == "plugged_in"
    assert st("EV Disconnected", 0) == "unplugged"
    assert st(None, 7000) == "charging"                  # plug status unmapped: fall back to power
    assert st("unavailable", 0) == "unplugged"


def test_unsigned_battery_power_uses_the_in_and_out_sensors():
    cfg = dataclasses.replace(CONFIG, inputs={**CONFIG.inputs, "battery_charge_power": {"entity": "sensor.b_in"},
                                              "battery_discharge_power": {"entity": "sensor.b_out"}})
    charging = {**STATES, "sensor.bat_power": S("2000", "W"),
                "sensor.b_in": S("2000", "W"), "sensor.b_out": S("0", "W")}
    assert read(cfg, get_state(charging), NOW).battery_power == -2000
    discharging = {**STATES, "sensor.b_in": S("0", "W"), "sensor.b_out": S("1.5", "kW")}
    assert read(cfg, get_state(discharging), NOW).battery_power == 1500
    assert read(CONFIG, get_state(charging), NOW).battery_power == 2000     # not mapped: the plain sensor as before
    gap = {**STATES, "sensor.bat_power": S("2000", "W"), "sensor.b_in": S("unavailable"), "sensor.b_out": S("0", "W")}
    assert read(cfg, get_state(gap), NOW).battery_power is None             # never falls back to the unsigned sensor


def test_battery_pair_replaces_the_single_sensor_as_a_required_input():
    from pe_core.config import required_roles
    pair = dataclasses.replace(CONFIG, inputs={**CONFIG.inputs, "battery_charge_power": {"entity": "sensor.b_in"},
                                               "battery_discharge_power": {"entity": "sensor.b_out"}})
    assert "battery_power" in required_roles(CONFIG) and "battery_charge_power" not in required_roles(CONFIG)
    req = required_roles(pair)
    assert "battery_power" not in req and {"battery_charge_power", "battery_discharge_power"} <= set(req)
