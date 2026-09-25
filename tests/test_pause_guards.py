"""Pause, handover guards, leaving Active and supervised test writes (0.5.13)."""
import sys
import types
from datetime import datetime, timezone

import pytest

from pe_core import testwrite
from pe_core.config import parse_config
from pe_core.control import readback_mismatches, release, writes_needed
from pe_core.entities import ENTITIES
from pe_core.modes import effective_mode, guard_problems

GUARDED = {"operation": {"mode": "active"}, "inputs": {
    "guard_read_only": {"entity": "switch.predbat_set_read_only"},
    "guard_off_1": {"entity": "automation.charge_house_battery_on"}}}
SAFE = {"switch.predbat_set_read_only": "on", "automation.charge_house_battery_on": "off"}


# --- guards and mode --------------------------------------------------------------------

def test_no_guards_mapped_is_a_problem():
    assert guard_problems(parse_config({}), lambda e: None) == ["no handover guards are mapped"]


def test_guards_safe():
    assert guard_problems(parse_config(GUARDED), SAFE.get) == []


def test_predbat_not_read_only_or_legacy_on_is_reported():
    states = {"switch.predbat_set_read_only": "off", "automation.charge_house_battery_on": "on"}
    probs = guard_problems(parse_config(GUARDED), states.get)
    assert len(probs) == 2 and "must be on" in probs[0] and "must be off" in probs[1]


def test_unavailable_guard_is_unsafe():
    states = dict(SAFE, **{"switch.predbat_set_read_only": "unavailable"})
    assert guard_problems(parse_config(GUARDED), states.get)


def test_guards_refuse_active():
    m = effective_mode(parse_config(GUARDED), build_supports_active=True, guards=["x is on (must be off)"])
    assert (m.configured, m.effective) == ("active", "passive") and "Active refused" in m.reason


def test_pause_gives_paused():
    m = effective_mode(parse_config(GUARDED), build_supports_active=True, paused=True)
    assert m.effective == "paused"


def test_guards_win_over_pause():
    m = effective_mode(parse_config(GUARDED), build_supports_active=True, guards=["x"], paused=True)
    assert m.effective == "passive"


def test_passive_ignores_guards_and_pause():
    m = effective_mode(parse_config({}), build_supports_active=True, guards=["x"], paused=True)
    assert m.effective == "passive" and "Passive" in m.reason


def test_this_build_still_refuses_active_even_with_safe_guards():
    assert effective_mode(parse_config(GUARDED)).effective == "passive"


def test_pause_switch_entity():
    sw = [e for e in ENTITIES if e.entity_id == "switch.pe_ctl_pause"]
    assert sw and sw[0].options["command_topic"] == sw[0].options["state_topic"] and sw[0].options["retain"]


# --- release ------------------------------------------------------------------------------

def test_release_closes_both_windows_in_self_use():
    want = release()
    assert want["storage_mode"] == "Self-Use"
    assert all(v == 0 for k, v in want.items() if k != "storage_mode")
    assert "timed_charge_current" not in want            # currents are left alone


def test_release_writes_only_what_differs_and_presses_button():
    have = {k: 0 for k in release()} | {"storage_mode": "Self-Use", "timed_charge_end_hour": 5}
    writes = writes_needed(release(), have)
    assert [w.role for w in writes] == ["timed_charge_end_hour", "timed_update_button"]


def test_readback():
    assert readback_mismatches({"a": 5, "b": "Self-Use"}, {"a": "5.0", "b": "Self-Use"}) == []
    assert readback_mismatches({"a": 5}, {"a": "10"}) == ["a"]


# --- test-write validation ---------------------------------------------------------------

@pytest.mark.parametrize("data,guards,missing,running,why", [
    ({"action": "hold", "confirm": True}, [], [], True, "already running"),
    ({"action": "backup", "confirm": True}, [], [], False, "unknown action"),
    ({"action": "hold"}, [], [], False, "not confirmed"),
    ({"action": "hold", "confirm": True}, [], ["storage_mode"], False, "not mapped"),
    ({"action": "hold", "confirm": True}, ["x"], [], False, "guards"),
    ({"action": "hold", "confirm": True, "minutes": 11}, [], [], False, "1 to 10"),
    ({"action": "charge", "confirm": True, "power_w": 9000}, [], [], False, "power_w"),
])
def test_validate_refuses(data, guards, missing, running, why):
    req, err = testwrite.validate(data, guards, missing, running)
    assert req is None and why in err


def test_validate_ok():
    req, err = testwrite.validate({"action": "charge", "confirm": True, "minutes": "3", "power_w": 1000},
                                  [], [], False)
    assert err is None and req == {"action": "charge", "minutes": 3, "power_w": 1000.0}


def test_test_window_ends_after_test_and_before_midnight():
    t = datetime(2026, 9, 25, 23, 55)
    assert testwrite.end_time(t, 10) == datetime(2026, 9, 25, 23, 59)
    assert testwrite.end_time(datetime(2026, 9, 25, 10, 0), 5) == datetime(2026, 9, 25, 10, 7)


# --- the adapter, with a stub AppDaemon ---------------------------------------------------

@pytest.fixture
def app(monkeypatch):
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine

    roles = ["timed_charge_start_hour", "timed_charge_start_minute", "timed_charge_end_hour",
             "timed_charge_end_minute", "timed_charge_current", "timed_discharge_start_hour",
             "timed_discharge_start_minute", "timed_discharge_end_hour", "timed_discharge_end_minute",
             "timed_discharge_current", "storage_mode", "timed_update_button"]
    cfg = dict(GUARDED, operation={"mode": "passive"})
    dom = {"storage_mode": "select", "timed_update_button": "button"}
    ent = {r: f"{dom.get(r, 'number')}.{r}" for r in roles}
    cfg["inputs"] = dict(cfg["inputs"], **{r: {"entity": e} for r, e in ent.items()})
    a = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    a.cfg = parse_config(cfg)
    a.tz = timezone.utc
    a.mode = effective_mode(a.cfg)
    a.states = {**SAFE, **{e: 0 for e in ent.values()}, "select.storage_mode": "Self-Use"}
    a.calls, a.published, a.timers, a.fired = [], [], [], []

    def call_service(service, **kw):
        a.calls.append((service, kw))
        if service.startswith(("number/", "select/")):
            a.states[kw["entity_id"]] = kw.get("value", kw.get("option"))
    a.get_state = lambda eid, attribute=None: a.states.get(eid)
    a.call_service = call_service
    a.log = lambda *args, **kw: None
    a.run_in = lambda cb, delay, **kw: a.timers.append((cb, kw)) or len(a.timers)
    a.run_every = lambda cb, start, every, **kw: "every"
    a.cancel_timer = lambda h: None
    a.fire_event = lambda ev, **kw: a.fired.append(kw)
    a._publish_state = lambda key, state, attrs=None: a.published.append((key, state, attrs))
    a._notify = lambda event, msg: None
    a._logbook = lambda msg: None
    a._battery_now = lambda: {"soc": 50, "battery_w": 0}
    a.writes = types.SimpleNamespace(observed=lambda day, eid: None)
    a._today = lambda: "2026-09-25"
    return a


def run_timers(a):
    while a.timers:
        cb, kw = a.timers.pop(0)
        cb(kw)


def test_supervised_hold_writes_reads_back_then_reverts(app):
    app._on_test("pe_test_write", {"action": "hold", "minutes": 2, "confirm": True}, {})
    assert app._test.status == "running"
    assert app.states["number.timed_charge_end_hour"] != 0 or app.states["number.timed_charge_end_minute"] != 0
    assert app.states["number.timed_charge_current"] == 0
    assert any(s == "button/press" for s, _ in app.calls)
    run_timers(app)                                   # start check, then end (revert), then end check
    assert app._test.status == "passed", app._test.problems
    assert all(app.states[f"number.{r}"] == 0 for r in release() if r != "storage_mode")


def test_readback_failure_marks_test_failed(app):
    app._on_test("pe_test_write", {"action": "charge", "minutes": 1, "confirm": True, "power_w": 1040}, {})
    app.states["number.timed_charge_current"] = 5         # the inverter didn't take 20 A
    run_timers(app)
    assert app._test.status == "failed" and "timed_charge_current" in app._test.problems[0]


def test_test_refused_when_guards_unsafe(app):
    app.states["switch.predbat_set_read_only"] = "off"
    app._on_test("pe_test_write", {"action": "hold", "confirm": True}, {})
    assert app._test.status == "refused" and not app.calls


def test_test_refused_while_in_control(app):
    app.mode = effective_mode(parse_config(GUARDED), build_supports_active=True)
    app._on_test("pe_test_write", {"action": "hold", "confirm": True}, {})
    assert app._test.status == "refused" and "pause it first" in app._test.problems[0]


def test_stop_reverts_early(app):
    app._on_test("pe_test_write", {"action": "discharge", "minutes": 10, "confirm": True}, {})
    app.timers.clear()
    app._on_test("pe_test_write", {"action": "stop"}, {})
    run_timers(app)
    assert app._test.status == "stopped"
    assert app.states["number.timed_discharge_end_hour"] == 0


def test_pause_from_active_releases_once(app):
    active = effective_mode(parse_config(GUARDED), build_supports_active=True)
    paused = effective_mode(parse_config(GUARDED), build_supports_active=True, paused=True)
    app.states["number.timed_charge_end_hour"] = 5
    app._leave_active(active, paused)
    assert app.states["number.timed_charge_end_hour"] == 0
    app.calls.clear()
    app._leave_active(paused, paused)
    assert not app.calls


def test_guard_trip_writes_nothing(app):
    active = effective_mode(parse_config(GUARDED), build_supports_active=True)
    tripped = effective_mode(parse_config(GUARDED), build_supports_active=True, guards=["x"])
    app.states["number.timed_charge_end_hour"] = 5
    app._leave_active(active, tripped)
    assert not app.calls
