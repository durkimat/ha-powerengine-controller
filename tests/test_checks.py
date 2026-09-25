from datetime import datetime, timedelta, timezone

from pe_core.checks import check, summarise
from pe_core.roles import ROLE_BY_KEY

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def st(state, unit=None, minutes_ago=1, **attrs):
    a = dict(attrs)
    if unit:
        a["unit_of_measurement"] = unit
    return {"state": state, "attributes": a, "last_updated": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


R = ROLE_BY_KEY


def test_ok_power():
    assert check(R["battery_power"], {"entity": "sensor.b"}, st("1040", "W"), NOW)[0] == "ok"


def test_unmapped_and_missing():
    assert check(R["battery_soc"], None, None, NOW)[0] == "unmapped"
    assert check(R["battery_soc"], {"entity": "sensor.x"}, None, NOW)[0] == "missing"


def test_wrong_unit_and_domain():
    assert check(R["battery_soc"], {"entity": "sensor.x"}, st("5", "W"), NOW)[0] == "wrong_unit"
    assert check(R["battery_soc"], {"entity": "switch.x"}, st("on"), NOW)[0] == "wrong_domain"


def test_unavailable_but_timestamps_may_be_unknown():
    assert check(R["battery_soc"], {"entity": "sensor.x"}, st("unavailable", "%"), NOW)[0] == "unavailable"
    assert check(R["axle_event_start"], {"entity": "sensor.x"}, st("unknown"), NOW)[0] == "ok"


def test_stale_power():
    assert check(R["grid_power"], {"entity": "sensor.g"}, st("5", "W", minutes_ago=90), NOW)[0] == "stale"


def test_idle_power_at_zero_is_not_stale():
    # e.g. the Zappi's charging power sits at 0 W for hours and the integration only writes on change
    assert check(R["ev_charge_power"], {"entity": "sensor.c"}, st("0", "W", minutes_ago=600), NOW)[0] == "ok"
    assert check(R["ev_charge_power"], {"entity": "sensor.c"}, st("0.0", "kW", minutes_ago=600), NOW)[0] == "ok"


def test_attribute_roles():
    role = R["import_rates_today"]
    assert check(role, {"entity": "event.r"}, st("2026-09-25", rates=[{"value_inc_vat": 0.07}]), NOW)[0] == "ok"
    assert check(role, {"entity": "event.r"}, st("2026-09-25"), NOW)[0] == "no_attribute"


def test_static_values():
    assert check(R["battery_capacity"], {"value": 18}, None, NOW)[0] == "ok"
    assert check(R["battery_soc"], {"value": 50}, None, NOW)[0] == "bad_static"


def test_forbidden_control():
    assert check(R["smart_target_soc"], {"entity": "number.edf_bump_charge"}, st("1"), NOW)[0] == "forbidden"


def test_summary():
    ok = ("ok", "OK")
    assert summarise({"a": ok}, ["a"]) == "ok"
    assert summarise({"a": ("missing", "")}, ["a"]) == "incomplete"
    assert summarise({"a": ok, "b": ("stale", "")}, ["a"]) == "warnings"


def test_axle_direction_unknown_between_events_is_ok():
    assert check(R["axle_direction"], {"entity": "sensor.a"}, st("unknown", None), NOW)[0] == "ok"
    assert check(R["axle_direction"], {"entity": "sensor.a"}, st("unavailable", None), NOW)[0] == "unavailable"
