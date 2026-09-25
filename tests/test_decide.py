import dataclasses
from datetime import timedelta

import pytest
from fixtures import BST, NOW

from pe_core.config import parse_config
from pe_core.decide import FORCE_DISCHARGE, GRID_CHARGE, HOLD, NONE, SELF_USE, decide, pre_axle_reserve
from pe_core.readings import Readings

CFG = parse_config({"inputs": {"battery_capacity": {"value": 18}, "battery_max_discharge_power": {"value": 4800}}})
PEAK, CHEAP = 0.302831, 0.06993


def R(**kw):
    base = dict(now=NOW, battery_soc=60, battery_power=500, import_rate=PEAK, ev_power=0, ev_plug="EV Disconnected")
    base.update(kw)
    return Readings(**base)


def test_default_is_self_use():
    d = decide(R(), CFG)
    assert d.action == SELF_USE and d.rule == "default"


def test_cheap_rate_charges_to_target():
    d = decide(R(import_rate=CHEAP), CFG)
    assert d.action == GRID_CHARGE and d.target_soc == 100 and "6.99p" in d.reason


def test_cheap_rate_holds_when_full():
    assert decide(R(import_rate=CHEAP, battery_soc=100), CFG).action == HOLD


def test_hysteresis_stops_flapping():
    # at 98% with no previous charge: within the 3% band, so hold
    assert decide(R(import_rate=CHEAP, battery_soc=98), CFG).action == HOLD
    # but if we were already charging, keep going to the target
    prev = decide(R(import_rate=CHEAP, battery_soc=50), CFG)
    assert decide(R(import_rate=CHEAP, battery_soc=98), CFG, prev).action == GRID_CHARGE


def test_car_charging_never_drains_battery():
    d = decide(R(ev_power=7000, ev_plug="Charging"), CFG)
    assert d.action == HOLD and d.rule == "car_charging"


def test_car_charging_on_cheap_rate_charges_battery_too():
    assert decide(R(ev_power=7000, import_rate=CHEAP), CFG).action == GRID_CHARGE


def test_car_rule_off_when_car_not_in_house_load():
    cfg = dataclasses.replace(CFG, system={"house_load_includes_ev": False})
    r = R(ev_power=7000)
    r.house_includes_ev = False
    assert decide(r, cfg).action == SELF_USE


def test_free_power_fills_battery():
    d = decide(R(free_active=True), CFG)
    assert d.action == GRID_CHARGE and d.target_soc == 100


def test_axle_active_wins_over_everything():
    d = decide(R(axle_active=True, free_active=True, ev_power=7000, import_rate=CHEAP), CFG)
    assert d.action == FORCE_DISCHARGE and d.power_w == 4000


def test_axle_feature_off_is_ignored():
    cfg = dataclasses.replace(CFG, features={**CFG.features, "axle": False})
    assert decide(R(axle_active=True), cfg).action == SELF_USE


def test_pre_axle_reserve_maths():
    r = R(axle_start=NOW + timedelta(hours=2), axle_end=NOW + timedelta(hours=3))
    # 12% floor + 4 kWh / 18 kWh (22.2%) + 5% margin
    assert pre_axle_reserve(r, CFG) == pytest.approx(39.2, abs=0.1)


def test_pre_axle_holds_charge():
    r = R(battery_soc=30, axle_start=NOW + timedelta(hours=2), axle_end=NOW + timedelta(hours=3))
    d = decide(r, CFG, tz=BST)
    assert d.action == HOLD and d.rule == "pre_axle" and "19:58" in d.reason


def test_pre_axle_charges_if_cheap():
    r = R(battery_soc=30, import_rate=CHEAP, axle_start=NOW + timedelta(hours=2), axle_end=NOW + timedelta(hours=3))
    assert decide(r, CFG).action == GRID_CHARGE


def test_far_away_axle_event_not_yet_protected():
    r = R(battery_soc=30, axle_start=NOW + timedelta(hours=10), axle_end=NOW + timedelta(hours=11))
    assert decide(r, CFG).action == SELF_USE


def test_reserve_floor():
    d = decide(R(battery_soc=12), CFG)
    assert d.action == HOLD and d.rule == "reserve"


def test_no_data():
    assert decide(None, CFG).action == NONE
    assert decide(R(battery_soc=None), CFG).action == NONE


def test_sentences():
    charging = decide(R(import_rate=CHEAP), CFG).sentence(passive=True)
    assert charging.startswith("Would grid-charge to 100%: import is cheap")
    assert decide(R(axle_active=True), CFG).sentence(passive=False).startswith("Force-discharge at 4.0 kW")


def test_safety_settings_validated():
    from pe_core.config import ConfigError
    with pytest.raises(ConfigError):
        parse_config({"safety": {"min_reserve_soc": 150}})
    with pytest.raises(ConfigError):
        parse_config({"safety": {"min_reserve_soc": 90, "grid_charge_target_soc": 80}})
    with pytest.raises(ConfigError):
        parse_config({"system": {"house_load_includes_ev": "yes"}})
    assert parse_config({"safety": {"cheap_threshold_p": 8}}).safety["cheap_threshold_p"] == 8
