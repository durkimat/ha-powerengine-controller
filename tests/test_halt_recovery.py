"""Control stopped because inverter writes did not read back: it says so everywhere and tries again by itself."""
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from pe_core.modes import ModeDecision


@pytest.fixture
def app():
    sys.modules.pop("powerengine", None)
    import powerengine

    a = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    a.tz = timezone.utc
    a.mode = ModeDecision("active", "active", "Active: PowerEngine is in control.")
    a.cfg = None
    a.timers, a.notes, a.published, a.logs, a.forgot = [], [], [], [], []
    a.__dict__["_host"] = types.SimpleNamespace(call_later=lambda cb, secs, **kw: a.timers.append((cb.__name__, secs)),
                                                log=lambda msg, *args, **kw: a.logs.append(msg))
    a.log = lambda msg, *args, **kw: a.logs.append(msg)
    a._notify = lambda event, msg: a.notes.append(msg)
    a._publish_state = lambda key, state, attrs=None: a.published.append((key, state, attrs))
    a._evaluate = lambda: None
    a._health = lambda: None
    a._ram_forget = lambda: a.forgot.append(True)
    return a


def due(a):
    a._halt_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)      # the wait is over


def test_each_stop_waits_longer_up_to_an_hour(app):
    waits = []
    for _ in range(7):
        app._halt_control(["storage_mode"])
        waits.append(app.timers[-1][1] / 60)
        due(app)
        app._lift_halt()
    assert waits == [5, 10, 20, 40, 60, 60, 60]


def test_a_second_failed_write_in_the_same_stop_changes_nothing(app):
    app._halt_control(["storage_mode"])
    app._halt_control(["timed_charge_start_hour#1"])
    assert app._halt_count == 1 and len(app.timers) == 1 and len(app.notes) == 1


def test_stopped_means_stopped_until_the_wait_is_over_and_inputs_are_back(app):
    app._halt_control(["storage_mode"])
    app._lift_halt()
    assert app._halted                                            # an older timer fired early: still waiting
    due(app)
    app.mode = ModeDecision("active", "unconfigured", "inputs not ready")
    app._lift_halt()
    assert app._halted and app.timers[-1] == ("_lift_halt", 60)   # wait for the inputs first
    app.mode = ModeDecision("active", "active", "ok")
    app._lift_halt()
    assert not app._halted and app._halt_probation and app.forgot  # resumed; the command is sent afresh


def test_control_is_back_only_when_the_setting_that_failed_reads_back(app):
    app._halt_control(["storage_mode"])
    due(app)
    app._lift_halt()
    app._writes_confirmed(["timed_charge_start_hour#1"])           # something else read back: not recovery
    assert app._halt_probation and app._halt_count == 1 and len(app.notes) == 1
    app._writes_confirmed(["storage_mode"])
    assert not app._halt_probation and app._halt_count == 0
    assert app.notes[-1][0] == "control:recovered"


def test_an_hour_without_another_stop_forgets_the_growing_wait(app):
    app._halt_control(["storage_mode"])
    due(app)
    app._lift_halt()
    assert ("_end_probation", 3600) in app.timers
    app._end_probation()
    assert app._halt_count == 0 and not app._halt_probation


def test_every_status_line_says_control_is_stopped(app):
    assert app._stopped_why() is None
    app._halt_control(["storage_mode"])
    why = app._stopped_why()
    assert "control is stopped" in why and "Trying again at" in why
    app._publish_status()
    key, word, attrs = app.published[-1]
    assert (key, word) == ("state_status", "Stopped") and attrs["reason"] == why
    assert app.notes[0][0] == "control:verify" and "try again at" in app.notes[0][2]
    due(app)
    app._lift_halt()
    assert app._stopped_why() is None
