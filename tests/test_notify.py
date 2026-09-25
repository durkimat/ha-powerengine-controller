from datetime import datetime, timedelta, timezone

import pytest

from pe_core.config import ConfigError, parse_config
from pe_core.notify import DAILY_CAP, Notifier, axle_message, daily_message, health_message, input_message

NOW = datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc)


def test_notifications_are_off_until_a_service_is_set_and_validated():
    cfg = parse_config({})
    assert cfg.notifications["service"] == "" and cfg.notifications["events"]["health"] is True
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
    assert key == "daily:2026-09-25" and "£1.40" in title and "saved £3.32" in msg
