import sys
import types
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pe_core.announce import event_summary, finish_message
from pe_core.carstop import CarStop
from pe_core.config import parse_config
from pe_core.readings import Readings
from pe_core.setback import Setback

UK = ZoneInfo("Europe/London")
START = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
END = START + timedelta(hours=1)
M = timedelta(minutes=1)


def record(start, **kw):
    r = {"start": start.isoformat(), "house": 0.5, "car": 0.0, "grid_export": 2.6, "seconds": 1800,
         "v": {"event_kwh": 2.4, "event_gross": 2.76, "event_net": 2.60}}
    r.update(kw)
    return r


RECORDS = [record(START - 30 * M), record(START), record(START + 30 * M, house=0.7, grid_export=2.2,
                                                         v={"event_kwh": 2.2, "event_gross": 2.53, "event_net": 2.40}),
           record(END)]


def test_summary_adds_up_the_half_hours_of_the_event_only():
    s = event_summary(RECORDS, START, END)
    assert s["half_hours"] == 2 and round(s["house_kwh"], 2) == 1.2 and round(s["export_kwh"], 2) == 4.8
    assert round(s["event_kwh"], 2) == 4.6 and round(s["gross"], 2) == 5.29 and round(s["net"], 2) == 5.0
    assert event_summary(RECORDS, START + 5 * 60 * M, END + 5 * 60 * M) is None


def test_the_message_says_what_the_house_used_what_was_exported_and_what_it_should_pay():
    s = event_summary(RECORDS, START, END)
    text = finish_message("Daddybot here.", START, END, END + M, UK, s, None, None)
    assert text == ("Daddybot here. The grid event at 8 pm today is over. The house used 1.2 kilowatt hours, and we "
                    "exported 4.8 kilowatt hours. It should pay about £5.29, or £5.00 after the battery energy it "
                    "used.")


def test_it_says_what_was_turned_back_and_to_what():
    s = event_summary(RECORDS, START, END)
    text = finish_message("", START, END, END + M, UK, s, "restored", "restored", "Eco+")
    assert "The heating set-back is back to off, as it was before, so the heating is back on its programme." in text
    assert "The car charger is back to Eco+, as it was before." in text
    left = finish_message("", START, END, END + M, UK, s, "already_on", "already_stopped")
    assert "The heating set-back was already on before the event, so I've left it on." in left
    assert "The car charger was already stopped before the event, so I've left it stopped." in left
    changed = finish_message("", START, END, END + M, UK, s, "user_override", "user_changed")
    assert "switched off during the event, so I've left it off" in changed and "changed during the event" in changed
    plain = finish_message("", START, END, END + M, UK, s, None, None)
    assert "heating" not in plain and "charger" not in plain             # nothing was switched: nothing is said


def test_the_message_copes_with_no_figures_and_with_the_next_morning():
    text = finish_message("Hi.", START, END, END + timedelta(hours=11), UK, None, None, None)
    assert text == "Hi. The grid event at 8 pm yesterday is over."
    with_car = {**event_summary(RECORDS, START, END), "car_kwh": 1.5}
    car = finish_message("", START, END, END + M, UK, with_car, None, None)
    assert "and the car 1.5 kilowatt hours" in car


def test_setback_and_carstop_leave_notes_for_the_announcement():
    sb = Setback()
    sb.step(True, "e1", "off")
    assert sb.note == "set_back"
    sb.step(False, "e1", "on")
    assert sb.note == "restored"
    sb2 = Setback()
    sb2.step(True, "e2", "on")
    assert sb2.note == "already_on"
    sb3 = Setback()
    sb3.step(True, "e3", "off"), sb3.step(True, "e3", "off")
    assert sb3.note == "user_override"
    car = CarStop()
    car.step(START, True, "Eco+", ["Eco+", "Stopped"])
    assert car.note == "stopped" and car.mode_before == "Eco+"
    car.step(END, False, "Stopped", ["Eco+", "Stopped"])
    assert car.note == "restored" and car.mode_before == "Eco+"
    other = CarStop()
    other.step(START, True, "Stopped", ["Eco+", "Stopped"])
    assert other.note == "already_stopped"


# --- the app ---

def _app(monkeypatch, tmp_path, alexa=None, records=None):
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine
    a = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    a.cfg = parse_config({"notifications": {"events": {"alexa": True}, "alexa": {
        "services": ["notify.alexa_media_kitchen"], "intro": "Daddybot here.", **(alexa or {})}}})
    a._demo, a.tz = None, UK
    a._save_path = lambda: str(tmp_path / "config.yaml")
    a.calls, a.logs = [], []
    a.call_service = lambda svc, **kw: a.calls.append((svc, kw))
    a.log = lambda msg, **kw: a.logs.append(msg)
    a._logbook = lambda msg: None
    a.costbook = types.SimpleNamespace(day_records=lambda d: RECORDS if records is None else records)
    return a


def _r(t, active):
    return Readings(now=t, axle_active=active, axle_start=START, axle_end=END)


def test_app_follows_an_event_and_announces_it_once_when_it_is_over(monkeypatch, tmp_path):
    a = _app(monkeypatch, tmp_path, alexa={"latest": "23:00"})
    a._run_note(_r(START - 10 * M, False), "heat", "set_back")                 # the set-back began before the event
    a._event_finish_step(_r(START + 5 * M, True))
    a._run_note(_r(START + 5 * M, True), "car", "stopped", "Eco+")
    a._event_finish_step(_r(START + 30 * M, True))
    assert a.calls == []                                                      # still running
    a._run_note(_r(END, False), "heat", "restored")
    a._run_note(_r(END, False), "car", "restored", "Eco+")
    a._event_finish_step(Readings(now=END + 10 * timedelta(seconds=1)))       # just over: last half-hour not recorded
    assert a.calls == []
    a._event_finish_step(Readings(now=END + 1 * M))
    (svc, kw), = a.calls
    assert svc == "notify/alexa_media_kitchen" and kw["data"] == {"type": "announce"}
    assert kw["message"].startswith("Daddybot here. The grid event at 8 pm today is over. The house used 1.2")
    assert "The heating set-back is back to off" in kw["message"] and "back to Eco+, as it was before" in kw["message"]
    a._event_finish_step(Readings(now=END + 2 * M))
    assert len(a.calls) == 1                                                  # once
    a._run = None                                                             # and a restart does not repeat it
    a._event_finish_step(Readings(now=END + 3 * M))
    assert len(a.calls) == 1


def test_app_waits_for_the_allowed_hours_and_can_be_switched_off(monkeypatch, tmp_path):
    a = _app(monkeypatch, tmp_path)                                           # the event ends at 21:00: the latest time
    a._run_note(_r(START, True), "heat", "restored")
    a._event_finish_step(Readings(now=END + 1 * M))
    assert a.calls == []                                                      # too late tonight: kept
    a._event_finish_step(Readings(now=END + timedelta(hours=12)))             # 09:00 the next morning (a Sunday)
    assert len(a.calls) == 1 and "8 pm yesterday" in a.calls[0][1]["message"]
    b = _app(monkeypatch, tmp_path / "x" if False else tmp_path, alexa={"finish": False})
    b._run = None
    b._run_note(_r(START, True), "heat", "restored")
    b._event_finish_step(Readings(now=END + timedelta(hours=12)))
    assert b.calls == []
    old = _app(monkeypatch, tmp_path)
    old._run = {"start": START.isoformat(), "end": END.isoformat(), "done": False}
    old._event_finish_step(Readings(now=END + timedelta(hours=15)))
    assert old.calls == []                                                    # too old to be worth saying
