import json
import sys
import types
from datetime import date, datetime, timedelta, timezone

from pe_core import coverage
from pe_core.energy import MAX_GAP_S, Recorder
from pe_core.readings import Readings

T0 = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
S = timedelta(seconds=1)


def reading(t, **kw):
    base = dict(now=t, battery_soc=70.0, battery_power=1000.0, grid_power=0.0, house_power=1000.0)
    base.update(kw)
    return Readings(**base)


def feed(rec, start_s, end_s, step=30, **kw):
    done = []
    for s in range(start_s, end_s + 1, step):
        d = rec.add(reading(T0 + s * S, **kw))
        if d is not None:
            done.append(d)
    return done


def test_a_missing_reading_is_noted_by_name_and_the_time_is_not_counted():
    rec = Recorder()
    feed(rec, 0, 600)
    feed(rec, 630, 750, battery_power=None)                      # five cycles with no battery power
    hh = feed(rec, 780, 1830)[0]
    assert hh.miss == {"battery_power": 150.0} and round(hh.seconds) == 1800 - 150
    assert hh.as_dict()["miss"] == {"battery_power": 150}


def test_a_long_gap_and_a_late_start_are_noted():
    rec = Recorder()
    feed(rec, 600, 900)                                          # the app started ten minutes into the half-hour
    hh = feed(rec, 900 + MAX_GAP_S + 30, 1830)[0]                # then stalled for over five minutes
    assert hh.miss["late_start"] == 600.0 and hh.miss["gap"] == MAX_GAP_S + 30
    assert "miss" not in Recorder_clean().as_dict()


def Recorder_clean():
    rec = Recorder()
    return feed(rec, 0, 1830)[0]


def test_coverage_summary_names_what_each_day_lacked(tmp_path):
    def rec(start, seconds, miss=None):
        r = {"start": start, "seconds": seconds}
        if miss:
            r["miss"] = miss
        return r
    day = [rec(f"2026-10-30T{h:02d}:{m:02d}:00+00:00", 1800) for h in range(24) for m in (0, 30)]
    day[6] = rec(day[6]["start"], 1620, {"grid_power": 180})
    day[7] = rec(day[7]["start"], 1620)                          # an old record: nothing says why
    (tmp_path / "2026-10-30.json").write_text(json.dumps(day))
    out = coverage.summary(str(tmp_path), date(2026, 10, 31))
    d = out["2026-10-30"]
    assert d["records"] == 48 and d["covered_pct"] == round(100 * (46 * 1800 + 2 * 1620) / 86400, 1)
    assert d["lost_min"] == {"grid_power": 3.0, "unexplained": 3.0}
    assert d["worst"][0] == {"lost_s": 180, "at": "03:00", "why": {"grid_power": 180}}


def _app(monkeypatch):
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine

    from pe_core.config import parse_config
    a = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    a.cfg = parse_config({"inputs": {"axle_event_start": {"entity": "sensor.axle_start"},
                                     "battery_soc": {"entity": "sensor.soc"}}})
    a.states = {}
    a.get_state = lambda eid, attribute=None: a.states.get(eid)
    return a, powerengine


def test_the_grid_event_sensors_are_held_through_a_short_outage_but_not_forever(monkeypatch):
    from datetime import datetime as real
    a, pe = _app(monkeypatch)
    good = {"state": "2026-10-31T20:00:00+00:00", "attributes": {}}
    a.states["sensor.axle_start"] = good
    assert a._state_all("sensor.axle_start") == good
    a.states["sensor.axle_start"] = {"state": "unavailable", "attributes": {}}
    assert a._state_all("sensor.axle_start") == good                     # held through the outage
    a.states["sensor.axle_start"] = {"state": "unknown", "attributes": {}}
    assert a._state_all("sensor.axle_start")["state"] == "unknown"       # "unknown" is no event scheduled: not held
    a.states["sensor.axle_start"] = {"state": "unavailable", "attributes": {}}
    held_at, st = a._event_held["sensor.axle_start"]
    a._event_held["sensor.axle_start"] = (held_at - timedelta(seconds=pe.EVENT_HOLD_S + 5), st)
    assert a._state_all("sensor.axle_start")["state"] == "unavailable"   # too long: dropped
    a.states["sensor.soc"] = {"state": "unavailable", "attributes": {}}
    assert a._state_all("sensor.soc")["state"] == "unavailable"          # other sensors are never held
    assert real is not None
