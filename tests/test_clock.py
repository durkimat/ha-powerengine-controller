from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pe_core import clock

LON = ZoneInfo("Europe/London")
READ = datetime(2026, 9, 22, 16, 58, 25, tzinfo=timezone.utc)      # 17:58:25 BST


def test_drift_from_solis_rtc():
    assert clock.drift_seconds("2026-09-22 17:58:23", READ, LON) == -2.0


def test_drift_after_clocks_go_back_is_an_hour():
    read = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)   # 12:00 GMT; inverter still on BST
    assert clock.drift_seconds("2026-10-26 13:00:00", read, LON) == 3600


def test_drift_unknown():
    assert clock.drift_seconds("unavailable", READ, LON) is None
    assert clock.drift_seconds("rubbish", READ, LON) is None
    assert clock.drift_seconds("2026-09-22 17:58:23", None, LON) is None


def test_last_sync_from_button_state():
    assert clock.last_sync("2026-09-22T13:23:20.566694+00:00", LON) == datetime(2026, 9, 22, 13, 23, 20, 566694,
                                                                                 tzinfo=timezone.utc)
    assert clock.last_sync("unknown", LON) is None


def test_sync_due():
    now = READ
    assert clock.sync_due(2, now - timedelta(days=2), now) is None
    assert clock.sync_due(2, now - timedelta(days=7), now) == "routine"
    assert clock.sync_due(2, None, now) == "routine"
    assert clock.sync_due(90, now - timedelta(days=2), now) == "drift"
    assert clock.sync_due(90, now - timedelta(hours=3), now) is None          # at most once a day for drift
    assert clock.sync_due(None, now - timedelta(days=1), now) is None


def test_finding():
    assert clock.finding(30) is None
    f = clock.finding(3600)
    assert f["level"] == "problem" and "60 min ahead" in f["title"]
    assert clock.finding(-150)["level"] == "warning"
