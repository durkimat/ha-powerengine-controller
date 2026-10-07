"""Stopping the car charger during a grid event, and putting its mode back (pe_core/carstop.py)."""

from datetime import datetime, timedelta, timezone

from pe_core.carstop import CarStop
from pe_core.config import FEATURE_DEFAULTS, parse_config

T0 = datetime(2026, 10, 6, 18, 30, tzinfo=timezone.utc)
OPTIONS = ["Fast", "Eco", "Eco+", "Stopped"]


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def test_the_setting_is_on_by_default():
    assert FEATURE_DEFAULTS["axle_stop_car"] is True
    assert parse_config({}).features.get("axle_stop_car", True) is True
    assert parse_config({"features": {"axle_stop_car": False}}).features["axle_stop_car"] is False


def test_stops_the_charger_at_the_start_and_restores_the_mode_at_the_end():
    cs = CarStop()
    assert cs.step(at(-5), False, "Eco+", OPTIONS) is None            # nothing going on
    a = cs.step(at(0), True, "Eco+", OPTIONS)
    assert a.option == "Stopped" and cs.held and cs.previous == "Eco+"
    assert cs.step(at(1), True, "Stopped", OPTIONS) is None            # already stopped: no more writes
    assert cs.step(at(30), True, "Stopped", OPTIONS) is None
    b = cs.step(at(60), False, "Stopped", OPTIONS)                     # the event is over
    assert b.option == "Eco+" and not cs.held
    assert cs.step(at(61), False, "Eco+", OPTIONS) is None


def test_restores_the_mode_it_found_not_always_eco_plus():
    cs = CarStop()
    cs.step(at(0), True, "Fast", OPTIONS)
    assert cs.step(at(60), False, "Stopped", OPTIONS).option == "Fast"


def test_a_charger_already_stopped_is_left_alone():
    cs = CarStop()
    assert cs.step(at(0), True, "Stopped", OPTIONS) is None and not cs.held
    assert cs.step(at(60), False, "Stopped", OPTIONS) is None          # and it is not 'restored' to anything


def test_a_mode_changed_by_someone_else_during_the_event_is_not_overwritten_at_the_end():
    cs = CarStop()
    cs.step(at(0), True, "Eco+", OPTIONS)
    assert cs.step(at(50), False, "Fast", OPTIONS) is None             # you set Fast yourself: it stands
    assert not cs.held


def test_stops_again_if_something_sets_the_mode_back_but_only_a_few_times():
    cs = CarStop()
    cs.step(at(0), True, "Eco+", OPTIONS)
    assert cs.step(at(1), True, "Eco+", OPTIONS) is None               # too soon after the last write
    assert cs.step(at(3), True, "Eco+", OPTIONS).option == "Stopped"
    assert cs.step(at(6), True, "Eco+", OPTIONS).option == "Stopped"
    assert cs.step(at(9), True, "Eco+", OPTIONS).option == "Stopped"
    assert cs.step(at(12), True, "Eco+", OPTIONS) is None              # gives up rather than fight for an hour
    assert cs.step(at(60), False, "Stopped", OPTIONS).option == "Eco+"   # and still puts it back at the end


def test_an_unreadable_mode_changes_nothing_and_stays_held():
    cs = CarStop()
    assert cs.step(at(0), True, None, OPTIONS) is None and not cs.held
    cs.step(at(1), True, "Eco+", OPTIONS)
    assert cs.step(at(60), False, "unavailable", OPTIONS) is None and cs.held       # try again once it reads
    assert cs.step(at(61), False, "Stopped", OPTIONS).option == "Eco+"


def test_restores_when_the_setting_or_active_mode_goes_away_mid_event():
    cs = CarStop()
    cs.step(at(0), True, "Eco+", OPTIONS)
    assert cs.step(at(10), False, "Stopped", OPTIONS).option == "Eco+"  # want_stop False covers both


def test_survives_a_restart_in_the_middle_of_an_event():
    cs = CarStop()
    cs.step(at(0), True, "Eco", OPTIONS)
    again = CarStop.from_dict(cs.to_dict())
    assert again.held and again.step(at(61), False, "Stopped", OPTIONS).option == "Eco"
    assert not CarStop.from_dict(None).held and not CarStop.from_dict({"held": "x", "previous": 3}).previous


def test_uses_the_chargers_own_spelling_and_a_fallback():
    cs = CarStop()
    assert cs.step(at(0), True, "ECO+", ["fast", "eco", "eco+", "STOPPED"]).option == "STOPPED"
    assert cs.step(at(60), False, "STOPPED", ["fast", "eco", "eco+", "STOPPED"]).option == "eco+"
    odd = CarStop(True, "Solar only")                                  # a mode the charger no longer offers
    assert odd.step(at(0), False, "Stopped", OPTIONS).option == "Eco+"


# --- the app's use of it ------------------------------------------------------------------

def _app(monkeypatch, tmp_path, features=None, mode="active", ev_mode="Eco+", entity="select.zappi_charge_mode"):
    import sys
    import types

    from pe_core.readings import Readings
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine
    a = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    a.cfg = parse_config({"inputs": {"ev_charge_mode": {"entity": entity}}, "features": features or {}})
    a._demo = None
    a.mode = types.SimpleNamespace(effective=mode)
    a._save_path = lambda: str(tmp_path / "config.yaml")
    a._test_running = lambda: False
    a.calls, a.logs = [], []
    a.call_service = lambda svc, **kw: a.calls.append((svc, kw))
    a.log = lambda msg, **kw: a.logs.append(msg)
    a.get_state = lambda eid, attribute=None: OPTIONS if attribute == "options" else None
    a.readings = lambda active, mode_text: Readings(now=T0, axle_active=active, ev_mode=mode_text)
    return a


def test_app_stops_and_restores_the_charger_and_keeps_it_across_a_restart(monkeypatch, tmp_path):
    a = _app(monkeypatch, tmp_path)
    a._car_stop_step(a.readings(False, "Eco+"))
    assert a.calls == []                                               # no event: nothing touched
    a._car_stop_step(a.readings(True, "Eco+"))
    assert a.calls == [("select/select_option", {"entity_id": "select.zappi_charge_mode", "option": "Stopped"}),
                       ("logbook/log", {"name": "PowerEngine", "message": a.calls[1][1]["message"]})]
    a.calls.clear()
    a._car_stop_step(a.readings(True, "Stopped"))
    assert a.calls == []                                               # already stopped: no repeat writes
    a._car_stop = None                                                 # the app restarts mid-event...
    a._car_stop_step(a.readings(False, "Stopped"))                     # ...and the event ends
    assert a.calls[0] == ("select/select_option", {"entity_id": "select.zappi_charge_mode", "option": "Eco+"})
    a.calls.clear()
    a._car_stop = None
    a._car_stop_step(a.readings(False, "Eco+"))
    assert a.calls == []                                               # nothing left to undo


def test_app_leaves_the_charger_alone_when_it_should(monkeypatch, tmp_path):
    for kw in ({"features": {"axle_stop_car": False}}, {"features": {"axle": False}}, {"mode": "passive"},
               {"entity": "sensor.zappi_charge_mode"}):
        a = _app(monkeypatch, tmp_path, **kw)
        a._car_stop_step(a.readings(True, "Eco+"))
        assert a.calls == [], kw
