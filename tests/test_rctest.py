"""Supervised tests of the Solis remote-control (RC) registers (0.9.5)."""
import pytest
from test_pause_guards import app, run_timers  # noqa: F401  (fixture)

from pe_core import rctest, testwrite

RC = {"select.solis_inverter_battery_control_override": "Off",
      "number.solis_inverter_battery_control_override_charge_power": 0,
      "number.solis_inverter_battery_control_override_discharge_power": 0}


def test_find_entities_prefers_solis():
    ids = ["select.other_battery_control_override", *RC, "sensor.x"]
    f = rctest.find_entities(ids)
    assert f == {"rc_mode": "select.solis_inverter_battery_control_override",
                 "rc_charge_power": "number.solis_inverter_battery_control_override_charge_power",
                 "rc_discharge_power": "number.solis_inverter_battery_control_override_discharge_power"}
    assert rctest.missing_roles({}, "rc_discharge") == ["rc_mode", "rc_discharge_power"]


@pytest.mark.parametrize("data,why", [
    ({"action": "rc_failsafe", "confirm": True, "minutes": 3}, "4 to 35"),
    ({"action": "rc_charge", "confirm": True, "minutes": 16}, "1 to 15"),
    ({"action": "rc_charge", "confirm": True, "power_w": 0}, "power_w"),
])
def test_validate_rc(data, why):
    req, err = testwrite.validate(data, [], [], False)
    assert req is None and why in err
    assert testwrite.validate({"action": "rc_hold", "confirm": True, "power_w": 0}, [], [], False)[1] is None


def s(t, b):
    return {"time": f"2026-09-27T20:{t:02d}:00+00:00", "battery_w": b}


def test_judge():
    assert rctest.judge("rc_charge", 2000, [s(0, 300), s(1, -1950)])[0] == "worked"
    assert rctest.judge("rc_charge", 2000, [s(0, 300), s(1, 200)])[0] == "no effect"
    assert rctest.judge("rc_discharge", 2000, [s(0, 2010)])[0] == "worked"
    assert rctest.judge("rc_hold", 0, [s(0, 40), s(1, -120)])[0] == "worked"
    assert rctest.judge("rc_hold", 0, [s(0, 800)])[0] == "no effect"
    assert rctest.judge("rc_charge", 2000, [])[0] == "inconclusive"
    stop = "2026-09-27T20:02:00+00:00"
    fs = [s(1, -2000), s(2, -2000), s(3, -2000), s(4, 400), s(5, 350)]
    v, why = rctest.judge("rc_failsafe", 2000, fs, stop)
    assert v == "reverted" and "20:05:00" in why
    assert rctest.judge("rc_failsafe", 2000, fs[:3], stop)[0] == "did not revert"
    assert rctest.judge("rc_failsafe", 2000, [s(1, 100)], stop)[0] == "inconclusive"


@pytest.fixture
def rcapp(app):  # noqa: F811
    app.states.update(RC)
    base = app.get_state
    app.get_state = lambda eid=None, attribute=None: dict(app.states) if eid is None else (
        ["Off", "Force charge", "Force discharge"] if attribute == "options" else base(eid))
    app.samples = [-1900, -2000, -1950]
    app._battery_now = lambda: {"soc": 50, "battery_w": app.samples[0] if app.samples else 0, "grid_w": 2300}
    return app


def fire_everything(a):
    while a.timers:
        cb, kw = a.timers.pop(0)
        if cb.__name__ == "_rc_end":
            a._rc_sample({})
        cb(kw)


def test_rc_charge_runs_and_switches_off(rcapp):
    rcapp._on_test("pe_test_write", {"action": "rc_charge", "minutes": 3, "confirm": True, "power_w": 2000}, {})
    assert rcapp._test.status == "running", rcapp._test.problems
    assert rcapp.states["select.solis_inverter_battery_control_override"] == "Force charge"
    assert rcapp.states["number.solis_inverter_battery_control_override_charge_power"] == 2000
    fire_everything(rcapp)
    assert rcapp.states["select.solis_inverter_battery_control_override"] == "Off"
    assert rcapp._test.verdict == "worked" and rcapp._test.status == "passed", rcapp._test.problems


def test_rc_failsafe_reloads_without_writing_off(rcapp):
    rcapp._on_test("pe_test_write", {"action": "rc_failsafe", "minutes": 8, "confirm": True}, {})
    stop = [cb for cb, _ in rcapp.timers if cb.__name__ == "_rc_stop_resending"]
    assert stop
    rcapp._rc_sample({})
    stop[0]({})
    assert ("homeassistant/reload_config_entry",
            {"entity_id": "select.solis_inverter_battery_control_override"}) in rcapp.calls
    assert rcapp.states["select.solis_inverter_battery_control_override"] == "Force charge"   # Off not written


def test_rc_refused_without_entities(app):  # noqa: F811
    app.get_state = lambda eid=None, attribute=None: dict(app.states) if eid is None else app.states.get(eid)
    app._on_test("pe_test_write", {"action": "rc_charge", "confirm": True}, {})
    assert app._test.status == "refused" and "not found" in app._test.problems[0] and not app.calls
