"""RAM remote control (0.9.16)."""
from datetime import datetime, timedelta, timezone

import pytest
from test_pause_guards import GUARDED, SAFE, app, run_timers  # noqa: F401  (fixture)

from pe_core.config import parse_config
from pe_core.decide import EXPORT, GRID_CHARGE, HOLD, SELF_USE, Decision
from pe_core.modes import effective_mode
from pe_core.ramcontrol import Command, RamController, command_for

T = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)
RC = {"select.solis_inverter_battery_control_override": "Off",
      "number.solis_inverter_battery_control_override_charge_power": 0,
      "number.solis_inverter_battery_control_override_discharge_power": 0}


def test_command_for_each_action():
    assert command_for(GRID_CHARGE, 3000, 4800, 4800) == Command("Force charge", 3000)
    assert command_for(GRID_CHARGE, None, 4800, 4800) == Command("Force charge", 4800)
    assert command_for(HOLD, None, 4800, 4800) == Command("Force charge", 0)
    assert command_for(EXPORT, 9000, 4800, 4800) == Command("Force discharge", 4800)
    assert command_for(SELF_USE, None, 4800, 4800) == Command("Off", 0)
    assert [w.role for w in Command("Force discharge", 2000).writes()] == ["rc_discharge_power", "rc_mode",
                                                                         "rc_discharge_power"]
    assert [w.role for w in Command("Off").writes()] == ["rc_mode"]


def test_changes_go_at_once_and_force_commands_are_refreshed():
    c = RamController()
    ch = Command("Force charge", 3000)
    w, why = c.step(T, ch, timedelta(minutes=1))
    assert why == "change" and len(w) == 3
    c.done(T, "2026-09-27", ch, why)
    assert c.step(T + timedelta(seconds=30), ch, timedelta(minutes=1)) == ([], None)
    assert c.step(T + timedelta(seconds=30), Command("Force charge", 3050), timedelta(minutes=1)) == ([], None)
    assert c.step(T + timedelta(seconds=61), ch, timedelta(minutes=1))[1] == "refresh"
    assert c.step(T + timedelta(seconds=30), Command("Force charge", 2000), timedelta(minutes=1))[1] == "change"
    off = Command("Off")
    c.done(T, "2026-09-27", off, "change")
    assert c.step(T + timedelta(minutes=10), off, timedelta(minutes=1)) == ([], None)   # Off isn't refreshed


def test_following_check():
    c = RamController()
    c.done(T, "d", Command("Force charge", 3000), "change")
    assert c.check_following(T + timedelta(seconds=30), 500, 50, 12) == "waiting"         # grace
    assert c.check_following(T + timedelta(minutes=2), -2900, 50, 12) == "ok"
    assert c.check_following(T + timedelta(minutes=3), 500, 50, 12) == "waiting"
    assert c.check_following(T + timedelta(minutes=6, seconds=1), 500, 50, 12) == "not following"
    assert c.check_following(T + timedelta(minutes=7), 500, 97, 12) == "ok"               # near full: tapering
    c.done(T, "d", Command("Force charge", 0), "change")
    assert c.check_following(T + timedelta(minutes=2), 100, 50, 12) == "ok"               # hold
    c.done(T, "d", Command("Force discharge", 2000), "change")
    assert c.check_following(T + timedelta(minutes=2), 1950, 50, 12) == "ok"


@pytest.fixture
def ramapp(app):  # noqa: F811
    cfg = dict(GUARDED, system={"control_method": "ram_remote"}, inputs=dict(app.cfg.raw["inputs"]))
    app.cfg = parse_config(cfg)
    app.mode = effective_mode(app.cfg, build_supports_active=True)
    app.states.update(RC)
    base = app.get_state
    app.get_state = lambda eid=None, attribute=None: dict(app.states) if eid is None else base(eid)
    app._publish_if_changed = lambda key, state, attrs: app.published.append((key, state, attrs))
    app._test_running = lambda: False
    app._params = lambda readings=None: __import__("pe_core.planner", fromlist=["Params"]).Params()
    app.journal = None
    return app


def R(now, bw=0.0, soc=50.0):
    return type("R", (), {"now": now, "battery_power": bw, "battery_soc": soc})()


def test_ram_control_in_the_adapter(ramapp):
    a = ramapp
    a._control(R(T), Decision(GRID_CHARGE, "plan", "charge", power_w=3000))
    sel = "select.solis_inverter_battery_control_override"
    assert a.states[sel] == "Force charge"
    assert a.states["number.solis_inverter_battery_control_override_charge_power"] == 3000
    assert not any(c[1].get("entity_id", "").startswith("number.timed_")
                   for c in a.calls if c[0] != "select/select_option")
    method = [p for p in a.published if p[0] == "state_control_method"][-1]
    assert method[1] == "RAM remote control" and method[2]["command"] == "Force charge at 3000 W"
    a.calls.clear()
    a._control(R(T + timedelta(seconds=30), -2900), Decision(GRID_CHARGE, "plan", "charge", power_w=3000))
    assert not a.calls                                              # nothing new yet
    a._control(R(T + timedelta(seconds=70), -2900), Decision(GRID_CHARGE, "plan", "charge", power_w=3000))
    assert [c[0] for c in a.calls] == ["number/set_value", "select/select_option", "number/set_value"]  # refresh
    a._control(R(T + timedelta(seconds=90), -2900), Decision(SELF_USE, "plan", "idle"))
    assert a.states[sel] == "Off"
    # pausing switches remote control off
    a._control(R(T + timedelta(seconds=120)), Decision(EXPORT, "plan", "sell", power_w=2000))
    assert a.states[sel] == "Force discharge"
    paused = effective_mode(a.cfg, build_supports_active=True, paused=True)
    a._leave_active(a.mode, paused)
    assert a.states[sel] == "Off"


def test_falls_back_to_timed_windows_without_the_entities(ramapp):
    a = ramapp
    for e in RC:
        a.states.pop(e)
    assert a._control_method() == "timed_windows" and "not found" in a._ram_fallback


def test_switching_back_to_timed_windows_turns_remote_control_off(ramapp):
    a = ramapp
    a._control(R(T), Decision(EXPORT, "plan", "sell", power_w=2000))
    a.cfg = parse_config(dict(a.cfg.raw, system={"control_method": "timed_windows"}))
    a._control(R(T + timedelta(minutes=1)), Decision(SELF_USE, "plan", "idle"))
    assert a.states["select.solis_inverter_battery_control_override"] == "Off"


def test_switch_cost_follows_the_control_method():
    from pe_core.planner import params_from
    timed = parse_config({"safety": {"window_switch_cost_p": 5, "ram_switch_cost_p": 0.5}})
    ram = parse_config({"safety": {"window_switch_cost_p": 5, "ram_switch_cost_p": 0.5},
                        "system": {"control_method": "ram_remote"}})
    assert params_from(timed).switch_cost_p == 5 and params_from(ram).switch_cost_p == 0.5


def test_power_is_resent_after_a_change(ramapp):
    a = ramapp
    a._control(R(T), Decision(GRID_CHARGE, "plan", "charge", power_w=3000))
    relatch = [(cb, kw) for cb, kw in a.timers if cb.__name__ == "_ram_relatch"]
    assert relatch and relatch[-1][1] == {"role": "rc_charge_power", "watts": 3000}
    a.calls.clear()
    relatch[-1][0](relatch[-1][1])
    assert a.calls == [("number/set_value", {"entity_id": "number.solis_inverter_battery_control_override_charge_power",
                                             "value": 3000})]
