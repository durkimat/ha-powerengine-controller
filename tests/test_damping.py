"""Dampening tuning: restart hold-off and burst damping (0.9.12)."""
from datetime import datetime, timedelta, timezone

from pe_core.control import Write
from pe_core.damping import Damper, write_keys

T = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)
W1 = [Write("timed_charge_start_hour#1", 21, "number"), Write("timed_update_button#1", None, "button")]
W2 = [Write("timed_charge_current", 0, "number")]


def f(d, now, writes, want, rule="plan", restart=True, bursts=False):
    return d.filter(now, "2026-09-27", writes, want, rule, restart, bursts, 10, 5)


def test_write_keys():
    assert write_keys(W1 + W2) == {"slot1", "timed_charge_current"}


def test_restart_hold_off_holds_then_releases():
    d = Damper()
    d.restarted(T, 5)
    assert f(d, T + timedelta(minutes=1), W1, {"a": 1}) == [] and "restart hold-off" in d.last_reason
    assert f(d, T + timedelta(minutes=6), W1, {"a": 1}) == W1
    assert f(d, T + timedelta(minutes=1), W1, {"a": 1}, restart=False) == W1        # switched off


def test_safety_rules_never_wait():
    d = Damper()
    d.restarted(T, 5)
    for rule in ("axle_active", "car_charging", "reserve", "free_power", "pre_axle"):
        assert f(d, T + timedelta(minutes=1), W1, {"a": 1}, rule=rule) == W1


def test_burst_damping_first_change_goes_through_then_waits_for_a_steady_plan():
    d = Damper()
    assert f(d, T, W1, {"a": 1}, bursts=True) == W1                                 # first change: straight away
    d.wrote(T, W1)
    t = T + timedelta(minutes=3)
    assert f(d, t, W1, {"a": 2}, bursts=True) == []                                 # slot 1 again, 3 min later
    assert f(d, t + timedelta(minutes=2), W1, {"a": 2}, bursts=True) == []           # steady 2 min: still waiting
    assert f(d, t + timedelta(minutes=4), W1, {"a": 3}, bursts=True) == []           # changed again: clock restarts
    assert f(d, t + timedelta(minutes=9), W1, {"a": 3}, bursts=True) == W1           # steady 5 min: one write
    assert f(d, T + timedelta(minutes=3), W2, {"b": 1}, bursts=True) == W2           # a different key: no burst
    assert f(d, T + timedelta(minutes=3), W1, {"a": 2}, bursts=False) == W1          # off by default


def test_held_count():
    d = Damper()
    d.count_held("2026-09-27")
    d.count_held("2026-09-27")
    assert d.held == {"2026-09-27": 2}


def test_restart_hold_off_works_before_the_config_is_loaded():
    """0.9.12 called it at the very start of initialize(), before self.cfg existed, and the app failed to start."""
    import sys
    import types
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules.setdefault("appdaemon.plugins.hass.hassapi", hassapi)
    import powerengine
    a = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    a._damp_restart(T)                                   # no self.cfg yet: must not raise
    assert a.damper.hold_until == T + timedelta(minutes=5)
