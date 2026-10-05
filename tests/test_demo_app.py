"""PowerEngine in demo mode, closed loop: the app decides, the simulated battery follows, and nothing reaches the
real Home Assistant."""
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
from replay_harness import Clock, FrozenDatetime, _fake_appdaemon

BST = timezone(timedelta(hours=1))
START = datetime(2026, 10, 6, 0, 10, tzinfo=BST)          # a cheap night, on the "car" day (68 % at midnight)
HOURS = 6


def load_demo_app(monkeypatch):
    """powerengine against a fake AppDaemon that records anything that reaches the 'real' Home Assistant."""
    Base = _fake_appdaemon()

    class Hass(Base):
        def __init__(self):
            super().__init__()
            self.real_calls, self.real_events, self.real_sets, self.real_mqtt = [], [], [], []

        def set_state(self, entity_id, state=None, attributes=None, **kw):
            self.real_sets.append(entity_id)
            self.set_fake(entity_id, state, attributes)

        def call_service(self, service, **kw):
            self.real_calls.append(service)

        def fire_event(self, event, **kw):
            self.real_events.append(event)

        def get_plugin_api(self, name):
            self.real_mqtt.append(name)
            raise AssertionError("demo mode must not look for the MQTT plugin")

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


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """Six simulated hours in demo mode; returns what the tests look at."""
    mp = pytest.MonkeyPatch()
    folder = tmp_path_factory.mktemp("ha") / "powerengine"
    folder.mkdir()
    (folder / "demo").mkdir()
    (folder / "demo" / "stale.txt").write_text("left from an earlier demo")
    (folder / "costs").mkdir()
    (folder / "costs" / "2026-09-22.json").write_text("[]")          # the owner's own data, which must survive
    (folder / "config.yaml").write_text("# the owner's config\n")
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file() and "demo" not in p.parts}
    try:
        powerengine = load_demo_app(mp)
        app = powerengine.PowerEngine()
        app.args = {"demo": "car", "settings_file": str(folder / "config.yaml")}
        Clock.now, app.tz = START, None
        app.initialize()
        world = app._demo_world()
        out = {"app": app, "world": world, "folder": folder, "before": before, "soc": [(START, world.soc)],
               "options": [], "decisions": []}
        t = START
        for n in range(HOURS * 60):
            t += timedelta(minutes=1)
            Clock.now = t
            due = [x for x in app.timers if x[0] <= t]
            app.timers = [x for x in app.timers if x[0] > t]
            for _, cb, kw, _h in sorted(due, key=lambda x: x[0]):
                cb(kw)
            if n % 5 == 0:
                app._evaluate()
            app._cycle({})
            out["options"].append((t, world.option, world.flows["charge_w"], world.flows["discharge_w"]))
            d = getattr(app, "_decision", None)
            if d is not None and (not out["decisions"] or out["decisions"][-1][1:] != (d.action, d.rule)):
                out["decisions"].append((t, d.action, d.rule))
            if n % 30 == 29:
                out["soc"].append((t, world.soc))
        yield out
    finally:
        mp.undo()
        sys.modules.pop("powerengine", None)


def test_demo_mode_is_on_direct_and_never_looks_for_mqtt(run):
    app = run["app"]
    assert app._demo == "car" and app._get_publisher().name == "direct" and app.real_mqtt == []
    assert app.mode.effective == "active" and app._demo_gate is not None


def test_zero_service_calls_reach_home_assistant_and_only_pe_states_are_set(run):
    app = run["app"]
    assert app.real_calls == [] and app.real_events == []
    assert len(app.real_sets) > 500 and all(".pe_" in e for e in app.real_sets)
    assert "sensor.pe_diag_version" in app.real_sets and "switch.pe_ctl_pause" in app.real_sets
    assert app._demo_gate.dropped                                 # the logbook entries it tried to make, refused
    assert {d[1] for d in app._demo_gate.dropped} >= {"logbook/log"}


def test_nothing_is_written_outside_the_demo_folder_and_the_owners_files_are_untouched(run):
    folder, before = run["folder"], run["before"]
    after = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file() and "demo" not in p.parts}
    assert after == before and before                            # config.yaml and costs/ exactly as they were
    assert not (folder / "demo" / "stale.txt").exists()          # a fresh demo each start
    assert (folder / "demo" / "config.yaml").exists() and (folder / "demo" / "costs").is_dir()


def test_plans_are_made_and_decisions_reach_the_world(run):
    app, world = run["app"], run["world"]
    assert app.plan is not None and len(app.plan.slots) > 40
    actions = [d[1] for d in run["decisions"]]
    assert len(actions) >= 5 and "grid_charge" in actions
    assert app._ram().changes and sum(app._ram().changes.values()) >= 4
    options = {o[1] for o in run["options"]}
    assert "Force charge" in options and world.refused and all(r[0] == "logbook/log" for r in world.refused)
    assert world.events == []                                     # the app kept re-sending: the failsafe never fired


def test_a_force_charge_in_a_cheap_slot_raises_the_soc(run):
    soc = run["soc"]
    first_charge = next(t for t, opt, charge, _ in run["options"] if opt == "Force charge" and charge > 4000)
    assert first_charge < START + timedelta(minutes=5)
    # The plan may sell again after the first charge (cheap power in, 15p out: the optimiser's cycle), so the SoC
    # after 45 minutes isn't the test: the world must have been force-charged at about 5 kW for several minutes.
    minutes = sum(1 for t, opt, charge, _ in run["options"]
                  if t <= START + timedelta(minutes=45) and opt == "Force charge" and charge > 4000)
    assert minutes >= 5
    assert min(s for _, s in soc) >= 12 and max(s for _, s in soc) <= 100.0


def test_the_battery_only_ever_did_what_it_was_told(run):
    world = run["world"]
    for _t, opt, charge, discharge in run["options"]:
        assert charge <= 5000 and discharge <= 5000
        if opt == "Force charge":
            assert discharge == 0
        if opt == "Force discharge":
            assert charge == 0
    assert 12 <= world.soc <= 100


def test_the_version_sensor_says_it_is_a_demo_with_the_days_title(run):
    app = run["app"]
    attrs = app.states["sensor.pe_diag_version"]["attributes"]
    assert attrs["demo"]["day"] == "car" and attrs["demo"]["title"] == "Car charging day"
    assert attrs["names"]["supplier"] == "EDF"


def test_history_backfilled_so_learning_and_health_have_days_to_work_with(run):
    app = run["app"]
    days = sorted(p.stem for p in (run["folder"] / "demo" / "costs").glob("2026-*.json"))
    assert len(days) >= 14 and days[-1] == "2026-10-06"
    assert app.profile is not None and app.profile.days >= 10
