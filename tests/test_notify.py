from datetime import datetime, timedelta, timezone

import pytest

from pe_core.config import ConfigError, parse_config
from pe_core.notify import DAILY_CAP, Notifier, axle_message, daily_message, health_message, input_message

NOW = datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc)


def test_notifications_go_to_the_ha_notification_area_by_default_and_are_validated():
    cfg = parse_config({})
    assert cfg.notifications["service"] == "persistent_notification" and cfg.notifications["events"]["health"] is True
    assert parse_config({"notifications": {"service": "off"}}).notifications["service"] == ""
    assert parse_config({"notifications": {"service": ""}}).notifications["service"] == ""
    assert cfg.notifications["events"]["daily"] is False
    cfg = parse_config({"notifications": {"service": "notify.mobile_app_pixel", "events": {"daily": True}}})
    assert cfg.notifications["events"]["daily"] is True
    for bad in ({"service": "light.kitchen"}, {"events": {"spam": True}}, {"events": {"axle": "yes"}}, {"x": 1}):
        with pytest.raises(ConfigError):
            parse_config({"notifications": bad})


def test_each_key_is_sent_once_until_cleared(tmp_path):
    n = Notifier(str(tmp_path / "n.json"))
    assert n.should_send("input:grid_power", NOW)
    n.mark("input:grid_power", NOW)
    assert not n.should_send("input:grid_power", NOW)
    assert not Notifier(str(tmp_path / "n.json")).should_send("input:grid_power", NOW)   # survives restarts
    n.clear("input:grid")
    assert n.should_send("input:grid_power", NOW)


def test_daily_cap(tmp_path):
    n = Notifier(str(tmp_path / "n.json"))
    for i in range(DAILY_CAP):
        n.mark(f"k{i}", NOW)
    assert not n.should_send("another", NOW)
    assert n.should_send("another", NOW + timedelta(days=1))


def test_messages():
    assert health_message({"findings": [{"level": "warning", "title": "x"}]}) is None
    key, title, msg = health_message({"findings": [{"level": "problem", "title": "Battery never charging"}]})
    assert key.startswith("health:") and "problem" in title and "Health tab" in msg
    key, title, _ = input_message("grid_power", "Grid power", "stale", "No update for 40 min")
    assert key == "input:grid_power" and "Grid power" in title
    key, _, msg = axle_message(NOW + timedelta(hours=8), NOW + timedelta(hours=9), NOW)
    assert key.startswith("axle:") and "17:00 today to 18:00" in msg
    s = {"date": "2026-09-25", "s0": 4.72, "actual": 1.40, "solar": 1.03, "smart": 0.05, "s3a": 0.01, "s3b": 1.70}
    key, title, msg = daily_message(s)
    assert key == "daily:2026-09-25" and "£1.40" in title and "You paid £1.40 against £4.72" in msg
    steps = [{"label": "No solar or battery", "kind": "total", "value": 4.72},
             {"label": "Solar", "kind": "step", "value": -1.03},
             {"label": "EDF tariff", "kind": "step", "value": 0.10},
             {"label": "PowerEngine", "kind": "step", "value": -2.39},
             {"label": "Axle & free power", "kind": "step", "value": -0.05},
             {"label": "You paid", "kind": "total", "value": 1.35}]
    key, title, msg = daily_message(s, steps)
    assert "£1.35" in title
    assert msg == ("You paid £1.35 against £4.72 with no solar or battery. Solar −£1.03, EDF tariff +£0.10, "
                   "PowerEngine −£2.39, Axle & free power −£0.05.")


def test_notification_area_create_and_dismiss(monkeypatch, tmp_path):
    import sys
    import types
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine
    a = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    a.cfg = parse_config({})
    a.notifier = Notifier(str(tmp_path / "n.json"))
    calls = []
    a.call_service = lambda svc, **kw: calls.append((svc, kw))
    a.log = lambda *args, **kw: None
    a._notify("health", ("guard:absent", "T", "M"))
    assert calls == [("persistent_notification/create",
                      {"title": "T", "message": "M", "notification_id": "powerengine_guard_absent"})]
    a._clear_notice("guard:absent")
    assert calls[-1] == ("persistent_notification/dismiss", {"notification_id": "powerengine_guard_absent"})
