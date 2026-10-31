import json
import sys
import types
from datetime import datetime, timedelta, timezone

from pe_core.config import FEATURE_DEFAULTS, SAFETY, parse_config
from pe_core.readings import Readings
from pe_core.setback import Setback, window

START = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
END = START + timedelta(hours=1)
M = timedelta(minutes=1)
EV = START.isoformat()


def test_the_window_starts_the_pump_overrun_before_the_event():
    assert window(START, END, 15) == (START - 15 * M, END)
    assert window(None, END, 15) is None and window(START, START, 15) is None


def test_defaults_are_off_and_fifteen_minutes():
    assert FEATURE_DEFAULTS["axle_heating_setback"] is False
    assert SAFETY["heating_pump_overrun_min"] == (15, 0, 120)
    assert parse_config({}).safety["heating_pump_overrun_min"] == 15
    assert parse_config({"safety": {"heating_pump_overrun_min": 20}}).safety["heating_pump_overrun_min"] == 20


def test_turns_on_once_and_off_at_the_end():
    sb = Setback()
    assert sb.step(False, None, "off") is None
    a = sb.step(True, EV, "off", 15)
    assert a.service == "turn_on" and sb.held
    assert sb.step(True, EV, "on", 15) is None                        # on already: no repeat
    assert sb.step(False, EV, "on").service == "turn_off" and not sb.held
    assert sb.step(False, EV, "off") is None


def test_a_switch_that_was_already_on_is_left_alone():
    sb = Setback()
    assert sb.step(True, EV, "on") is None and not sb.held
    assert sb.step(False, EV, "on") is None                           # and not switched off at the end


def test_turning_it_off_during_the_setback_stands_for_that_event():
    sb = Setback()
    sb.step(True, EV, "off")
    assert sb.step(True, EV, "off") is None and not sb.held           # the owner turned it off: not switched on again
    assert sb.step(False, EV, "off") is None
    assert sb.step(True, (START + 3 * 60 * M).isoformat(), "off").service == "turn_on"      # the next event, again


def test_unreadable_switch_does_nothing_and_a_restart_keeps_the_state():
    sb = Setback()
    assert sb.step(True, EV, "unavailable") is None and sb.step(True, EV, None) is None
    sb.step(True, EV, "off")
    again = Setback.from_dict(json.loads(json.dumps(sb.to_dict())))
    assert again.held and again.step(False, EV, "on").service == "turn_off"
    assert Setback.from_dict("junk").held is False


def _app(monkeypatch, tmp_path, features=None, mode="active", entity="input_boolean.heating_off", safety=None):
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine
    a = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    a.cfg = parse_config({"inputs": {"heating_setback": {"entity": entity}} if entity else {},
                          "features": {"axle_heating_setback": True, **(features or {})}, "safety": safety or {}})
    a._demo = None
    a.mode = types.SimpleNamespace(effective=mode)
    a._save_path = lambda: str(tmp_path / "config.yaml")
    a._test_running = lambda: False
    a.calls, a.logs, a.state = [], [], "off"
    a.call_service = lambda svc, **kw: a.calls.append((svc, kw))
    a.log = lambda msg, **kw: a.logs.append(msg)
    a.get_state = lambda eid, attribute=None: a.state
    return a


def _r(minutes_from_start):
    return Readings(now=START + minutes_from_start * M, axle_start=START, axle_end=END)


def test_app_switches_on_fifteen_minutes_before_and_off_after_and_survives_a_restart(monkeypatch, tmp_path):
    a = _app(monkeypatch, tmp_path)
    a._heating_setback_step(_r(-20))
    assert a.calls == []                                               # too early
    a._heating_setback_step(_r(-15))
    assert a.calls[0] == ("input_boolean/turn_on", {"entity_id": "input_boolean.heating_off"})
    a.calls.clear(), setattr(a, "state", "on")
    a._heating_setback_step(_r(30))
    assert a.calls == []
    a._setback = None                                                  # the app restarts mid-event
    a._heating_setback_step(_r(61))
    assert a.calls[0] == ("input_boolean/turn_off", {"entity_id": "input_boolean.heating_off"})


def test_app_does_nothing_when_off_passive_or_unset(monkeypatch, tmp_path):
    for kw in ({"features": {"axle_heating_setback": False}}, {"features": {"axle": False}}, {"mode": "passive"},
               {"entity": None}):
        a = _app(monkeypatch, tmp_path, **kw)
        a._heating_setback_step(_r(-10))
        assert a.calls == [], kw


def test_the_overrun_is_the_setting(monkeypatch, tmp_path):
    a = _app(monkeypatch, tmp_path, safety={"heating_pump_overrun_min": 30})
    a._heating_setback_step(_r(-31))
    assert a.calls == []
    a._heating_setback_step(_r(-30))
    assert a.calls[0][0] == "input_boolean/turn_on"


def test_a_wrong_kind_of_entity_is_refused_when_the_config_is_read():
    import pytest
    from pe_core.config import ConfigError
    with pytest.raises(ConfigError):
        parse_config({"inputs": {"heating_setback": {"entity": "light.kitchen"}}})
