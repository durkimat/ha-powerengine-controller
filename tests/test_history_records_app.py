"""The recording wired into the app: forecast snapshots (never in a demo), the engine tag on cost records, engine v2's
history and its sensor, the v2 day picker, and v1's as-run plan only while engine v1 is the chosen engine."""
import json
import sys
import types
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from test_engine_v2_app import simulate

from pe_core import fcsnap
from pe_core.forecast import LoadProfile
from pe_core.history import PASSIVE_LABEL
from pe_core.names import set_current as set_names

LON = ZoneInfo("Europe/London")
LIMIT_V2_HISTORY = 12_000


@pytest.fixture
def mp():
    m = pytest.MonkeyPatch()
    yield m
    m.undo()
    sys.modules.pop("powerengine", None)
    set_names(None)


@pytest.fixture(scope="module")
def v2_run(tmp_path_factory):
    m = pytest.MonkeyPatch()
    try:
        yield simulate(tmp_path_factory.mktemp("ha"), m, 2.5, engine="v2")
    finally:
        m.undo()
        sys.modules.pop("powerengine", None)
        set_names(None)                                   # the app sets the module's names map: put his words back


@pytest.fixture(scope="module")
def v1_run(tmp_path_factory):
    m = pytest.MonkeyPatch()
    try:
        yield simulate(tmp_path_factory.mktemp("ha"), m, 1.6)
    finally:
        m.undo()
        sys.modules.pop("powerengine", None)
        set_names(None)                                   # the app sets the module's names map: put his words back


def published(app, call, key):
    """What `call()` publishes under `key` ((state, attributes)): the history sensors go straight to the publisher."""
    got = []
    original = app._publish_state
    app._publish_state = lambda k, state, attrs=None: got.append((state, attrs)) if k == key else None
    try:
        call()
    finally:
        app._publish_state = original
    return got[-1] if got else None


def today_of(app):
    from replay_harness import Clock
    return Clock.now.astimezone(app.tz or timezone.utc).date()


def test_no_snapshots_in_a_demo(v2_run, v1_run):
    for run in (v2_run, v1_run):
        assert run["app"]._fcsnap is None
        assert not list(run["folder"].rglob("snapshots"))


def test_engine_v2_records_its_history_and_publishes_it(v2_run):
    app = v2_run["app"]
    assert app._v2hist is not None
    state, attrs = published(app, app._publish_v2_history, "v2_history")
    assert state == attrs["date"] == today_of(app).isoformat()
    assert attrs["latest"] == attrs["date"] and attrs["in_control"] == "v2" and attrs["live"] is True
    ran = [r for r in attrs["series"] if r["mode"]]
    assert len(ran) >= 3 and all(r["sent"] for r in ran) and not any(r.get("preview") for r in ran)
    assert any(r["expected"] is not None for r in attrs["series"]) and attrs["changes"]
    assert attrs["preview_only"] is False
    size = len(json.dumps(attrs, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    assert size < LIMIT_V2_HISTORY
    assert [m for m in app.warnings if "history" in str(m).lower() and "Could not" in str(m)] == []


def test_engine_v2_publishes_its_recent_hours_whatever_day_the_picker_shows(v2_run):
    app = v2_run["app"]
    app._v2_history_date = today_of(app) - timedelta(days=3)               # the picker is on another day
    state, attrs = published(app, app._publish_v2_recent, "v2_recent")
    assert state == str(len(attrs["series"])) and attrs["hours"] == 18 and attrs["step_min"] == 30
    starts = [r["t"] for r in attrs["series"]]
    assert all(r["sent"] for r in attrs["series"]) and starts == sorted(starts)
    assert len(json.dumps(attrs, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) < 6_000
    app._v2_history_date = None


def test_cost_records_are_tagged_with_the_engine_that_was_chosen(v2_run, v1_run):
    for run, engine in ((v2_run, "v2"), (v1_run, "v1")):
        app = run["app"]
        recs = [x for d in app.costbook.recorded_days() for x in app.costbook.day_records(date.fromisoformat(d))]
        live = [x for x in recs if x.get("source") == "live"]
        assert live and all(x["engine"] == engine and x["live"] is True for x in live)
        day = date.fromisoformat(app.costbook.recorded_days()[-1])
        assert app.costbook.day_engine(day) == engine


def test_v1_as_run_plan_is_recorded_only_while_v1_is_chosen(v2_run, v1_run):
    app1, app2 = v1_run["app"], v2_run["app"]
    d1, d2 = today_of(app1), today_of(app2)
    assert app1.costbook.ran_plan(d1) and app1.costbook.ran_plan(d1)["slots"]
    assert app2.plan is not None and app2.costbook.ran_plan(d2) is None       # v1 still plans, but nothing "ran"
    assert app2.costbook.plan_snapshot(d2) is not None                        # the start-of-day plan is kept


def test_plan_history_says_v1_was_passive_when_v2_ran(v2_run, v1_run):
    app = v2_run["app"]
    app._history_date = today_of(app)
    attrs = published(app, app._publish_history, "plan_history")[1]
    assert attrs["in_control"] == "v2" and attrs["live"] is True and attrs["plan"] == PASSIVE_LABEL
    app = v1_run["app"]
    app._history_date = today_of(app)
    attrs = published(app, app._publish_history, "plan_history")[1]
    assert attrs["in_control"] == "v1" and attrs["plan"] == "As run"


def test_v2_records_a_preview_while_v1_is_in_control(v1_run):
    app = v1_run["app"]
    attrs = published(app, app._publish_v2_history, "v2_history")[1]
    ran = [r for r in attrs["series"] if r["mode"]]
    assert ran and all(r["preview"] is True and r["sent"] is False for r in ran)
    assert attrs["preview_only"] is True and attrs["in_control"] == "v1"


def test_the_v2_day_picker_shows_the_picked_day_inside_the_kept_days(v2_run):
    app = v2_run["app"]
    today = today_of(app)
    app._v2_history_date = None

    def pick(data, event="pe_v2_history_day"):
        return published(app, lambda: app._on_v2_history_day(event, data, {}), "v2_history")

    for bad in ((today + timedelta(days=2)).isoformat(), (today - timedelta(days=45)).isoformat(), "x", None):
        assert pick({"date": bad}) is None
    assert pick({"date": today.isoformat()}, "other_event") is None and pick(None) is None
    assert app._v2_history_date is None
    older = today - timedelta(days=3)
    state, attrs = pick({"date": older.isoformat()})
    assert state == older.isoformat() and attrs["series"] == [] and attrs["latest"] == today.isoformat()
    assert attrs["earliest"] <= older.isoformat()
    app._v2_history_date = None


# --- the snapshot hook, bound from the real class ------------------------------------------------------------------

def _hook_app(monkeypatch, tmp_path, states):
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine as pe
    monkeypatch.setattr(pe, "datetime", type("D", (datetime,), {
        "now": classmethod(lambda cls, tz=None: datetime(2026, 10, 7, 12, tzinfo=timezone.utc).astimezone(tz))}))
    logs = []
    cfg = types.SimpleNamespace(inputs={"solar_forecast_today": {"entity": "sensor.solcast_today"},
                                        "import_rates_today": {"entity": "event.rates"},
                                        "battery_soc": {"entity": "sensor.soc"}})
    app = types.SimpleNamespace(
        _fcsnap=fcsnap.SnapshotWriter(str(tmp_path), LON), _demo=None, cfg=cfg, tz=LON,
        profile=LoadProfile({(False, 0): 400.0}, 10.0), logs=logs, _warned_days={},
        slots=types.SimpleNamespace(slots={"2026-10-07T01:30:00+01:00": {"first_seen": "2026-10-06T19:00:00+01:00"}}))
    app.get_state = lambda eid, attribute=None: states(eid)
    app.log = lambda msg, *a, **k: logs.append((k.get("level"), msg))
    for name in ("_snapshot_forecast", "_warn_daily"):
        setattr(app, name, types.MethodType(getattr(pe.PowerEngine, name), app))
    return app


def test_the_cycle_hook_saves_the_mapped_future_roles_only(monkeypatch, tmp_path):
    seen = []

    def states(eid):
        seen.append(eid)
        return {"state": "1", "attributes": {"x": [1, 2]}}
    app = _hook_app(monkeypatch, tmp_path, states)
    app._snapshot_forecast(types.SimpleNamespace(now=datetime(2026, 10, 7, 0, 5, tzinfo=timezone.utc)))
    assert sorted(set(seen)) == ["event.rates", "sensor.solcast_today"]          # not the battery level
    snap = json.load(open(tmp_path / "2026-10-07.json"))
    assert set(snap["entries"][0]["states"]) == {"event.rates", "sensor.solcast_today"}
    assert snap["entries"][0]["profile"]["watts"] == {"0|0": 400.0}
    assert snap["entries"][0]["first_seen"] == {"2026-10-07T01:30:00+01:00": "2026-10-06T19:00:00+01:00"}
    assert snap["entries"][0]["at"] == "2026-10-07T01:05:00+01:00"


def test_a_failing_snapshot_warns_once_a_day_and_never_raises(monkeypatch, tmp_path):
    def boom(eid):
        raise RuntimeError("HA went away")
    app = _hook_app(monkeypatch, tmp_path, boom)
    r = types.SimpleNamespace(now=datetime(2026, 10, 7, 0, 5, tzinfo=timezone.utc))
    for _ in range(5):
        app._snapshot_forecast(r)
    assert len([m for m in app.logs if m[0] == "WARNING"]) == 1
    app._demo = "sunny"                                                          # a demo never snapshots
    app._fcsnap = fcsnap.SnapshotWriter(str(tmp_path / "x"), LON)
    app.get_state = lambda *a, **k: {"state": "1", "attributes": {}}
    app._snapshot_forecast(r)
    assert not (tmp_path / "x").exists()
