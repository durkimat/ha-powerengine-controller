"""Settings saved when the grid-event names were `axle_*` still load, under the current names."""
from pe_core.compare.snapfeed import Snapshot
from pe_core.config import migrate_names, parse_config

OLD = {
    "features": {"axle": False, "axle_plus_export": True, "axle_stop_car": False, "axle_heating_setback": True,
                 "arbitrage": True},
    "safety": {"pre_axle_lookahead_h": 4, "axle_margin_soc": 7},
    "inputs": {"axle_event_active": {"entity": "sensor.a"}, "axle_event_start": {"entity": "sensor.s"},
               "axle_event_end": {"entity": "sensor.e"}, "axle_direction": {"entity": "sensor.d"}},
    "notifications": {"service": "persistent_notification", "events": {"axle": False, "health": True}},
}


def test_an_older_file_is_read_under_the_current_names():
    cfg = parse_config(OLD)
    assert cfg.features["grid_events"] is False and "axle" not in cfg.features
    assert cfg.features["grid_event_plus_export"] is True and cfg.features["grid_event_stop_car"] is False
    assert cfg.features["grid_event_heating_setback"] is True and cfg.features["arbitrage"] is True
    assert cfg.safety["pre_grid_event_lookahead_h"] == 4 and cfg.safety["grid_event_margin_soc"] == 7
    assert cfg.inputs["grid_event_start"] == {"entity": "sensor.s"} and "axle_event_start" not in cfg.inputs
    assert set(cfg.inputs) == {"grid_event_active", "grid_event_start", "grid_event_end", "grid_event_direction"}


def test_the_older_notification_kind_goes_to_the_current_one_in_both_forms():
    legacy = parse_config(OLD).notifications
    assert all("axle" not in r["events"] and "grid_event" not in r["events"] for r in legacy["rules"])   # it was off
    rules = {"channels": {"bell": {"type": "bell"}}, "rules": [{"events": ["axle", "health"], "to": ["bell"]}]}
    assert parse_config({"notifications": rules}).notifications["rules"][0]["events"] == ["grid_event", "health"]


def test_a_name_already_current_wins_and_the_input_is_not_changed():
    data = {"features": {"axle": False, "grid_events": True}}
    before = {"features": dict(data["features"])}
    assert migrate_names(data)["features"] == {"grid_events": True}
    assert data == before
    assert migrate_names({}) == {}


def test_an_old_snapshot_has_its_roles_under_the_current_names():
    snap = Snapshot({"version": 1, "day": "2026-08-20", "tz": "Europe/London",
                     "roles": {"axle_event_start": "sensor.s", "import_rates_today": "event.r"}, "entries": []})
    assert snap.roles == {"grid_event_start": "sensor.s", "import_rates_today": "event.r"}
