"""Starting, switching and leaving the demo from the dashboard (pe_demo / pe_demo_result), and doing it cleanly."""
import asyncio
import itertools
import json
import sys
import types
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from replay_harness import Clock, FrozenDatetime, _fake_appdaemon

_HANDLES = itertools.count(1000)
BST = timezone(timedelta(hours=1))
NOW = datetime(2026, 10, 6, 0, 10, tzinfo=BST)
REFUSED = "The demo can only start on a PowerEngine that isn't set up yet, so it never takes over a real system."
NOTE = "Recorded data from a real home. Nothing is controlled."
DAYS = [("sunny", "Sunny day"), ("dull", "Dull day"), ("axle", "<<event>> event day"), ("car", "Car charging day")]


def load_app(monkeypatch, wrap=None):
    """`wrap`: what a scheduling or listening call hands back. None: the handle itself (AppDaemon 4.4); a function
    such as `task_handle` makes it an asyncio Task/Future that resolves to the handle (AppDaemon 4.5 called from
    its event loop)."""
    Base = _fake_appdaemon()

    class Hass(Base):
        def __init__(self):
            super().__init__()
            self.invalid_cancels = 0
            self.live = {}
            self.real_calls, self.real_events, self.real_sets = [], [], []

        def _add(self, kind, cb, key):
            h = next(_HANDLES)
            self.live[h] = (kind, cb.__name__, key)
            return wrap(h) if wrap else h

        def run_in(self, cb, delay, **kw):
            h = super().run_in(cb, delay, **kw)
            self.live[h] = ("timer", cb.__name__, "in")
            return wrap(h) if wrap else h

        def run_every(self, cb, *a, **k):
            return self._add("timer", cb, "every")

        def run_daily(self, cb, *a, **k):
            # AppDaemon 4.5 builds run_daily on self.run_every, so through the app's own tracking wrapper too
            return self.run_every(cb, "now", 86400)

        def timer_running(self, handle, **kw):
            return handle in self.live

        def listen_event(self, cb, event=None, **k):
            return self._add("event", cb, event)

        def listen_state(self, cb, entity=None, **k):
            return self._add("state", cb, entity)

        def cancel_timer(self, handle, **kw):
            if isinstance(handle, asyncio.Future):          # AppDaemon: "Invalid callback handle", nothing cancelled
                self.invalid_cancels += 1
                return
            self.timers[:] = [x for x in self.timers if x[3] != handle]  # in place: no new attribute
            if self.live.pop(handle, None) is None:          # AppDaemon: "Invalid callback handle"
                self.invalid_cancels += 1

        def cancel_listen_event(self, handle, **kw):
            if isinstance(handle, asyncio.Future):
                self.invalid_cancels += 1
                return
            self.live.pop(handle, None)

        cancel_listen_state = cancel_listen_event

        def get_plugin_api(self, name):
            raise RuntimeError("no MQTT plugin in this AppDaemon")          # so entities are published directly

        def set_state(self, entity_id, state=None, attributes=None, **kw):
            self.real_sets.append(entity_id)
            self.set_fake(entity_id, state, attributes)

        def call_service(self, service, **kw):
            self.real_calls.append(service)

        def fire_event(self, event, **kw):
            self.real_events.append((event, kw))

    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = Hass
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine
    for name, mod in list(sys.modules.items()):
        if (name == "powerengine" or name.startswith("pe_core")) and getattr(mod, "datetime", None) is datetime:
            monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    return powerengine


@pytest.fixture
def make(tmp_path, monkeypatch):
    folder = tmp_path / "powerengine"
    folder.mkdir()

    def build(real_config=False, wrap=None, **args):
        powerengine = load_app(monkeypatch, wrap)
        if real_config:
            (folder / "config.yaml").write_text("# the owner's config\n")
        app = powerengine.PowerEngine()
        app.args = {"settings_file": str(folder / "config.yaml"), **args}
        Clock.now, app.tz = NOW, None
        app.initialize()
        return app
    build.folder = folder
    yield build
    sys.modules.pop("powerengine", None)


_LOOP = asyncio.new_event_loop()


def task_handle(handle):
    """What AppDaemon 4.5 returns when the call is made on its event loop: a finished Task holding the handle."""
    fut = _LOOP.create_future()
    fut.set_result(handle)
    return fut


def send(app, **data):
    n = len(app.real_events)
    app._on_demo("pe_demo", data, {})
    return [kw for ev, kw in app.real_events[n:] if ev == "pe_demo_result"]


def version(app):
    return app.states["sensor.pe_diag_version"]["attributes"]


def shape(app):
    """What is scheduled or listened to right now."""
    return Counter(app.live.values())


def test_unconfigured_publishes_setup_and_no_demo(make):
    app = make()
    assert app._demo is None
    a = version(app)
    assert a["setup"] == "unconfigured" and a["demo"] is None and a["names"]


def test_a_real_config_is_configured_and_start_is_refused(make):
    app = make(real_config=True)
    assert version(app)["setup"] == "configured" and version(app)["demo"] is None
    assert send(app, action="start", day="sunny") == [{"ok": False, "message": REFUSED}]
    assert not (make.folder / "demo.json").exists() and app._demo is None and app.real_calls == []


def test_a_configured_app_runs_exactly_as_before_no_tracking(make):
    # A set-up PowerEngine never re-initialises in place, so none of the recording is installed: its timers and
    # listeners go straight to AppDaemon, and no list of handles grows while it runs.
    app = make(real_config=True)
    assert "_touched" not in app.__dict__ and "_handles" not in app.__dict__
    assert "run_in" not in app.__dict__ and "listen_state" not in app.__dict__


def test_an_unconfigured_app_forgets_timers_that_have_run(make):
    app = make()
    before = len(app.__dict__["_handles"])
    for _ in range(50):
        app.run_in(lambda kw: None, 1)
    Clock.now = NOW + timedelta(minutes=5)
    app.run_in(lambda kw: None, 1)
    assert len(app.__dict__["_handles"]) <= before + 1


def test_start_writes_demo_json_and_reinitialises_into_that_day(make):
    app = make()
    assert send(app, action="start", day="sunny") == [{"ok": True, "message": "Starting the demo: Sunny day."}]
    assert json.loads((make.folder / "demo.json").read_text()) == {"day": "sunny"}
    assert app._demo == "sunny" and app._demo_world().day == "sunny"
    a = version(app)
    assert a["setup"] == "unconfigured"
    assert a["demo"] == {"day": "sunny", "title": "Sunny day", "note": NOTE,
                         "days": [{"key": k, "title": t} for k, t in DAYS[:2]] + [
                             {"key": "axle", "title": a["demo"]["days"][2]["title"]},
                             {"key": "car", "title": "Car charging day"}]}
    assert "<<" not in a["demo"]["days"][2]["title"] and a["demo"]["days"][2]["title"].endswith("event day")


def test_the_contract_attributes_are_exact(make):
    app = make()
    send(app, action="start", day="car")
    d = version(app)["demo"]
    assert list(d) == ["day", "title", "days", "note"] and d["note"] == NOTE
    assert [x["key"] for x in d["days"]] == [k for k, _ in DAYS]
    assert all(list(x) == ["key", "title"] for x in d["days"])
    assert d["title"] == "Car charging day"


def test_a_saved_choice_is_picked_up_on_the_next_start(make):
    (make.folder / "demo.json").write_text(json.dumps({"day": "dull"}))
    app = make()
    assert app._demo == "dull" and version(app)["demo"]["day"] == "dull"


def test_a_saved_choice_is_ignored_when_a_real_config_exists(make):
    (make.folder / "demo.json").write_text(json.dumps({"day": "dull"}))
    app = make(real_config=True)
    assert app._demo is None and version(app)["setup"] == "configured"


def test_day_switch_reseeds_and_persists(make):
    app = make()
    send(app, action="start", day="sunny")
    soc_sunny = app._demo_world().soc
    assert send(app, action="day", day="car") == [{"ok": True, "message": "Switched to: Car charging day."}]
    assert app._demo == "car" and version(app)["demo"]["day"] == "car"
    assert json.loads((make.folder / "demo.json").read_text()) == {"day": "car"}
    assert app._demo_world().day == "car" and app._demo_world().soc != soc_sunny


def test_day_needs_a_running_demo(make):
    app = make()
    assert send(app, action="day", day="car")[0]["ok"] is False
    assert app._demo is None and not (make.folder / "demo.json").exists()


def test_exit_returns_to_unconfigured(make):
    app = make()
    send(app, action="start", day="axle")
    assert send(app, action="exit") == [{"ok": True, "message": "The demo has ended."}]
    assert not (make.folder / "demo.json").exists() and app._demo is None and app.__dict__.get("_demo_gate") is None
    a = version(app)
    assert a["setup"] == "unconfigured" and a["demo"] is None
    assert app.get_state("sensor.pe_diag_version") is not None            # the real get_state is back


def test_exit_when_no_demo_is_harmless(make):
    app = make()
    assert send(app, action="exit")[0]["ok"] is True and app._demo is None


def test_invalid_day_and_unknown_action_are_refused(make):
    app = make()
    for bad in ("nope", "", None, 7):
        r = send(app, action="start", day=bad)
        assert len(r) == 1 and r[0]["ok"] is False and r[0]["message"]
    assert app._demo is None and not (make.folder / "demo.json").exists()
    send(app, action="start", day="sunny")
    assert send(app, action="day", day="nope")[0]["ok"] is False
    assert send(app, action="explode")[0]["ok"] is False
    assert send(app)[0]["ok"] is False
    assert app._demo == "sunny" and json.loads((make.folder / "demo.json").read_text()) == {"day": "sunny"}


def test_only_the_pe_demo_event_is_handled(make):
    app = make()
    app._on_demo("pe_command", {"action": "start", "day": "sunny"}, {})
    assert app._demo is None and app.real_events == [] and not (make.folder / "demo.json").exists()


def test_the_demo_app_argument_still_wins(make):
    app = make(demo="car")
    assert app._demo == "car"
    for msg in ({"action": "day", "day": "sunny"}, {"action": "exit"}, {"action": "start", "day": "sunny"}):
        assert send(app, **msg)[0]["ok"] is False
    assert app._demo == "car" and not (make.folder / "demo.json").exists()


def test_the_gate_holds_after_a_runtime_start(make):
    app = make()
    n_sets = len(app.real_sets)
    send(app, action="start", day="car")
    for i in range(30):
        Clock.now = NOW + timedelta(minutes=i)
        app._evaluate()
        app._cycle({})
    assert app.real_calls == []                                   # zero service calls to the real Home Assistant
    assert all(ev == "pe_demo_result" for ev, _ in app.real_events)
    new = app.real_sets[n_sets:]
    assert new and all(".pe_" in e for e in new)
    assert app._demo_gate.dropped == [] or all(d[1] == "logbook/log" for d in app._demo_gate.dropped)


def test_no_duplicate_timers_or_listeners_after_start_day_exit_start(make):
    app = make()
    idle = shape(app)
    assert idle[("event", "_on_demo", "pe_demo")] == 1 and sum(idle.values()) >= 8      # not vacuous
    send(app, action="start", day="sunny")
    first = shape(app)
    assert first[("event", "_on_demo", "pe_demo")] == 1
    send(app, action="day", day="dull")
    send(app, action="exit")
    assert shape(app) == idle                                     # back to exactly what unconfigured has
    send(app, action="start", day="sunny")
    assert shape(app) == first                                    # and exactly what the first start had
    for _ in range(3):
        send(app, action="day", day="car")
        send(app, action="day", day="sunny")
    assert shape(app) == first
    assert len(app.timers) == len({t[3] for t in app.timers})     # no timer queued twice


def test_a_reinitialised_app_does_one_thing_per_cycle(make):
    """After several restarts one cycle still publishes and acts exactly once (no doubled callbacks)."""
    app = make()
    send(app, action="start", day="car")
    once = make(demo="car")
    for a in (app, once):
        Clock.now = NOW
        a.real_sets.clear()
        a._cycle({})
    send(app, action="day", day="dull")
    send(app, action="day", day="car")
    app.real_sets.clear()
    Clock.now = NOW
    app._cycle({})
    assert Counter(app.real_sets) == Counter(once.real_sets)


def test_the_demo_dashboard_is_written_where_home_assistant_loads_it(make):
    app = make()
    send(app, action="start", day="car")
    real, inner = make.folder / "dashboard.yaml", make.folder / "demo" / "dashboard.yaml"
    assert real.exists() and inner.exists() and real.read_text() == inner.read_text()
    doc = yaml.safe_load(real.read_text())
    assert doc["views"] and all(v["sections"][0]["cards"][0]["type"] == "custom:powerengine-demo-card"
                                for v in doc["views"] if "sections" in v)


def test_every_view_starts_with_the_demo_card_in_the_shipped_dashboard():
    from pathlib import Path
    text = (Path(__file__).parent.parent / "apps/powerengine/dashboard/dashboard.lovelace").read_text()
    assert text.count("- type: custom:powerengine-demo-card") == text.count("\n  - title:") == 8
