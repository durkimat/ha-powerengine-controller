import types
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pe_core.history import add_forecast, chosen_plan, day_view, real_flows
from pe_core.readings import Readings

LON = ZoneInfo("Europe/London")
TODAY = date(2026, 9, 26)
UTC = timezone.utc

def _day(day):
    start = datetime(day.year, day.month, day.day, tzinfo=LON).astimezone(UTC)
    recs, slots = [], []
    for i in range(48):
        t = start + timedelta(minutes=30 * i)
        recs.append({"start": t.isoformat(), "seconds": 1800, "soc_end": 50 + i / 2, "import_rate": 0.3,
                     "export_rate": 0.15, "grid_import": 0.2, "grid_export": 0.0, "g_b": 0.0, "b_e": 0.0,
                     "s_e": 0.1, "house": 0.4, "solar": 0.3})
        slots.append({"start": t.isoformat(), "soc": 50 + i / 2 + 1, "cost": 0.05, "charge_kwh": 0.0,
                      "bat_export_kwh": 0.0, "solar_export_kwh": 0.05, "load_kwh": 0.5, "solar_kwh": 0.3,
                      "price_p": 30.0})
    return recs, {"made_at": start.isoformat(), "slots": slots, "windows": []}


def test_day_view_pairs_plan_and_actual():
    day = date(2026, 9, 24)
    recs, snap = _day(day)
    v = day_view(day, recs, snap, "Start of day", LON, datetime(2026, 9, 26, 12, tzinfo=UTC))
    s = v["series"]
    assert len(s["t"]) == 48 and s["actual_soc"][0] == 50 and s["plan_soc"][0] == 51
    assert v["summary"]["half_hours"] == 48 and v["summary"]["soc_gap"] == 1.0
    assert v["summary"]["plan_cost"] == 2.4 and v["summary"]["actual_cost"] == 2.88
    first = datetime.fromtimestamp(s["x"][0] / 1000, LON)
    assert (first.date(), first.hour, first.minute) == (date(2026, 9, 26), 0, 0)   # moved onto today


def test_day_view_across_clock_change_keeps_local_times():
    day = date(2026, 10, 25)                                  # 25-hour day
    recs, snap = _day(day)
    v = day_view(day, recs, None, None, LON, datetime(2026, 10, 27, 12, tzinfo=UTC))
    assert len(v["series"]["t"]) == 50 and v["summary"] is None
    last = datetime.fromtimestamp(v["series"]["x"][-1] / 1000, LON)
    assert (last.hour, last.minute) == (23, 30)


def test_hourly_plans_are_stored_and_pruned_after_60_days(tmp_path):
    from pe_core.costbook import CostBook
    book = CostBook(str(tmp_path), UTC)
    old, recent = date(2026, 7, 1), date(2026, 9, 20)
    for d in (old, recent):
        book.save_plan_history(d, "06:00", {"made_at": "x", "slots": []})
        book.save_plan_history(d, "07:00", {"made_at": "y", "slots": []})
    assert sorted(book.plan_history(recent)) == ["06:00", "07:00"]
    book.prune(TODAY)
    assert book.plan_history(old) == {} and book.plan_history(recent)


def test_snapshot_keeps_what_the_history_tab_needs():
    from test_planner import T0, day

    from pe_core.health import plan_snapshot
    from pe_core.planner import Params, make_plan
    plan = make_plan(day(n=48, solar=1.0), soc=90.0, p=Params(), now=T0)
    snap = plan_snapshot(plan, T0, T0 + timedelta(days=1))
    s = snap["slots"][0]
    assert {"price_p", "charge_kwh", "bat_export_kwh", "solar_export_kwh", "import_kwh", "cost"} <= s.keys()
    assert snap["windows"] and "reason" in snap["windows"][0] and "day" not in snap["windows"][0]


def test_the_plan_shown_is_the_one_that_ran_else_the_start_of_day_plan():
    sod, ran = {"made_at": "a"}, {"made_at": None, "slots": [{"start": "x"}]}
    assert chosen_plan(sod, ran) == ("As run", ran)
    assert chosen_plan(sod, None) == ("Start of day", sod)
    assert chosen_plan(sod, {"slots": []}) == ("Start of day", sod)
    assert chosen_plan(None, None) == (None, None)


def test_ran_plan_keeps_latest_version_of_each_half_hour_and_is_pruned_after_400_days(tmp_path):
    from pe_core.costbook import CostBook
    book = CostBook(str(tmp_path))
    day = date(2026, 9, 26)
    w = {"start": "s", "end": "e", "action": "charge"}
    book.record_ran(day, {"start": "2026-09-26T00:30:00+00:00", "soc": 50}, None)
    book.record_ran(day, {"start": "2026-09-26T00:00:00+00:00", "soc": 40}, w)
    book.record_ran(day, {"start": "2026-09-26T00:30:00+00:00", "soc": 55}, {**w, "end": "e2"})
    ran = book.ran_plan(day)
    assert [(s["start"][11:16], s["soc"]) for s in ran["slots"]] == [("00:00", 40), ("00:30", 55)]
    assert ran["windows"] == [{**w, "end": "e2"}]
    book.prune(day + timedelta(days=399))
    assert book.ran_plan(day) is not None
    book.prune(day + timedelta(days=401))
    assert book.ran_plan(day) is None


def _picker_app(monkeypatch, tmp_path):
    """Just enough of the app to run the date picker's handlers, bound from the real class."""
    import sys
    import types

    from pe_core.costbook import CostBook
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine as pe

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 3, 12, tzinfo=UTC).astimezone(tz) if tz else datetime(2026, 10, 3, 12, tzinfo=UTC)

    monkeypatch.setattr(pe, "datetime", FakeDT)
    book = CostBook(str(tmp_path), LON)
    published = []
    app = types.SimpleNamespace(costbook=book, tz=LON, _history_date=None, published=published, cfg=None,
                                _flows_cache={})
    app._get_publisher = lambda: object()
    app._publish_state = lambda key, state, attrs=None: published.append((key, state, attrs))
    app.log = lambda *a, **k: None
    for name in ("_publish_history", "_on_history_day", "_day_engine", "_history_latest", "_day_flows"):
        setattr(app, name, types.MethodType(getattr(pe.PowerEngine, name), app))
    return app, pe


def test_date_picker_shows_the_picked_day_and_yesterday_until_then(monkeypatch, tmp_path):
    app, pe = _picker_app(monkeypatch, tmp_path)
    app._publish_history()
    assert app.published[-1][1] == "2026-10-02"                  # nothing picked yet: yesterday
    app._on_history_day("pe_history_day", {"date": "2026-03-04"}, {})
    assert app.published[-1][1] == "2026-03-04"
    assert app.published[-1][2]["latest"] == "2026-10-03" and app.published[-1][2]["earliest"] <= "2026-03-04"


def test_date_picker_ignores_bad_future_and_too_old_days(monkeypatch, tmp_path):
    app, pe = _picker_app(monkeypatch, tmp_path)
    for bad in ("2026-10-04", "2025-01-01", "not a date", None):
        app._on_history_day("pe_history_day", {"date": bad}, {})
    app._on_history_day("pe_history_day", None, {})
    app._on_history_day("other_event", {"date": "2026-09-01"}, {})
    assert app._history_date is None and not app.published


def test_as_run_windows_drop_replaced_copies_and_merge():
    from zoneinfo import ZoneInfo

    from pe_core.history import as_run_windows
    t = [f"2026-10-03T{h:02d}:{m:02d}:00+00:00" for h in (7, 8) for m in (0, 30)] + ["2026-10-03T09:00:00+00:00"]
    slots = [{"start": x, "action": "self_use"} for x in t[:4]]
    ws = [{"start": t[0], "end": t[4], "from": "08:00", "to": "10:00", "action": "self_use", "reason": "r"},
          {"start": t[1], "end": t[4], "from": "08:30", "to": "10:00", "action": "self_use", "reason": "r"},
          {"start": t[0], "end": t[1], "from": "08:00", "to": "08:30", "action": "hold", "reason": "old"}]
    out = as_run_windows(ws, slots, ZoneInfo("Europe/London"))
    assert [(w["from"], w["to"], w["action"]) for w in out] == [("08:00", "10:00", "self_use")]


def _live(day, from_slot=0):
    recs, snap = _day(day)
    snap["slots"] = [{**s, "soc": 80.0, "price_p": 7.0} for s in snap["slots"][from_slot:]]
    snap["windows"] = [{"start": snap["slots"][0]["start"], "action": "grid_charge", "reason": "cheap"}]
    return snap


def test_add_forecast_keeps_what_ran_and_fills_the_rest_from_the_live_plan():
    day = date(2026, 9, 26)
    _, ran = _day(day)
    ran["slots"] = ran["slots"][:24]                                  # the first 24 half-hours ran
    now = datetime(2026, 9, 26, 10, 40, tzinfo=UTC)                   # 11:40 local
    label, snap = add_forecast("As run", ran, _live(day), now)
    assert label == "As run"
    by = {s["start"]: s for s in snap["slots"]}
    assert len(by) == 48
    assert all(by[s["start"]]["soc"] == s["soc"] for s in ran["slots"])          # what ran is kept as it was
    assert by[datetime(2026, 9, 26, 11, 0, tzinfo=UTC).isoformat()]["soc"] == 80.0   # later half-hours: live plan


def test_add_forecast_for_a_coming_day_is_the_live_plan_labelled_forecast():
    day = date(2026, 9, 28)
    now = datetime(2026, 9, 26, 12, tzinfo=UTC)
    label, snap = add_forecast(None, None, _live(day), now)
    assert label == "Forecast" and len(snap["slots"]) == 48 and snap["windows"][0]["action"] == "grid_charge"
    v = day_view(day, [], snap, label, LON, now)
    assert v["forecast"] is True and v["now_x"] is None
    assert v["series"]["plan_soc"][0] == 80.0 and v["series"]["actual_soc"][0] is None
    assert v["series"]["price_p"][0] == 7.0


def test_add_forecast_without_a_live_plan_changes_nothing():
    _, ran = _day(date(2026, 9, 26))
    now = datetime(2026, 9, 26, 12, tzinfo=UTC)
    assert add_forecast("As run", ran, None, now) == ("As run", ran)
    assert add_forecast(None, None, {"slots": []}, now) == (None, None)


def test_day_view_marks_now_only_on_today():
    day = date(2026, 9, 26)
    recs, snap = _day(day)
    now = datetime(2026, 9, 26, 12, tzinfo=UTC)
    assert day_view(day, recs, snap, "x", LON, now)["now_x"] == int(now.timestamp() * 1000)
    past = day_view(date(2026, 9, 25), [], None, None, LON, now)
    assert past["now_x"] is None and past["forecast"] is False


def test_date_picker_reaches_into_the_plan_horizon_and_no_further(monkeypatch, tmp_path):
    app, pe = _picker_app(monkeypatch, tmp_path)
    def slot(d, h):
        return types.SimpleNamespace(slot=types.SimpleNamespace(start=datetime(2026, 10, d, h, tzinfo=UTC)))

    app.plan = types.SimpleNamespace(slots=[slot(3, 12), slot(4, 12), slot(5, 12)])       # ends on the 5th
    assert app._history_latest(date(2026, 10, 3)) == date(2026, 10, 5)
    app.plan.slots.append(slot(8, 12))
    assert app._history_latest(date(2026, 10, 3)) == date(2026, 10, 5)                  # two days ahead at most
    app.plan.slots = [slot(3, 12)]
    assert app._history_latest(date(2026, 10, 3)) == date(2026, 10, 3)
    app.plan = None
    assert app._history_latest(date(2026, 10, 3)) == date(2026, 10, 3)


def _sample(minute, grid_w, battery_w, hour=0):
    t = datetime(2026, 10, 3, hour, minute, tzinfo=UTC)
    return Readings(now=t, grid_power=grid_w, battery_power=battery_w)


def test_real_flows_say_when_charging_and_exports_happened():
    samples = ([_sample(m, 3000, -2800) for m in (0, 1, 2, 3, 4)]          # 00:00-00:05 charging 2.8 kW from the grid
               + [_sample(m, -4000, 3000) for m in (5, 6, 7, 8, 9)]         # then exporting 4 kW, 3 kW from the battery
               + [_sample(m, -1000, 0) for m in (10, 11)]                   # then 1 kW of solar only
               + [_sample(m, None, None) for m in (12, 13)])                # no grid reading: skipped
    f = real_flows(samples, LON)
    assert f["bucket"] == 5 and f["m"] == [60, 65, 70]                      # BST: local = UTC + 1h
    assert f["charge"] == [1.4, 0.0, 0.0]                                    # 2.8 kW for half an hour = 1.4 kWh
    assert f["bat_export"] == [0.0, 1.5, 0.0] and f["solar_export"] == [0.0, 0.5, 0.5]


def test_real_flows_leave_out_quiet_buckets_and_keep_the_rest():
    f = real_flows([_sample(0, 100, 0), _sample(5, 3000, -2800), _sample(10, 0, 0)], UTC)
    assert f["m"] == [5] and f["charge"] == [1.4]
    quiet = real_flows([_sample(0, 100, 0)], UTC)
    assert quiet["m"] == [] and quiet["charge"] == []                        # real, but nothing moved


def test_real_flows_without_battery_power_call_all_export_solar_and_empty_input_is_none():
    f = real_flows([_sample(0, -2000, None)], UTC)
    assert f["bat_export"] == [0.0] and f["solar_export"] == [1.0]
    assert real_flows([], UTC) is None and real_flows([_sample(0, None, None)], UTC) is None


def test_the_published_history_carries_real_flows_when_the_app_has_them(monkeypatch, tmp_path):
    app, pe = _picker_app(monkeypatch, tmp_path)
    app._day_flows = lambda day, today, now: {"bucket": 5, "m": [0], "charge": [0.1], "bat_export": [0.0],
                                              "solar_export": [0.0]}
    app._publish_history()
    assert app.published[-1][2]["real"]["m"] == [0]
    app2, _ = _picker_app(monkeypatch, tmp_path)
    app2._publish_history()
    assert app2.published[-1][2]["real"] is None                              # no config: nothing to read
