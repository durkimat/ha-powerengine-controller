"""Engine v2 wired into the app (work package D): the engine choice, the v2 tick, the sensors, the state file, Passive
and timed windows. Driven like tests/test_demo_app.py: the whole app in the demo world with a fake AppDaemon and a
fake clock. The v1 replay (tests/test_replay.py) is what proves engine v1 is unchanged."""
import json
import os
import shutil
import sys
from datetime import timedelta

import pytest
from replay_harness import Clock
from test_demo_app import START, load_demo_app

V2_SENSORS = ("state_engine", "v2_mode", "v2_value", "v2_timeline", "v2_value_curve", "v2_triggers", "diag_v2",
              "diag_v2_settings")
LIMIT = 15_000


def simulate(tmp_path, monkeypatch, hours, engine="v1", control_method=None, mode=None, tick=True, folder=None,
             preview=None):
    """The demo app on the car day from 00:10 for `hours` simulated hours: one cycle and (on v2) one engine tick a
    minute. `engine` and `control_method` go into the demo config's system block, `mode` replaces its operation mode."""
    folder = folder or (tmp_path / "powerengine")
    folder.mkdir(exist_ok=True)
    (folder / "config.yaml").write_text("# the owner's config\n")
    real_copy = shutil.copyfile

    def copy(src, dst, **kw):
        if str(src).endswith("config.template"):
            text = open(src, encoding="utf-8").read()
            text = text.replace("  publisher: direct\n", "  publisher: direct\n"
                                + (f"  engine: {engine}\n" if engine != "v1" else ""))
            if preview is not None:
                text += f"\nengine_v2:\n  preview_when_v1: {'true' if preview else 'false'}\n"
            if control_method:
                text = text.replace("control_method: ram_remote", f"control_method: {control_method}")
            if mode:
                text = text.replace("  mode: active", f"  mode: {mode}")
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(text)
            return dst
        return real_copy(src, dst, **kw)

    monkeypatch.setattr(shutil, "copyfile", copy)
    powerengine = load_demo_app(monkeypatch)
    app = powerengine.PowerEngine()
    app.args = {"demo": "car", "settings_file": str(folder / "config.yaml")}
    Clock.now, app.tz = START, None
    app.initialize()
    world = app._demo_world()
    out = {"app": app, "world": world, "folder": folder, "decisions": [], "options": []}
    t = START
    for _ in range(int(hours * 60)):
        t += timedelta(minutes=1)
        Clock.now = t
        due = [x for x in app.timers if x[0] <= t]
        app.timers = [x for x in app.timers if x[0] > t]
        for _, cb, kw, _h in sorted(due, key=lambda x: x[0]):
            cb(kw)
        app._evaluate()
        app._cycle({})
        if tick:
            app._engine_tick({})
        out["options"].append((t, world.option, world.flows["charge_w"], world.flows["discharge_w"]))
        d = getattr(app, "_decision", None)
        if d is not None and (not out["decisions"] or out["decisions"][-1][1:] != (d.action, d.rule)):
            out["decisions"].append((t, d.action, d.rule))
    return out


def attr_bytes(app, key):
    attrs = app._published[key][1]
    return len(json.dumps(attrs, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


@pytest.fixture
def mp():
    m = pytest.MonkeyPatch()
    yield m
    m.undo()
    sys.modules.pop("powerengine", None)


# --- engine v1 (the default) ---------------------------------------------------------------------------------------

def test_engine_v1_is_the_default_and_previews_v2_without_sending(tmp_path, mp):
    run = simulate(tmp_path, mp, 1)
    app = run["app"]
    assert app._engine_name() == "v1" and app.cfg.system["engine"] == "v1" and app.cfg.engine_v2.preview_when_v1
    assert app._published["state_engine"] == ("v1", {"v2_available": True, "v2_preview": True})
    assert app._published["diag_v2_settings"][0].isdigit()           # the settings catalogue is always there
    for key in ("v2_mode", "v2_value", "v2_timeline", "v2_value_curve", "v2_triggers", "diag_v2"):
        assert key in app._published and app._published[key][0] != "unknown"
    attrs = app._published["v2_mode"][1]
    assert attrs["preview"] is True and attrs["sending"] is False
    assert attrs["not_sending_reason"] == "Preview: engine v1 is in control"
    for key in ("v2_mode", "v2_timeline", "v2_triggers", "diag_v2"):
        assert attr_bytes(app, key) < LIMIT
    assert app._v2 is not None and app._v2.vr is not None
    # v1 decided; v2 sent nothing and wrote nothing to the activity log
    assert run["decisions"] and not any(rule.startswith("v2_") for _, _, rule in run["decisions"])
    assert not any(e["rule"].startswith("v2_") for e in app.activity.entries)
    assert not app._decision.rule.startswith("v2_")
    assert app.real_calls == [] and app.real_events == []


def test_the_preview_never_sends_a_command_of_its_own(tmp_path, mp):
    """Passive on v1 with the preview on: v2 would charge on the cheap night, but nothing reaches the inverter."""
    run = simulate(tmp_path, mp, 4, mode="passive")
    app = run["app"]
    assert app._published["v2_mode"][1]["preview"] is True
    assert not app._ram().changes and "Force charge" not in {o[1] for o in run["options"]}
    assert not any(rule.startswith("v2_") for _, _, rule in run["decisions"])


def test_preview_off_publishes_nothing_from_v2(tmp_path, mp):
    run = simulate(tmp_path, mp, 1, preview=False)
    app = run["app"]
    assert app._published["state_engine"] == ("v1", {"v2_available": True})
    for key in ("v2_mode", "v2_value", "v2_timeline", "v2_value_curve", "v2_triggers", "diag_v2"):
        assert key not in app._published
    assert app._v2 is None and not (run["folder"] / "demo" / "engine_v2_state.json").exists()


def test_timed_windows_still_preview(tmp_path, mp):
    run = simulate(tmp_path, mp, 0.5, control_method="timed_windows")
    app = run["app"]
    assert app._published["v2_mode"][1]["preview"] is True
    assert not any(rule.startswith("v2_") for _, _, rule in run["decisions"])


# --- engine v2, Active, RAM remote control ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def v2_run(tmp_path_factory):
    m = pytest.MonkeyPatch()
    try:
        yield simulate(tmp_path_factory.mktemp("ha"), m, 4, engine="v2")
    finally:
        m.undo()
        sys.modules.pop("powerengine", None)


def test_decisions_come_from_v2_and_the_ram_commands_reach_the_inverter(v2_run):
    app, world = v2_run["app"], v2_run["world"]
    assert app._engine_name() == "v2" and app.mode.effective == "active"
    assert v2_run["decisions"] and all(rule.startswith("v2_") for _, _, rule in v2_run["decisions"])
    assert "grid_charge" in {a for _, a, _ in v2_run["decisions"]}           # the cheap night is used
    assert app._ram().changes and sum(app._ram().changes.values()) >= 1
    assert "Force charge" in {o[1] for o in v2_run["options"]} and max(o[2] for o in v2_run["options"]) > 3000
    assert world.events == []                                # the refresh kept the failsafe from firing
    assert app.real_calls == [] and app.real_events == []    # the demo gate still holds
    assert app.activity.entries and "v2_" in app.activity.entries[0]["rule"]


def test_v1_still_makes_its_plan_but_does_not_decide(v2_run):
    app = v2_run["app"]
    assert app.plan is not None and len(app.plan.slots) > 40 and "plan" in app._published
    assert not any(rule == "plan" for _, _, rule in v2_run["decisions"])
    assert app._early.records == []                           # the early-target look is v1's


def test_the_v2_sensors_are_published_and_each_is_under_15000_bytes(v2_run):
    app = v2_run["app"]
    assert app._published["state_engine"] == ("v2", {"v2_available": True})
    for key in V2_SENSORS:
        assert key in app._published, key
        assert attr_bytes(app, key) < LIMIT, key
    mode, attrs = app._published["v2_mode"]
    assert mode in ("self_use", "hold", "charge", "export", "event", "free", "none")
    assert attrs["sending"] is True and attrs["rule"].startswith("v2_") and attrs["values_at"]
    assert app._published["v2_timeline"][1]["items"] and app._published["v2_value"][1]["value_p"] is not None
    sizes = app._attr_sizes
    assert all(sizes[k]["peak"] < LIMIT for k in V2_SENSORS if k in sizes)
    assert not [m for m in app.warnings if "attributes are" in str(m)]


def test_the_state_file_is_written_and_reloaded(v2_run):
    app = v2_run["app"]
    path = app._v2_state_path()
    assert os.path.dirname(path).endswith("demo") and os.path.isfile(path)     # beside the demo config
    saved = json.load(open(path, encoding="utf-8"))
    assert saved["version"] == 1 and saved["journal"]
    app.terminate()                                         # saved again on terminate: the journal is current
    saved = json.load(open(path, encoding="utf-8"))
    assert [r["at"] for r in saved["journal"]] == [r["at"] for r in app._v2.journal()]
    first = app._v2
    app._v2 = None                                          # a restart: the next tick builds the engine from the file
    again = app._engine_v2()
    assert again is not first and again.journal() == first.journal()
    assert again.executor.state() == first.executor.state()


def test_a_broken_state_file_is_ignored_with_a_warning(tmp_path, mp):
    folder = tmp_path / "powerengine"
    folder.mkdir()
    run = simulate(tmp_path, mp, 0.05, engine="v2", folder=folder, tick=False)
    app = run["app"]
    with open(app._v2_state_path(), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    app._v2 = None
    app._engine_tick({})
    assert app._v2 is not None and app._decision is not None
    assert any("saved state could not be read" in m["msg"] for m in app._log_ring.lines)


# --- Passive and timed windows -------------------------------------------------------------------------------------

def test_passive_decides_and_publishes_but_sends_nothing(tmp_path, mp):
    run = simulate(tmp_path, mp, 1.5, engine="v2", mode="passive")
    app = run["app"]
    assert app.mode.effective == "passive" and run["decisions"]
    assert all(rule.startswith("v2_") for _, _, rule in run["decisions"])
    assert max(o[2] for o in run["options"]) == 0          # no forced charge (the battery covering the house is not)
    assert not app._ram().changes and "Force charge" not in {o[1] for o in run["options"]}
    attrs = app._published["v2_mode"][1]
    assert attrs["sending"] is False and attrs["not_sending_reason"]
    assert app.activity.entries[0]["text"].startswith("Would ")


def test_timed_windows_with_v2_refuse_to_send_and_say_why(tmp_path, mp):
    run = simulate(tmp_path, mp, 1, engine="v2", control_method="timed_windows")
    app = run["app"]
    assert app.mode.configured == "active" and app.mode.effective == "passive"
    assert "Engine v2 needs RAM remote control" in app.mode.reason and app.mode.reason.startswith("Active refused")
    assert app.get_state("sensor.pe_state_operation_mode", attribute="reason") == app.mode.reason
    assert run["decisions"] and all(rule.startswith("v2_") for _, _, rule in run["decisions"])    # still decided
    assert app._published["v2_mode"][1]["sending"] is False
    assert not app.writes.own_today(app._today())            # nothing written to the inverter
    assert max(o[2] for o in run["options"]) == 0          # no forced charge (the battery covering the house is not)
    assert not {o[1] for o in run["options"]} & {"Force charge", "Force discharge"}
    assert app._ram().changes == {}


# --- switching engines ---------------------------------------------------------------------------------------------

def test_switching_the_engine_in_a_config_save_keeps_the_mode_and_rebuilds_v2(tmp_path, mp):
    run = simulate(tmp_path, mp, 0.5, engine="v2")
    app = run["app"]
    first = app._v2
    assert first is not None and app.mode.effective == "active"
    raw = json.loads(json.dumps(app.cfg.raw))
    raw["system"]["engine"] = "v1"
    app._on_save("pe_config_save", {"config": raw}, {})
    assert app._engine_name() == "v1" and app.mode.effective == "active" and app._v2 is None
    assert app._published["state_engine"][0] == "v1"
    assert any("Engine changed from v2 to v1" in m["msg"] for m in app._log_ring.lines)
    raw["system"]["engine"] = "v2"
    app._on_save("pe_config_save", {"config": raw}, {})
    assert app._engine_name() == "v2" and app.mode.effective == "active"
    Clock.now += timedelta(minutes=1)
    app._engine_tick({})
    assert app._v2 is not None and app._v2 is not first and app._v2.vr is not None     # revalued at once
    assert app._published["state_engine"][0] == "v2"


def test_a_save_is_answered_before_the_reload(v2_run):
    """The card waits for pe_config_result; the reload (inputs, dashboard, a whole cycle) comes after the answer."""
    app = v2_run["app"]
    order, reload, fire = [], app._reload, app.fire_event
    app._reload = lambda: (order.append("reload"), reload())
    app.fire_event = lambda event, **kw: (order.append(event), fire(event, **kw))
    try:
        app._on_save("pe_config_save", {"config": json.loads(json.dumps(app.cfg.raw))}, {})
    finally:
        del app._reload, app.fire_event
    assert order[:2] == ["pe_config_result", "reload"]


def test_the_diagnostics_export_has_an_engine_v2_section(v2_run):
    app = v2_run["app"]
    from datetime import datetime, timezone
    bundle = app._diag_bundle(datetime(2026, 10, 6, 5, tzinfo=timezone.utc))
    e = bundle["engine_v2"]
    assert e["in_use"] == "v2" and e["health"]["engine"] == "v2" and e["journal"] and len(e["journal"]) <= 300
    assert e["timeline"]["items"]
    json.dumps(bundle, default=str)


def test_live_v2_says_it_is_not_a_preview(v2_run):
    assert v2_run["app"]._published["v2_mode"][1]["preview"] is False
    assert v2_run["app"]._published["state_engine"] == ("v2", {"v2_available": True})
