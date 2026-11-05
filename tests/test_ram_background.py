"""Remote-control commands are sent from a worker thread under AppDaemon (pe_core/background.py), so the thread that
runs the engine's callbacks doesn't wait 2 to 4 seconds for each call to the inverter. Order, one at a time, and what
follows a send running back on the callback thread are what these check, against an inverter that is slow to answer."""

import importlib
import sys
import threading
import time
from datetime import datetime, timezone

from pe_core import ramcontrol
from pe_core.background import Worker
from pe_core.rctest import OPTION_CHARGE, OPTION_DISCHARGE, OPTION_OFF

APP = __import__("pathlib").Path(__file__).resolve().parents[1] / "apps" / "powerengine"
RC = {"rc_mode": "select.mode", "rc_charge_power": "number.charge", "rc_discharge_power": "number.discharge"}
WAIT = 5.0


def powerengine():
    sys.path.insert(0, str(APP))
    return importlib.import_module("powerengine")


class SlowInverter:
    """call_service that takes `delay` seconds and records what it was asked and on which thread."""

    def __init__(self, delay=0.05):
        self.delay, self.calls, self.threads, self.fail = delay, [], set(), set()

    def __call__(self, service, **kw):
        time.sleep(self.delay)
        self.threads.add(threading.get_ident())
        if kw.get("entity_id") in self.fail:
            raise RuntimeError("timed out")
        self.calls.append((service, kw["entity_id"], kw.get("value") if "value" in kw else kw.get("option")))


class FakeHost:
    background_writes = True

    def __init__(self, inverter):
        self.inverter, self.later, self.logged = inverter, [], []
        self.callback_thread = threading.get_ident()

    def call_service(self, service, **kw):
        return self.inverter(service, **kw)

    def get_state(self, eid, *a, **k):
        return "before"

    def run_in(self, callback, delay, **kw):
        self.later.append((callback, delay, kw))        # run by `settle`, on the test's own (callback) thread
        return f"timer-{len(self.later)}"

    def log(self, msg, *a, **k):
        self.logged.append(msg)


class Journal:
    def __init__(self):
        self.rows, self.saved = [], 0

    def add(self, now, eid, value, before, why):
        self.rows.append((eid, value, before, why))

    def save(self, now):
        self.saved += 1


def make(delay=0.05):
    pe = powerengine()
    inverter = SlowInverter(delay)
    engine = object.__new__(pe.Engine)
    engine.__dict__["_host"] = FakeHost(inverter)
    logs = []
    engine.__dict__["log"] = lambda msg, level="INFO", **k: logs.append((level, msg))
    engine.__dict__["journal"] = Journal()
    engine.__dict__["_inverter"] = lambda: type("Inv", (), {
        "service_for": staticmethod(lambda w: (f"{w.kind}/set_value" if w.kind == "number" else "select/select_option",
                                               {"value": w.value} if w.kind == "number" else {"option": w.value})),
        "ram_off_command": staticmethod(lambda: ramcontrol.Command(OPTION_OFF))})
    engine.__dict__["_rc_entities"] = lambda: RC
    engine.__dict__["mode"] = type("M", (), {"effective": "active"})
    engine.__dict__["_publish_writes_today"] = lambda: None
    engine.__dict__["_today"] = lambda: datetime.now(timezone.utc).date()
    return pe, engine, inverter, logs


def settle(engine, timeout=WAIT):
    """Wait for the worker, then run what it handed back to the callback thread (as AppDaemon would)."""
    host = engine.host
    while True:
        assert engine._ram_worker().idle(timeout)
        if not host.later:
            return
        while host.later:
            callback, _, kw = host.later.pop(0)
            callback(kw)


def send(engine, want, why="change"):
    now = datetime.now(timezone.utc)
    engine._ram_send_later(want.writes(), RC, why, want, now)
    return now


def test_a_send_returns_at_once_and_the_calls_are_made_in_order_on_another_thread():
    _, engine, inverter, logs = make(delay=0.1)
    want = ramcontrol.Command(OPTION_DISCHARGE, 5000)
    started = time.monotonic()
    send(engine, want)
    assert time.monotonic() - started < 0.1                          # three 0.1 s calls would be 0.3 s
    assert engine._ram_sending()
    settle(engine)
    assert [(c[1], c[2]) for c in inverter.calls[:3]] == [("number.discharge", 5000),
                                                         ("select.mode", OPTION_DISCHARGE), ("number.discharge", 5000)]
    assert inverter.threads and threading.get_ident() not in inverter.threads
    assert not engine._ram_sending()


def test_what_follows_a_send_runs_back_on_the_callback_thread_once_it_landed():
    _, engine, _, logs = make()
    want = ramcontrol.Command(OPTION_CHARGE, 4000)
    now = send(engine, want)
    assert engine._ram().sent is None                               # not yet: the books wait for the inverter
    settle(engine)
    ram = engine._ram()
    assert ram.sent == want and ram.sent_at == now and ram.changes == {str(engine._today()): 1}
    assert [r[0] for r in engine.journal.rows] == ["number.charge", "select.mode", "number.charge"]
    assert engine.journal.rows[0][2] == "before" and engine.journal.saved == 1
    assert ("INFO", "RAM remote control: Force charge at 4000 W") in logs
    assert [c[1] for c in engine.host.inverter.calls] == ["number.charge", "select.mode", "number.charge",
                                                          "number.charge"]       # and the relatch, once, afterwards


def test_a_failed_call_is_reported_counted_and_not_booked_as_sent():
    _, engine, inverter, logs = make()
    inverter.fail.add("select.mode")
    send(engine, ramcontrol.Command(OPTION_DISCHARGE, 5000))
    settle(engine)
    assert engine._ram().sent is None and engine._ram().errors == 1
    assert any(level == "WARNING" and "could not set select.mode" in msg for level, msg in logs)
    assert len(inverter.calls) == 2                                  # the others were still tried, in order


def test_while_a_command_is_on_its_way_the_tick_asks_for_nothing_more_and_a_stuck_one_is_given_up():
    pe, engine, _, logs = make(delay=0.3)
    send(engine, ramcontrol.Command(OPTION_CHARGE, 5000))
    assert engine._ram_sending() and engine._ram_sending()
    engine.__dict__["_ram_send_since"] = datetime.now(timezone.utc).replace(year=2000)
    assert not engine._ram_sending()
    assert any(level == "WARNING" and "on its way for over" in msg for level, msg in logs)
    settle(engine)


def test_off_goes_in_line_behind_a_command_still_being_sent_and_the_old_command_is_not_booked():
    _, engine, inverter, logs = make(delay=0.05)
    send(engine, ramcontrol.Command(OPTION_CHARGE, 5000))
    engine._ram_off("leaving Active (Passive)")
    assert not engine._ram_sending()
    settle(engine)
    assert [c[2] for c in inverter.calls][-1] == OPTION_OFF          # the last word is Off: nothing overtook it
    assert [c[1] for c in inverter.calls] == ["number.charge", "select.mode", "number.charge", "select.mode"]
    assert engine._ram().sent is None                                # the charge landed after, but is not booked
    assert ("INFO", "RAM remote control: Off (Self-Use)") in logs


def test_the_relatch_joins_the_same_line():
    _, engine, inverter, _ = make(delay=0.05)
    want = ramcontrol.Command(OPTION_DISCHARGE, 5000)
    engine._ram().sent = want
    send(engine, ramcontrol.Command(OPTION_DISCHARGE, 4000))
    engine._ram_relatch({"role": "rc_discharge_power", "watts": 5000})
    settle(engine)
    assert [c[2] for c in inverter.calls][3] == 5000                 # after the three writes, not between them
    assert len(inverter.calls) == 5                                  # (and the new command's own relatch)


def test_under_the_fake_home_and_in_the_demo_nothing_goes_off_thread():
    pe, engine, _, _ = make()
    assert engine._writes_off_thread()
    engine.__dict__["_demo"] = "car"
    assert not engine._writes_off_thread()
    from pe_core.compare.harness import fake_home
    from pe_core.host_local import LocalHost
    assert LocalHost(fake_home()()).background_writes is False


def test_the_worker_runs_work_in_order_reports_and_survives_a_failure():
    done, order = [], []
    worker = Worker()
    worker.submit(lambda: order.append(1) or 1, done.append)
    worker.submit(lambda: 1 / 0, done.append)                        # logged, not fatal
    worker.submit(lambda: order.append(3) or 3, done.append)
    assert worker.idle(WAIT)
    assert order == [1, 3] and done == [1, None, 3]
    assert Worker().idle(0.1)                                        # nothing ever sent: idle at once



def test_the_tick_hands_a_command_over_and_returns_then_asks_for_nothing_until_it_landed():
    """The real _control_ram, against an inverter that takes 0.1 s a call."""
    import types
    pe, engine, inverter, _ = make(delay=0.1)
    now = datetime.now(timezone.utc)
    engine.__dict__.update({
        "cfg": types.SimpleNamespace(safety={}), "_test_running": lambda: False, "_halted": False, "_ram_was_on": True,
        "_control_params": lambda r: types.SimpleNamespace(max_charge_kw=5, max_discharge_kw=5),
        "_bms_limits": lambda r, w: None, "_note_bms_cap": lambda c: None, "_reserve_soc": lambda: 10,
        "_bms_sample": lambda *a, **k: None, "_simulate_windows": lambda *a: None,
        "_publish_method": lambda *a, **k: None, "_publish_if_changed": lambda *a, **k: None,
        "_release": lambda: None})
    inv = engine._inverter
    engine.__dict__["_inverter"] = lambda: type("Inv", (), {
        "service_for": inv().service_for, "ram_off_command": inv().ram_off_command,
        "ram_command": staticmethod(lambda action, power, mc, md, cap: ramcontrol.Command(OPTION_DISCHARGE, 5000))})
    r = types.SimpleNamespace(now=now, battery_power=0, battery_soc=80, solar_power=0)
    decision = types.SimpleNamespace(action="export", power_w=5000)

    started = time.monotonic()
    engine._control_ram(r, decision)
    assert time.monotonic() - started < 0.15                         # three 0.1 s calls are not waited for
    assert engine._ram().sent is None and engine._ram_sending()
    engine._control_ram(r, decision)                                 # the next tick, while it is on its way
    settle(engine)
    assert [c[1] for c in inverter.calls[:3]] == ["number.discharge", "select.mode", "number.discharge"]
    assert len(inverter.calls) == 4                                  # nothing doubled; the fourth is the relatch
    assert engine._ram().sent == ramcontrol.Command(OPTION_DISCHARGE, 5000)
    engine._control_ram(r, decision)                                 # landed, same command, refresh not due
    assert engine._ram().refreshes == {} and not engine._ram_sending() and len(inverter.calls) == 4
