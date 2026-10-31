from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pe_core.announce import READY, SHORT, TIGHT, allowed, due, message, readiness, trial_message, trial_speakers
from pe_core.config import ConfigError, parse_config
from pe_core.notify import Notifier

UK = ZoneInfo("Europe/London")
START = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)     # a Saturday
END = START + timedelta(hours=1)


def _timeline(mid_end, low_end, floor=12.0):
    t0 = START - timedelta(hours=10)
    steps = 12 * 4 + 1                                           # 15-minute steps, long enough to cover the event
    i_end = round((END - t0).total_seconds() / 900)
    mid = [60.0] * steps
    low = [55.0] * steps
    mid[i_end], low[i_end] = mid_end, low_end
    return {"floor_soc": floor, "path": {"start": t0.isoformat(), "step_min": 15, "mid": mid, "low": low}}


def test_allowed_hours_weekday_and_weekend():
    tue = datetime(2026, 10, 27, tzinfo=UK)
    sat = datetime(2026, 10, 31, tzinfo=UK)
    args = (7 * 60 + 30, 9 * 60, 21 * 60)
    assert not allowed(tue.replace(hour=7, minute=29), UK, *args)
    assert allowed(tue.replace(hour=7, minute=30), UK, *args)
    assert not allowed(sat.replace(hour=8, minute=59), UK, *args)
    assert allowed(sat.replace(hour=9), UK, *args)
    assert allowed(tue.replace(hour=20, minute=59), UK, *args)
    assert not allowed(tue.replace(hour=21), UK, *args)


def test_due_picks_the_reminder_whose_time_has_come_and_drops_late_ones():
    hours = [8, 5, 2, 1, 0]
    assert due(START, END, START - timedelta(hours=8), hours) == 8
    assert due(START, END, START - timedelta(hours=5, minutes=-10), hours) == 5      # 10 minutes after T-5
    assert due(START, END, START - timedelta(hours=7), hours) is None
    assert due(START, END, START - timedelta(hours=1, minutes=-21), hours) is None   # too late to say it
    assert due(START, END, START, hours) == 0
    assert due(START, END, END, hours) is None


@pytest.mark.parametrize("mid_end,low_end,state", [(30, 25, READY), (30, 14, TIGHT), (14, 12, SHORT)])
def test_readiness_from_the_timeline(mid_end, low_end, state):
    assert readiness(_timeline(mid_end, low_end), START, END, 60.0)["state"] == state


def test_readiness_without_a_timeline_compares_the_level_with_what_is_needed():
    assert readiness(None, START, END, 70.0, 50.0)["state"] == READY
    assert readiness(None, START, END, 40.0, 50.0)["state"] == TIGHT
    assert readiness(None, START, END, 40.0, None)["state"] is None


def test_messages():
    now = START - timedelta(hours=8)
    ready = {"state": READY, "at_start": 51.0, "at_end": 20.0}
    first = message(8, START, END, now, timezone.utc, 62.0, ready)
    assert first == ("Heads up. There's a grid event today at 8 pm, lasting 1 hour. The battery is at 62 percent and "
                     "should be around 51 percent by then. We're looking well prepared.")
    short = {"state": SHORT, "at_start": 30.0, "at_end": 12.0}
    two = message(2, START, END, START - timedelta(hours=2), timezone.utc, 40.0, short)
    assert "starts in 2 hours" in two and "may run short" in two and "Please start turning off" in two
    at_start = message(0, START, END, START, timezone.utc, 51.0, ready)
    assert at_start.startswith("The grid event is starting now and runs until 9 pm.")
    named = message(0, START, END, START, timezone.utc, 51.0, ready, "Daddybot here.")
    assert named.startswith("Daddybot here. The grid event is starting now")
    assert "computers, ovens, lights" in at_start
    assert "turning off" not in message(1, START, END, START - timedelta(hours=1), timezone.utc, 55.0, ready)


def test_alexa_config_defaults_and_validation():
    cfg = parse_config({})
    a = cfg.notifications["alexa"]
    assert cfg.notifications["events"]["alexa"] is False
    assert a["intro"] == "Daddybot here." and a["services"] == ["notify.alexa_media_everywhere"]
    assert a["hours_before"] == [8, 5, 2, 1, 0]
    assert (a["earliest_weekday"], a["earliest_weekend"], a["latest"]) == (450, 540, 1260)
    mine = {"hours_before": [1, 0], "latest": "20:30", "services": ["notify.alexa_media_kitchen"] * 2}
    b = parse_config({"notifications": {"events": {"alexa": True}, "alexa": mine}})
    assert b.notifications["alexa"]["hours_before"] == [1.0, 0.0] and b.notifications["alexa"]["latest"] == 1230
    assert b.notifications["alexa"]["services"] == ["notify.alexa_media_kitchen"]
    assert parse_config({"notifications": {"alexa": {"intro": ""}}}).notifications["alexa"]["intro"] == ""
    bads = ({"latest": "9pm"}, {"services": "notify.alexa_media_kitchen"}, {"services": ["alexa"]},
            {"hours_before": []}, {"colour": 1}, {"intro": "x" * 81}, {"intro": 5})
    for bad in bads:
        with pytest.raises(ConfigError):
            parse_config({"notifications": {"alexa": bad}})


def test_alexa_reminders_have_their_own_cap(tmp_path):
    n = Notifier(str(tmp_path / "n.json"))
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    for i in range(12):
        n.mark(f"health:{i}", now)
    assert not n.should_send("health:new", now)
    assert n.should_send("alexa:x:8", now)
    n.mark("alexa:x:8", now)
    assert not n.should_send("alexa:x:8", now)


def test_trial_announcement_text_and_speakers():
    assert trial_message("Daddybot here.") == "Daddybot here. Test message."
    assert trial_message("") == "Test message."
    assert trial_message("  Hi  ") == "Hi Test message."
    got = trial_speakers(["notify.alexa_media_kitchen", "notify.alexa_media_kitchen", "notify.alexa_media_last_called",
                         "notify.mobile_app_phone", "light.turn_on", 5, "notify.alexa_media_Bad Name",
                         "notify.alexa_media_everywhere"])
    assert got == ["notify.alexa_media_kitchen", "notify.alexa_media_everywhere"]
    assert trial_speakers(None) == [] and trial_speakers("notify.alexa_media_kitchen") == []
