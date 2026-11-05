import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pe_core.config import ConfigError, parse_channel, parse_config
from pe_core.notify import EXT_DAILY_CAP, Notifier
from pe_core.router import Message, Outbox, channels_for, is_open, render

UK = ZoneInfo("Europe/London")
DAY = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)          # a Tuesday
NIGHT = datetime(2026, 11, 3, 22, 30, tzinfo=timezone.utc)
GROUP = "120363085203994232@g.us"
M = timedelta(minutes=1)

NOTIFICATIONS = {
    "channels": {
        "bell": {"type": "bell"},
        "alexa": {"type": "alexa", "services": ["notify.alexa_media_kitchen"], "intro": "Daddybot here.",
                  "hours": {"weekday_from": "07:30", "weekend_from": "09:00", "until": "21:00"}},
        "family": {"type": "whatsapp", "targets": [GROUP], "hours": {"weekday_from": "08:00", "until": "21:00"},
                   "outside_hours": "skip"},
    },
    "rules": [
        {"events": ["health"], "to": ["bell"]},
        {"events": ["grid_reminder", "bins"], "to": ["alexa", "family"]},
    ],
}


def test_channels_and_rules_are_checked():
    n = parse_config({"notifications": NOTIFICATIONS}).notifications
    assert n["format"] == "channels" and n["reminder_hours"] == [8.0, 5.0, 2.0, 1.0, 0.0]
    assert n["channels"]["alexa"]["hours"] == {"weekday_from": 450, "weekend_from": 540, "until": 1260}
    assert n["channels"]["family"]["hours"] == {"weekday_from": 480, "weekend_from": 480, "until": 1260}
    assert n["channels"]["family"]["outside_hours"] == "skip" and n["channels"]["bell"]["outside_hours"] == "hold"
    assert n["channels"]["bell"]["hours"] is None
    bads = [
        {"channels": {"x": {"type": "fax"}}},
        {"channels": {"Bad Name": {"type": "bell"}}},
        {"channels": {"a": {"type": "alexa", "services": []}}},
        {"channels": {"a": {"type": "alexa", "services": ["notify.alexa_media_a"], "colour": 1}}},
        {"channels": {"a": {"type": "notify", "service": "light.kitchen"}}},
        {"channels": {"w": {"type": "whatsapp", "targets": ["Durkin Chat"]}}},
        {"channels": {"b": {"type": "bell", "outside_hours": "later"}}},
        {"channels": {"b": {"type": "bell", "hours": {"until": "9pm"}}}},
        {"channels": {"b": {"type": "bell"}}, "rules": [{"events": ["health"], "to": ["nowhere"]}]},
        {"channels": {"b": {"type": "bell"}}, "rules": [{"events": ["Health!"], "to": ["b"]}]},
        {"channels": {"b": {"type": "bell"}}, "rules": [{"events": [], "to": ["b"]}]},
        {"reminder_hours": []},
        {"channels": {}, "service": "off"},                           # new and older settings together
    ]
    for bad in bads:
        with pytest.raises(ConfigError):
            parse_config({"notifications": bad})
    assert parse_channel({"type": "bell"}, "x")["outside_hours"] == "hold"


def test_the_older_settings_become_the_same_channels_and_rules():
    group = GROUP
    n = parse_config({"notifications": {"events": {"alexa": True, "daily": True}, "alexa": {
        "services": ["notify.alexa_media_kitchen"], "whatsapp": True, "whatsapp_targets": [group],
        "latest": "20:00"}}}).notifications
    assert n["format"] == "legacy" and set(n["channels"]) == {"bell", "alexa", "whatsapp"}
    assert n["channels"]["whatsapp"]["hours"]["until"] == 1200
    assert n["channels"]["whatsapp"]["intro"] == "Daddybot here."
    assert {"events": ["grid_reminder", "grid_summary"], "to": ["alexa", "whatsapp"]} in n["rules"]
    assert "daily" in n["rules"][0]["events"] and n["rules"][0]["to"] == ["bell"]
    off = parse_config({"notifications": {"alexa": {"finish": False}, "events": {"alexa": True}}}).notifications
    assert {"events": ["grid_reminder"], "to": ["alexa"]} in off["rules"]


def test_rules_say_where_each_kind_goes_and_hours_say_when():
    n = parse_config({"notifications": NOTIFICATIONS}).notifications
    assert channels_for(n, "grid_reminder") == ["alexa", "family"] and channels_for(n, "health") == ["bell"]
    assert channels_for(n, "daily") == [] and channels_for(n, "bins") == ["alexa", "family"]
    alexa = n["channels"]["alexa"]
    assert is_open(alexa, DAY, UK) and not is_open(alexa, NIGHT, UK)
    assert not is_open(alexa, datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc), UK)          # before 07:30
    assert is_open(n["channels"]["bell"], NIGHT, UK)                                          # no hours: always
    sunday = datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)
    assert not is_open(alexa, sunday, UK) and is_open(n["channels"]["family"], sunday, UK)


def test_each_channel_gets_the_message_in_its_own_form():
    n = parse_config({"notifications": NOTIFICATIONS}).notifications["channels"]
    msg = Message("health", "k", "A problem", "Something is wrong.")
    assert render(n["bell"], msg) == ("A problem", "Something is wrong.")
    assert render(n["alexa"], msg) == (None, "Daddybot here. A problem. Something is wrong.")
    assert render(n["family"], msg) == (None, "*A problem*\nSomething is wrong.")
    said = Message("grid_reminder", "k", "Reminder", "The event starts soon.", speech="The event starts soon.")
    assert render(n["alexa"], said)[1] == "Daddybot here. The event starts soon."
    assert render(n["family"], said)[1] == "The event starts soon."
    held = render(n["alexa"], said, held_at=NIGHT, now=NIGHT + timedelta(hours=11), tz=UK)[1]
    assert held == "Daddybot here. Held message from 10:30 pm yesterday. The event starts soon."


def test_the_outbox_holds_releases_one_per_channel_and_drops_the_stale(tmp_path):
    n = parse_config({"notifications": NOTIFICATIONS}).notifications
    box = Outbox(str(tmp_path / "o.json"))
    one = Message("grid_reminder", "a", "One", "one", expires=NIGHT + timedelta(hours=1))
    two = Message("bins", "b", "Two", "two")
    box.hold("alexa", one, NIGHT)
    box.hold("alexa", two, NIGHT)
    box.hold("alexa", two, NIGHT)                                      # the same message twice is one
    assert len(box.held) == 2 and box.release(NIGHT + M, n, UK) == []  # still closed
    morning = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)
    got = box.release(morning, n, UK)
    assert [m.key for _, _, m, _ in got] == ["b"]                      # "a" held for an hour only: dropped
    assert box.log[-1]["outcome"] == "dropped" and box.held == []
    assert Outbox(str(tmp_path / "o.json")).log == box.log             # kept across restarts
    box.hold("gone", two, NIGHT)
    assert box.release(morning, n, UK) == [] and box.log[-1]["note"] == "that channel no longer exists"
    box.hold("alexa", Message("health", "h:1", "T", "t"), NIGHT)
    box.hold("alexa", Message("health", "h:2", "T", "t"), NIGHT)
    assert box.cancel("h:") == 2 and box.held == []


# --- the app ---

def _app(monkeypatch, tmp_path, notifications=None):
    sys.modules.pop("powerengine", None)
    import powerengine
    a = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    a.cfg = parse_config({"notifications": notifications or NOTIFICATIONS})
    a.tz = UK
    a.notifier = Notifier(str(tmp_path / "n.json"))
    a._save_path = lambda: str(tmp_path / "config.yaml")
    a.calls, a.logs, a.events, a.failing = [], [], [], set()

    def call(svc, **kw):
        if svc in a.failing:
            raise RuntimeError("down")
        a.calls.append((svc, kw))
    a.call_service = call
    a.log = lambda msg, **kw: a.logs.append(msg)
    a.fire_event = lambda name, **kw: a.events.append((name, kw))
    return a


def test_a_message_goes_to_every_channel_its_rule_names(monkeypatch, tmp_path):
    a = _app(monkeypatch, tmp_path)
    assert a._deliver(Message("grid_reminder", "r1", "Reminder", "Event soon.", speech="Event soon."), DAY)
    assert [c[0] for c in a.calls] == ["notify/alexa_media_kitchen", "whatsapp/send_message"]
    assert a.calls[0][1] == {"message": "Daddybot here. Event soon.", "data": {"type": "announce"}}
    assert a.calls[1][1] == {"target": GROUP, "message": "Event soon."}
    assert not a._deliver(Message("unruled", "x", "T", "t"), DAY)              # no rule takes it
    assert a._outbox().seen["unruled"]["source"] == "powerengine"
    a._notify("health", ("guard:absent", "T", "M"))
    assert a.calls[-1] == ("persistent_notification/create",
                           {"title": "T", "message": "M", "notification_id": "powerengine_guard_absent"})
    n = len(a.calls)
    a._notify("health", ("guard:absent", "T", "M"))
    assert len(a.calls) == n                                                    # once


def test_outside_a_channels_hours_it_is_held_or_skipped_as_the_channel_says(monkeypatch, tmp_path):
    a = _app(monkeypatch, tmp_path)
    event = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
    assert a._deliver(Message("grid_reminder", "r1", "Reminder", "Event soon.", speech="Event soon.",
                              expires=event), NIGHT)
    assert a.calls == []                                                        # alexa holds, the family chat skips
    assert [h["channel"] for h in a._outbox().held] == ["alexa"]
    outcomes = {e["channel"]: e["outcome"] for e in a._outbox().log}
    assert outcomes == {"alexa": "held", "family": "skipped"}
    a._flush_held(NIGHT + 5 * M)
    assert a.calls == []
    a._flush_held(datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc))
    (svc, kw), = a.calls
    assert svc == "notify/alexa_media_kitchen"
    assert kw["message"] == "Daddybot here. Held message from 10:30 pm yesterday. Event soon."
    assert a._outbox().log[-1]["outcome"] == "released" and a._outbox().held == []


def test_a_reminder_is_not_held_past_the_start_of_the_event(monkeypatch, tmp_path):
    a = _app(monkeypatch, tmp_path)
    soon = Message("grid_reminder", "r2", "R", "r", expires=NIGHT + 30 * M)
    a._deliver(soon, NIGHT)
    a._flush_held(datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc))
    assert a.calls == [] and a._outbox().log[-1]["outcome"] == "dropped"
    late = Message("grid_reminder", "r3", "R", "r", expires=NIGHT - M)
    assert a._deliver(late, NIGHT) and a._outbox().held == []                  # already over: nothing to hold


def test_a_failed_channel_does_not_stop_the_others_and_all_failing_can_be_retried(monkeypatch, tmp_path):
    a = _app(monkeypatch, tmp_path)
    a.failing = {"whatsapp/send_message"}
    assert a._deliver(Message("grid_reminder", "r1", "R", "r"), DAY)
    assert [c[0] for c in a.calls] == ["notify/alexa_media_kitchen"]
    assert any("Could not send to WhatsApp" in m for m in a.logs)
    a.failing = {"notify/alexa_media_kitchen", "whatsapp/send_message"}
    assert not a._deliver(Message("grid_reminder", "r2", "R", "r"), DAY)       # nothing reached: the caller may retry


def test_clearing_a_notice_forgets_it_if_it_is_still_held(monkeypatch, tmp_path):
    a = _app(monkeypatch, tmp_path, {"channels": {"b": {"type": "bell", "hours": {"until": "21:00"}}},
                                    "rules": [{"events": ["health"], "to": ["b"]}]})
    a._deliver(Message("health", "control:inputs", "T", "M"), NIGHT)
    assert len(a._outbox().held) == 1
    a._clear_notice("control:inputs")
    assert a._outbox().held == []
    assert a.calls[-1] == ("persistent_notification/dismiss", {"notification_id": "powerengine_control_inputs"})


def test_a_message_from_elsewhere_is_routed_by_its_kind_and_listed_when_new(monkeypatch, tmp_path):
    a = _app(monkeypatch, tmp_path, {"channels": {"f": {"type": "whatsapp", "targets": [GROUP]}},
                                    "rules": [{"events": ["bins"], "to": ["f"]}]})
    a._on_pe_notify("pe_notify", {"kind": "bins", "title": "Bins", "text": "Bins out tonight.", "source": "family",
                                  "label": "Bin collections"}, None)
    assert a.calls == [("whatsapp/send_message", {"target": GROUP, "message": "*Bins*\nBins out tonight."})]
    assert a._outbox().seen["bins"] == {"source": "family", "label": "Bin collections",
                                        "last": a._outbox().seen["bins"]["last"]}
    before = len(a.calls)
    a._on_pe_notify("pe_notify", {"kind": "new_thing", "text": "Hello", "label": "A new thing"}, None)
    assert len(a.calls) == before and a._outbox().seen["new_thing"]["source"] == "external"
    for bad in ({}, {"kind": "Bad Kind", "text": "x"}, {"kind": "bins"}, {"kind": "bins", "text": "  "}):
        a._on_pe_notify("pe_notify", bad, None)
    assert len(a.calls) == before


def test_messages_from_elsewhere_have_their_own_daily_cap(tmp_path):
    n = Notifier(str(tmp_path / "n.json"))
    now = datetime(2026, 11, 3, 9, tzinfo=timezone.utc)
    for i in range(EXT_DAILY_CAP):
        n.mark(f"ext:{i}", now)
    assert not n.should_send("ext:more", now) and n.should_send("health:new", now)
    assert n.should_send("ext:more", now + timedelta(days=1))
    for i in range(3):
        n.mark(f"health:{i}", now)
    assert n.should_send("health:new", now)


def test_the_test_button_sends_to_the_channel_as_set_on_the_page(monkeypatch, tmp_path):
    a = _app(monkeypatch, tmp_path)
    a._on_notify_test("pe_notify_test", {"channel": {"type": "alexa", "services": ["notify.alexa_media_a"],
                                                      "intro": "Hi.", "hours": {"until": "01:00"}}}, None)
    assert a.calls == [("notify/alexa_media_a", {"message": "Hi. Test message.", "data": {"type": "announce"}})]
    assert a.events[-1] == ("pe_notify_test_result", {"ok": True, "message": "Sent to 1 of 1."})
    a._on_notify_test("pe_notify_test", {"channel": {"type": "fax"}}, None)
    assert a.events[-1][1]["ok"] is False and "type must be" in a.events[-1][1]["message"]
