"""Fixes from the first clean-install test of the demo (C4): timer handles that are Tasks, the demo's clock, the
start-up warnings, the health check before setup, settings saved from the card, and the words on the unconfigured
screen."""
import asyncio
import json
from datetime import datetime

import pytest
import test_demo_control as base
import yaml
from replay_harness import Clock
from test_demo_control import BST, send, shape, task_handle, version

make = base.make                                            # the fixture, shared with test_demo_control

AT_1640 = datetime(2026, 9, 30, 16, 40, tzinfo=BST)          # the C4 test instance's clock
LOG = []


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(base, "NOW", AT_1640)
    Clock.now = AT_1640
    LOG.clear()
    yield


def start(make, day="sunny", **kw):
    app = make(**kw)
    send(app, action="start", day=day)
    Clock.now = AT_1640
    app._cycle({})
    return app


# --- 1. handles that are Tasks (AppDaemon 4.5 called on its event loop) --------------------------------------------

def test_no_duplicate_timers_when_the_handles_are_tasks(make):
    plain, tasks = make(), make(wrap=task_handle)
    for app in (plain, tasks):
        idle = shape(app)
        send(app, action="start", day="sunny")
        first = shape(app)
        send(app, action="day", day="dull")
        send(app, action="exit")
        assert shape(app) == idle
        send(app, action="start", day="sunny")
        assert shape(app) == first
        assert len(app.timers) == len({t[3] for t in app.timers})
    assert tasks.invalid_cancels == 0                           # every cancel got a real handle, none got a Task
    assert shape(tasks) == shape(plain)
    assert all(isinstance(h[1], int) for h in tasks.__dict__["_handles"])     # recorded the handle, not the Task


def test_a_task_that_finishes_later_is_recorded_when_it_does(make):
    app = make()
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    assert app._track("timer", fut) is fut and app.__dict__["_handles"][-1:] != [("timer", 77, None)]
    before = len(app.__dict__["_handles"])
    fut.set_result(77)
    loop.run_until_complete(asyncio.sleep(0))
    assert len(app.__dict__["_handles"]) == before + 1 and app.__dict__["_handles"][-1] == ("timer", 77, None)


def test_a_task_that_finishes_after_a_wipe_is_cancelled_at_once(make):
    app = make()
    cancelled = []
    app.__dict__["cancel_timer"] = cancelled.append
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    app._track("timer", fut)
    app.__dict__["_wipes"] = app.__dict__.get("_wipes", 0) + 1       # the wipe happened while it was pending
    fut.set_result(78)
    loop.run_until_complete(asyncio.sleep(0))
    assert cancelled == [78] and ("timer", 78, None) not in app.__dict__["_handles"]


def test_a_failed_or_cancelled_task_is_not_recorded(make):
    app = make()
    loop = asyncio.new_event_loop()
    bad, gone = loop.create_future(), loop.create_future()
    bad.set_exception(RuntimeError("no"))
    gone.cancel()
    before = list(app.__dict__["_handles"])
    app._track("timer", bad)
    app._track("timer", gone)
    assert app.__dict__["_handles"] == before
    bad.exception()


def test_cancelling_with_a_task_uses_the_handle_inside_it(make):
    app = make()
    handle = app.run_every(app._beat, "now+60", 60)
    before = len(app.__dict__["_handles"])
    app.cancel_timer(task_handle(handle))
    assert len(app.__dict__["_handles"]) == before - 1 and app.invalid_cancels == 0


# --- 2 and 3. the demo's clock: solar, forecast and events at the right times ------------------------------------

def test_the_demo_runs_on_the_packs_clock_whatever_appdaemons_time_zone_is(make, monkeypatch):
    """A fresh AppDaemon is often set to UTC. The world and the app both use the recorded days' own zone, so 16:40
    London time is the 16:30 row (solar 455 W, house 384 W), not the 15:30 row an hour behind it."""
    app = make()
    monkeypatch.setattr(type(app), "get_timezone", lambda self: "UTC", raising=False)
    send(app, action="start", day="sunny")
    Clock.now = AT_1640
    app._cycle({})
    assert str(app.tz) == "Europe/London" and str(app._demo_world().tz) == "Europe/London"
    w = app._demo_world()
    assert w.get_state("sensor.demo_solar_power") == pytest.approx(455, abs=1)
    assert w.get_state("sensor.demo_inverter_house_load") == pytest.approx(384, abs=1)


def test_solar_sensors_are_published_and_the_flow_adds_up(make):
    app = start(make)
    s = {k: app.states[k]["state"] for k in ("sensor.pe_state_solar_power", "sensor.pe_state_solar_main_power",
                                            "sensor.pe_state_house_power", "sensor.pe_state_grid_power",
                                            "sensor.pe_state_battery_power")}
    assert all(v not in ("unknown", "unavailable") for v in s.values()), s
    solar, house, grid, battery = (float(s[k]) for k in ("sensor.pe_state_solar_power", "sensor.pe_state_house_power",
                                                         "sensor.pe_state_grid_power", "sensor.pe_state_battery_power"))
    assert float(s["sensor.pe_state_solar_main_power"]) == solar > 0
    assert grid == pytest.approx(house - solar - battery, abs=2)      # grid = house - solar + charge - discharge


def _forecast_local_hours(app, day):
    rows = app._demo_world().rows_for(app._demo_world().today0)
    items = app._demo_world()._forecast_attr(app._demo_world().today0)
    assert len(items) == len(rows) == 48
    return [(datetime.fromisoformat(i["period_start"]).astimezone(app.tz), i["pv_estimate"]) for i in items]


@pytest.mark.parametrize("day", ["sunny", "dull", "axle", "car"])
def test_the_solar_forecast_peaks_around_midday_local_time(make, monkeypatch, day):
    app = make()
    monkeypatch.setattr(type(app), "get_timezone", lambda self: "Asia/Tokyo", raising=False)   # another zone: same day
    send(app, action="start", day=day)
    pts = _forecast_local_hours(app, day)
    total = sum(v for _, v in pts)
    centroid = sum((t.hour + t.minute / 60 + 0.25) * v for t, v in pts) / total
    peak = max(pts, key=lambda p: p[1])[0]
    assert 10 <= centroid <= 15, centroid
    assert 8 <= peak.hour + peak.minute / 60 <= 15, peak
    assert all(v == 0 for t, v in pts if t.hour < 5 or t.hour >= 21)          # nothing at night


def test_the_plan_charts_solar_peaks_in_the_daytime(make):
    app = start(make)
    ser = app.states["sensor.pe_plan"]["attributes"]["series"]
    tz = app.tz
    peak = max(range(len(ser["t"])), key=lambda i: ser["solar_kwh"][i])
    assert 8 <= datetime.fromisoformat(ser["t"][peak]).astimezone(tz).hour <= 14
    assert len(ser["t"]) == len(ser["solar_kwh"]) == len(ser["load_kwh"])


def test_recorded_solar_in_the_world_and_its_history_is_at_the_recorded_times(make):
    app = start(make)
    w = app._demo_world()
    pack_day = w.pack["days"]["sunny"]
    d = w.today0
    rows = w.rows_for(d)
    assert [r["solar"] for r in rows] == pack_day["solar"]
    hist = w.history("sensor.demo_solar_power", datetime(2026, 9, 30, 0, 0, tzinfo=BST),
                     datetime(2026, 10, 1, 0, 0, tzinfo=BST))
    by_hour = {datetime.fromisoformat(h["last_changed"]).astimezone(app.tz).strftime("%H:%M"): float(h["state"])
               for h in hist}
    assert by_hour["10:30"] == pytest.approx(pack_day["solar"][21] * 2000, abs=1)
    assert by_hour["03:00"] == 0 and by_hour["23:30"] == 0


# --- 4. events per day ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("day", ["sunny", "dull", "axle", "car"])
def test_an_event_shows_only_on_days_whose_pack_rows_have_the_flag_at_the_recorded_times(make, day):
    app = start(make, day)
    w = app._demo_world()
    flagged = [i for i, f in enumerate(w.pack["days"][day]["axle"]) if f]
    start_state = w.get_state("sensor.demo_axle_start_time")
    end_state = w.get_state("sensor.demo_axle_end_time")
    if not flagged:
        assert start_state == end_state == "unknown"
        assert app.states["sensor.pe_state_axle"]["state"] == "No event"
        return
    first, last = flagged[0], flagged[-1]
    def local(iso):
        return datetime.fromisoformat(iso).astimezone(app.tz).strftime("%H:%M")
    end = (last + 1) * 30
    assert local(start_state) == f"{first // 2:02d}:{first % 2 * 30:02d}"
    assert local(end_state) == f"{end // 60 % 24:02d}:{end % 60:02d}"
    assert app.states["sensor.pe_state_axle"]["state"].startswith("Event at ")


def test_switching_day_changes_the_world_and_what_the_app_says_about_events(make):
    app = start(make, "sunny", wrap=task_handle)
    assert app._demo_world().day == "sunny" and app.states["sensor.pe_state_axle"]["state"] == "Event at 18:00"
    for day, event in (("dull", "No event"), ("axle", "Event at 06:30"), ("car", "No event"),
                       ("sunny", "Event at 18:00")):
        send(app, action="day", day=day)
        Clock.now = AT_1640
        app._cycle({})
        assert app._demo == app._demo_world().day == day
        assert app.states["sensor.pe_state_axle"]["state"] == event, day
        assert version(app)["demo"]["day"] == day


# --- 5 and 6. start-up: health, warnings ------------------------------------------------------------------------------

def test_health_skips_cleanly_while_unconfigured(make):
    app = make()
    app.cfg = None
    app._health()                                               # used to raise AttributeError inside a try: a warning
    assert app.states["sensor.pe_diag_health"]["state"] == "Not set up yet"
    assert not [m for m, lvl in LOG if lvl == "WARNING"]


def _capture(monkeypatch):
    import sys
    Base = sys.modules["appdaemon.plugins.hass.hassapi"].Hass
    monkeypatch.setattr(Base, "log", lambda self, msg, *a, level="INFO", **k: LOG.append((msg, level)), raising=False)


def test_a_clean_install_and_a_demo_start_log_no_warnings(make, monkeypatch):
    import sys
    app = make()
    Base = sys.modules["appdaemon.plugins.hass.hassapi"].Hass
    monkeypatch.setattr(Base, "log", lambda self, msg, *a, level="INFO", **k: LOG.append((msg, level)), raising=False)
    LOG.clear()
    app.initialize()
    send(app, action="start", day="sunny")
    Clock.now = AT_1640
    app._cycle({})
    warnings = [m for m, lvl in LOG if lvl not in ("INFO", "DEBUG")]
    assert warnings == [], warnings
    assert sum("no house-load input" in m for m, _ in LOG) <= 1


def _init_with(monkeypatch, make, set_state):
    powerengine = base.load_app(monkeypatch)
    monkeypatch.setattr(powerengine.hass.Hass, "set_state", set_state)
    app = powerengine.PowerEngine()
    app.args = {"settings_file": str(make.folder / "config.yaml")}
    Clock.now, app.tz = AT_1640, None
    app.initialize()
    return app


def test_direct_publishing_tells_appdaemon_not_to_warn_about_new_entities(make, monkeypatch):
    """AppDaemon 4.5 warns 'Entity ... not found' for every entity set_state creates unless check_existence=False; 4.4
    has no such argument (it would become an attribute), so it is passed only where set_state names it."""
    seen = []

    def new_set_state(self, entity_id, state=None, attributes=None, check_existence=True, **kw):
        seen.append((entity_id, check_existence, kw))
    _init_with(monkeypatch, make, new_set_state)
    assert len(seen) > 50 and all(check is False and not kw for _, check, kw in seen)

    seen.clear()

    def old_set_state(self, entity_id, **kwargs):
        seen.append((entity_id, kwargs))
    _init_with(monkeypatch, make, old_set_state)
    assert len(seen) > 50 and all("check_existence" not in kw for _, kw in seen)


def test_the_demos_guard_entities_exist_in_the_world_and_nothing_listens_to_them_in_appdaemon(make):
    app = start(make)
    w = app._demo_world()
    assert w.get_state("switch.demo_other_controller_read_only") == "on"
    assert w.get_state("automation.demo_legacy_battery_control") == "off"
    assert not [v for v in app.live.values() if v[0] == "state" and str(v[2]).split(".")[1].startswith("demo_")]


# --- 7. settings saved from the card ---------------------------------------------------------------------------------

def test_ad_would_alter_flags_what_appdaemons_rest_write_changes():
    from pe_core.adapters.publish import ad_would_alter
    assert all(ad_would_alter(v) for v in (True, False, None, 0, 0.0, [1, 0], {"a": {"b": False}}, [[None]]))
    assert not any(ad_would_alter(v) for v in ("true", "", 1, 2.5, [1, 2], {"a": "x"}, [], {}))


def test_attributes_appdaemons_set_state_would_change_are_written_exactly_as_given(make, monkeypatch):
    """AppDaemon 4.5's REST set_state turns true into "true" and drops false, null and every 0 from the attributes
    (the config's booleans arrived as text: 'feature ... must be true or false'; a series lost its zeros so the
    solar forecast plotted hours early). Such attributes go to Home Assistant with the exact JSON instead."""
    app = make()
    monkeypatch.setattr(type(app), "_appdaemon_cleans_attributes", staticmethod(lambda: True))
    sent, plain = [], []
    monkeypatch.setattr(type(app), "_rest_states_poster",
                        lambda self: lambda eid, state, attrs: sent.append((eid, state, attrs)))
    write = app._lossless_set_state(lambda entity_id, **kw: plain.append((entity_id, kw)))
    attrs = {"config": {"features": {"a": True, "b": False}}, "series": {"solar_kwh": [0.0, 0, 1.5]}}
    write("sensor.pe_map_config", state="ok", attributes=attrs)
    assert sent == [("sensor.pe_map_config", "ok", attrs)] and plain == []
    write("sensor.pe_state_grid_power", state="5", attributes={"unit": "W"})
    assert len(plain) == 1 and len(sent) == 1
    write("sensor.other_thing", state="on", attributes={"x": False})            # not ours: AppDaemon's own path
    assert len(plain) == 2 and len(sent) == 1


def test_without_a_way_round_appdaemon_the_first_altered_write_is_reported_once(make, monkeypatch):
    app = make()
    monkeypatch.setattr(type(app), "_appdaemon_cleans_attributes", staticmethod(lambda: True))
    LOG.clear()
    import sys
    Base = sys.modules["appdaemon.plugins.hass.hassapi"].Hass
    monkeypatch.setattr(Base, "log", lambda self, msg, *a, level="INFO", **k: LOG.append((msg, level)), raising=False)
    monkeypatch.setattr(type(app), "_rest_states_poster", lambda self: None)
    plain = []
    write = app._lossless_set_state(lambda entity_id, **kw: plain.append(entity_id))
    for _ in range(3):
        write("sensor.pe_map_config", state="ok", attributes={"f": False})
    assert len(plain) == 3 and sum("AppDaemon's set_state changes" in m for m, _ in LOG) == 1


def test_where_appdaemon_does_not_clean_set_state_is_used_as_it_is(make):
    app = make()
    assert app._rest_states_poster() is None and not app._appdaemon_cleans_attributes()   # (4.4, or none here)
    plain = lambda entity_id, **kw: None                                                    # noqa: E731
    assert app._lossless_set_state(plain) is plain


def test_a_config_whose_booleans_arrived_as_text_saves(make):
    app = start(make)
    cfg = json.loads(json.dumps(app.states["sensor.pe_map_config"]["attributes"]["config"]))
    cfg["features"] = {k: ("true" if v else "false") for k, v in cfg["features"].items()}
    cfg["system"]["house_load_includes_ev"] = "true"
    n = len(app.real_events)
    app._on_save("pe_config_save", {"config": cfg}, {})
    assert [kw for ev, kw in app.real_events[n:] if ev == "pe_config_result"][0]["ok"] is True


def test_coerce_flags_only_touches_true_false_settings():
    from pe_core.store import coerce_flags
    raw = {"features": {"axle": "true", "arbitrage": "False", "x": 1, "bad": "maybe"},
           "system": {"battery_location": "garage", "house_load_includes_ev": "on"},
           "notifications": {"service": "notify.me", "events": {"health": "false"}},
           "solar_plants": [{"id": "m", "enabled": "true"}],
           "inputs": {"battery_power": {"entity": "sensor.x", "invert": "true"}}, "remove_entities": "false"}
    out = coerce_flags(raw)
    assert out["features"] == {"axle": True, "arbitrage": False, "x": True, "bad": "maybe"}
    assert out["system"] == {"battery_location": "garage", "house_load_includes_ev": True}
    assert out["notifications"]["events"] == {"health": False} and out["notifications"]["service"] == "notify.me"
    assert out["solar_plants"][0]["enabled"] is True and out["inputs"]["battery_power"]["invert"] is True
    assert out["remove_entities"] is False and raw["features"]["axle"] == "true"     # a copy


# --- 8. words on the unconfigured screen ----------------------------------------------------------------------------

def test_the_mode_and_health_say_not_set_up_yet_before_setup(make):
    app = make()
    assert app.states["sensor.pe_state_status"]["state"] == "Not set up yet"
    assert app.states["sensor.pe_diag_health"]["state"] == "Not set up yet"


def test_a_config_with_missing_inputs_is_still_blocked(make):
    app = make(real_config=True)
    assert app.states["sensor.pe_state_status"]["state"] != "Not set up yet"


def test_the_dashboard_shows_a_neutral_tile_for_not_set_up_yet_and_hides_the_health_alert():
    from pathlib import Path
    doc = yaml.safe_load((Path(__file__).parent.parent / "apps/powerengine/dashboard/dashboard.lovelace").read_text())
    tiles = [c for v in doc["views"] for s in v.get("sections", []) for c in s.get("cards", [])
             if c.get("type") == "tile" and c.get("entity") in ("sensor.pe_state_status", "sensor.pe_diag_health")]
    neutral = [t for t in tiles if any(c.get("state") == "Not set up yet" for c in t.get("visibility", []))]
    assert {t["entity"] for t in neutral} == {"sensor.pe_state_status", "sensor.pe_diag_health"}
    assert all("color" not in t for t in neutral)
    red = [t for t in tiles if t.get("color") == "red"]
    assert red and all(not any(c.get("state") == "Not set up yet" for c in t.get("visibility", [])) for t in red)
    alert = next(t for t in tiles if t.get("name") == "Health needs a look")
    assert {c.get("state_not") for c in alert["visibility"]} == {"ok", "Not set up yet"}


def test_the_welcome_lists_the_days_with_the_names_the_demo_will_show(make):
    app = make()
    days = version(app)["demo_days"]
    assert [d["key"] for d in days] == ["sunny", "dull", "axle", "car"]
    started = start(make)
    assert [d["title"] for d in days] == [d["title"] for d in version(started)["demo"]["days"]]
    assert "event day" in next(d["title"] for d in days if d["key"] == "axle") and "<<" not in json.dumps(days)


def test_a_configured_or_running_app_does_not_publish_the_preview(make):
    assert "demo_days" not in version(make(real_config=True))
    assert "demo_days" not in version(start(make))


# --- round 2: "Invalid callback handle" at a wipe, and the plugin check -------------------------------------------

@pytest.mark.parametrize("wrap", [None, task_handle])
def test_a_wipe_cancels_each_timer_once_so_appdaemon_logs_no_invalid_handles(make, wrap):
    """run_daily is built on run_every inside AppDaemon, so both wrappers recorded the same handle: the wipe cancelled
    it twice and AppDaemon logged 'Invalid callback handle' for the second (five run_daily timers, five warnings)."""
    app = make(wrap=wrap)
    send(app, action="start", day="sunny")
    send(app, action="day", day="dull")
    send(app, action="exit")
    send(app, action="start", day="car")
    assert app.invalid_cancels == 0
    handles = [h[1] for h in app.__dict__["_handles"]]
    assert len(handles) == len(set(handles))
    assert sorted(handles) == sorted(app.live)                  # nothing left over from earlier starts: no leak


def test_a_timer_appdaemon_no_longer_has_is_not_cancelled(make):
    app = make()
    send(app, action="start", day="sunny")
    gone = next(h for h, v in app.live.items() if v[1] == "_beat")
    del app.live[gone]                                          # it ran (one-shot) or was cancelled elsewhere
    send(app, action="day", day="dull")
    assert app.invalid_cancels == 0


def test_a_handle_is_recorded_once(make):
    app = make()
    n = len(app.__dict__["_handles"])
    app._track("timer", 555)
    app._track("timer", 555)
    app._track("timer", task_handle(555))
    assert len(app.__dict__["_handles"]) == n + 1


def test_the_mqtt_check_does_not_ask_appdaemon_for_a_plugin_it_has_not_got(make):
    """get_plugin_api logs a WARNING ('Unknown Plugin Configuration') when the plugin isn't configured."""
    import types
    app = make()
    asked = []
    type(app).get_plugin_api = lambda self, name: asked.append(name)
    app.AD = types.SimpleNamespace(plugins=types.SimpleNamespace(config={"HASS": object()}))
    assert app._mqtt_api(quiet=True) is None and asked == []
    app.AD = types.SimpleNamespace(plugins=types.SimpleNamespace(config={"HASS": 1, "MQTT": 2}))
    app._mqtt_api(quiet=True)
    assert asked == ["MQTT"]
    del app.AD                                                  # list unreadable (older AppDaemon): ask, as before
    app._mqtt_api(quiet=True)
    assert asked == ["MQTT", "MQTT"]
