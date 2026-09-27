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


def test_absent_guard_counts_as_safe_but_is_reported():
    from pe_core.modes import guard_status
    for gone in ("unavailable", "unknown", None):
        states = dict(SAFE, **{"switch.predbat_set_read_only": gone})
        assert guard_problems(parse_config(GUARDED), states.get) == []
        assert guard_status(parse_config(GUARDED), states.get)[1] == ["switch.predbat_set_read_only"]
    states = dict(SAFE, **{"switch.predbat_set_read_only": "off"})           # present and off: not safe
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


def test_this_build_goes_active_with_safe_guards():
    assert effective_mode(parse_config(GUARDED)).effective == "active"


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
    own = {"n": 0}
    a.own = own

    def add_own(day, n=1):
        own["n"] += n
    base = {}
    a.writes = types.SimpleNamespace(observed=lambda day, eid: None, own=add_own, own_today=lambda day: own["n"],
                                     base=lambda day: base.get(day, 0),
                                     set_base=lambda day, n: base.__setitem__(day, n))
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
    run_timers(app)                                   # apply button, start check, end (revert), end check
    assert any(s == "button/press" for s, _ in app.calls)
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
    app._leave_active(active, tripped, ["x"])
    assert not app.calls


def test_inputs_failing_while_active_release_after_the_grace_period(app):
    import powerengine
    active = effective_mode(parse_config(GUARDED), build_supports_active=True)
    broken = effective_mode(parse_config(GUARDED), build_supports_active=True, missing_required=["battery_soc"])
    assert broken.effective == "unconfigured"
    app.cfg_error = None
    app.states["number.timed_charge_end_hour"] = 5
    app._leave_active(active, broken, [])
    assert app.states["number.timed_charge_end_hour"] == 5            # windows left running for now
    app.mode = broken
    app._release_if_still_missing()
    assert app.states["number.timed_charge_end_hour"] == 5
    app._release_due = datetime.now(timezone.utc) - powerengine.timedelta(seconds=1)
    app._release_if_still_missing()
    assert app.states["number.timed_charge_end_hour"] == 0 and app._release_due is None


def test_inputs_back_within_the_grace_period_writes_nothing(app):
    active = effective_mode(parse_config(GUARDED), build_supports_active=True)
    broken = effective_mode(parse_config(GUARDED), build_supports_active=True, missing_required=["battery_soc"])
    app.cfg_error = None
    app._leave_active(active, broken, [])
    app.mode = active
    app._release_if_still_missing()
    assert app._release_due is None and not app.calls


def test_write_limit_pauses(app):
    app._publish = lambda topic, payload: app.published.append((topic, payload))
    app.own["n"] = 148
    assert app._within_write_limit(2)
    assert not app._within_write_limit(3)
    assert ("powerengine/ctl_pause/set", "ON") in app.published


def test_write_limit_counts_from_resume(app):
    app._publish = lambda topic, payload: app.published.append((topic, payload))
    app.own["n"] = 200
    app.writes.set_base(app._today(), 150)  # resumed after 150 writes
    assert app._within_write_limit(100) and not app._within_write_limit(101)


def test_own_writes_counted(app):
    app._write(writes_needed(release(), {}), {r: f"number.{r}" for r in release()} | {
        "storage_mode": "select.storage_mode", "timed_update_button": "button.timed_update_button"})
    assert app.own["n"] == 1                # the mode; the 8 window times are staged in HA, not inverter writes
    run_timers(app)
    assert app.own["n"] == 2                # + the update button, which sends them (one block write)


def _clock_app(app, mode_active, drift_s, synced_days_ago):
    from datetime import timedelta
    app.cfg = parse_config({**GUARDED, "inputs": {**app.cfg.raw["inputs"],
                                                  "inverter_clock": {"entity": "sensor.solis_rtc"},
                                                  "inverter_clock_sync": {"entity": "button.solis_sync_rtc"}}})
    app.mode = effective_mode(app.cfg, build_supports_active=mode_active)
    read = datetime.now(timezone.utc).replace(microsecond=0)
    rtc = (read + timedelta(seconds=drift_s)).strftime("%Y-%m-%d %H:%M:%S")
    pressed = (read - timedelta(days=synced_days_ago)).isoformat()
    app.get_state = lambda eid, attribute=None: (
        {"state": rtc, "last_updated": read.isoformat()} if eid == "sensor.solis_rtc" and attribute == "all"
        else pressed if eid == "button.solis_sync_rtc" else app.states.get(eid))
    app._publish_if_changed = lambda key, state, attrs: app.published.append((key, state, attrs))
    app._health = lambda: None


def test_clock_synced_in_active_when_drifting(app):
    _clock_app(app, True, 90, 2)
    app._clock_step({})
    assert ("button/press", {"entity_id": "button.solis_sync_rtc"}) in app.calls
    assert app._clock_drift == 90


def test_clock_not_synced_when_recent_and_close(app):
    _clock_app(app, True, 5, 2)
    app._clock_step({})
    assert not app.calls


def test_clock_never_synced_in_passive(app):
    _clock_app(app, False, 3600, 30)
    app._clock_step({})
    assert not app.calls and app.published[-1][2]["sync_due"] == "drift"


# --- the Predbat/PowerEngine switch (0.7.4) ----------------------------------------------

def test_with_operation_sets_mode_and_leaves_the_rest():
    from pe_core.config import ConfigError
    from pe_core.store import with_operation
    raw = {"operation": {"mode": "passive", "x": 1}, "inputs": {"a": {"entity": "sensor.a"}}}
    new = with_operation(raw, "active")
    assert new["operation"] == {"mode": "active", "x": 1} and new["inputs"] == raw["inputs"]
    assert raw["operation"]["mode"] == "passive"
    assert with_operation(None, "passive") == {"operation": {"mode": "passive"}}
    with pytest.raises(ConfigError):
        with_operation(raw, "on")


def test_switch_event_saves_active_and_reloads(app, tmp_path):
    saved = []
    app._save_path = lambda: str(tmp_path / "config.yaml")
    app._reload = lambda: saved.append("reload")
    app._on_set_control("pe_set_control", {"operation": "active"}, {})
    text = (tmp_path / "config.yaml").read_text()
    assert "mode: active" in text and saved == ["reload"]


def test_switch_event_rejects_nonsense(app, tmp_path):
    notes = []
    app._save_path = lambda: str(tmp_path / "config.yaml")
    app._reload = lambda: notes.append("reload")
    app._notify = lambda ev, msg: notes.append(msg[1])
    app._on_set_control("pe_set_control", {"operation": "boost"}, {})
    assert notes == ["PowerEngine: switch failed"] and not (tmp_path / "config.yaml").exists()


def test_mode_sensor_accepts_every_effective_mode():
    from pe_core.modes import ACTIVE, PASSIVE, PAUSED, UNCONFIGURED
    opts = next(e for e in ENTITIES if e.key == "state_operation_mode").options["options"]
    assert set(opts) >= {UNCONFIGURED, PASSIVE, ACTIVE, PAUSED}


def test_unconfigured_rechecks_every_cycle(app):
    calls = []
    app.cfg_error = None
    app._evaluate = lambda: calls.append(1)
    app.mode = effective_mode(app.cfg, missing_required=["battery_soc"])
    assert app.mode.effective == "unconfigured"
    app.read = None
    import powerengine
    orig = powerengine.read
    powerengine.read = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop"))
    try:
        app._cycle({})
    except Exception:
        pass
    finally:
        powerengine.read = orig
    assert calls == [1]


def test_guard_missing_from_appdaemon_is_checked_with_ha(app):
    app.states.pop("switch.predbat_set_read_only")
    app.render_template = lambda t: "on"
    assert app._guard_state("switch.predbat_set_read_only") == "on" and app._guard_live
    app.render_template = lambda t: "off"
    assert guard_problems(app.cfg, app._guard_state)                            # really off: not safe
    app.render_template = lambda t: "unknown"
    from pe_core.modes import guard_status
    assert guard_status(app.cfg, app._guard_state) == ([], ["switch.predbat_set_read_only"])


def test_guard_that_cannot_be_checked_is_never_safe(app):
    app.states.pop("switch.predbat_set_read_only")

    def boom(t):
        raise RuntimeError("no REST")
    app.render_template = boom
    probs = guard_problems(app.cfg, app._guard_state)
    assert probs and "unverified" in probs[0]


def test_writes_today_summary():
    from pe_core.journal import day_summary, is_staged
    assert is_staged("number.solis_timed_charge_start_hours_2") and is_staged("timed_discharge_end_minute")
    assert not is_staged("number.solis_timed_charge_current")
    assert not is_staged("button.solis_update_charge_discharge_times")
    e = [{"t": "2026-09-26T23:00:00+00:00", "entity": "number.solis_timed_charge_current", "value": 50, "before": 0,
          "why": "old"},
         {"t": "2026-09-27T08:00:00+00:00", "entity": "number.solis_timed_charge_start_hours", "value": 9, "before": 0,
          "why": "three windows: grid_charge (plan)"},
         {"t": "2026-09-27T08:00:00+00:00", "entity": "number.solis_timed_charge_current", "value": 100, "before": 0,
          "why": "three windows: grid_charge (plan)"},
         {"t": "2026-09-27T08:00:03+00:00", "entity": "button.solis_update_charge_discharge_times", "value": None,
          "before": None, "why": "three windows: grid_charge (plan)"}]
    s = day_summary(e, "2026-09-26T23:00:01+00:00")
    assert (s["writes"], s["staged"], s["changes"]) == (2, 1, 1)
    assert s["by_reason"] == [{"why": "three windows: grid_charge (plan)", "writes": 2}]
    assert s["recent"][0] == {"time": "08:00:03", "setting": "update charge discharge times", "change": "pressed",
                              "why": "three windows: grid_charge (plan)"}
    assert s["recent"][1]["change"] == "0 → 100"


def test_diagnostics_export(app, tmp_path):
    import os
    events = []
    app.fire_event = lambda ev, **kw: events.append((ev, kw))
    app._save_path = lambda: str(tmp_path / "state.json")
    app._user_name = lambda data: "tester"
    app.journal = types.SimpleNamespace(entries=[
        {"t": "2020-01-01T00:00:00+00:00", "entity": "x", "value": 1, "before": 0, "why": "old"},
        {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "entity": "number.a", "value": 1,
         "before": 0, "why": "now"}])
    app._on_diag_request("pe_diag_request", {"id": "abc"}, {})
    ev, kw = events[-1]
    assert ev == "pe_diag_bundle" and kw["id"] == "abc"
    b = kw["bundle"]
    assert b["app"]["version"] and [e["why"] for e in b["journal"]] == ["now"]
    assert "error" in b["writes"] or isinstance(b["writes"], dict)
    assert kw["saved"] and os.path.exists(kw["saved"])
    for i in range(7):
        from pe_core.diagnostics import save_copy
        save_copy(str(tmp_path / "diagnostics"), f"2026010{i}", {"i": i})
    assert len(os.listdir(tmp_path / "diagnostics")) == 5


def test_log_ring_keeps_recent_lines():
    from pe_core.diagnostics import LogRing
    r = LogRing(3)
    for i in range(5):
        r.add(datetime(2026, 9, 27, tzinfo=timezone.utc), "INFO", f"line {i}")
    assert [x["msg"] for x in r.lines] == ["line 2", "line 3", "line 4"]


def test_limit_base_survives_a_restart(tmp_path):
    from datetime import date

    from pe_core.eeprom import WriteLog
    path = str(tmp_path / "w.json")
    w = WriteLog(path)
    w.own(date(2026, 9, 27), 90)
    w.set_base(date(2026, 9, 27), 90)
    w.save()
    again = WriteLog(path)
    assert again.base(date(2026, 9, 27)) == 90 and again.base(date(2026, 9, 28)) == 0


def test_mid_slot_replan_keeps_the_running_action_on_a_near_tie():
    from test_optimiser import T0, day

    from pe_core.optimiser import optimise
    from pe_core.planner import Params
    slots = day()
    free = optimise(slots, 50.0, Params(arbitrage=True), prev_action="hold")
    kept = optimise(slots, 50.0, Params(arbitrage=True), prev_action="hold", stick=10.0)   # huge: always keep
    assert kept["actions"][0] == "hold" and T0
    assert free["actions"][0] in ("self_use", "hold", "grid_charge", "export")


def test_smart_requests_wait_to_settle_after_start(app):
    from datetime import timedelta
    app.cfg = parse_config({**GUARDED, "inputs": {**app.cfg.raw["inputs"],
                                                  "smart_dispatches": {"entity": "binary_sensor.edf_dispatching"}}})
    t = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)
    app.states["binary_sensor.edf_dispatching"] = "off"
    assert not app._smart_settled(t, "07:00")                       # just started
    assert not app._smart_settled(t + timedelta(minutes=10), "07:00")
    assert app._smart_settled(t + timedelta(minutes=16), "07:00")
    app.states["binary_sensor.edf_dispatching"] = "unavailable"     # EDF integration restarting
    assert not app._smart_settled(t + timedelta(minutes=17), "07:00")
    app.states["binary_sensor.edf_dispatching"] = "off"
    assert not app._smart_settled(t + timedelta(minutes=18), "07:00")
    assert app._smart_settled(t + timedelta(minutes=34), "07:00")
    assert not app._smart_settled(t + timedelta(minutes=35), "unavailable")


def test_no_mid_slot_stickiness_just_after_a_start(app):
    from datetime import timedelta

    from pe_core.decide import Decision
    t = datetime(2026, 9, 27, 17, 10, tzinfo=timezone.utc)          # 10 minutes into a half-hour
    app._decision = Decision("self_use", "plan", "x")
    app._started_at = t - timedelta(minutes=1)
    assert app._mid_slot_stick(t) == 0.0                            # the startup plan's choice isn't kept
    app._started_at = t - timedelta(minutes=6)
    assert app._mid_slot_stick(t) > 0
