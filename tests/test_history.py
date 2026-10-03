from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pe_core.history import DAY_OPTIONS, chosen_day, chosen_plan, day_view

LON = ZoneInfo("Europe/London")
UTC = timezone.utc
TODAY = date(2026, 9, 26)


def test_day_choice():
    assert chosen_day("Today", TODAY) == TODAY
    assert chosen_day("Yesterday", TODAY) == date(2026, 9, 25)
    assert chosen_day("7 days ago", TODAY) == date(2026, 9, 19)
    assert chosen_day(None, TODAY) == date(2026, 9, 25)
    assert len(DAY_OPTIONS) == 31


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


def _picker_app(monkeypatch, tmp_path, select="Yesterday"):
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
    app = types.SimpleNamespace(costbook=book, tz=LON, _history_date=None, select=select, published=published)
    app.get_state = lambda eid: app.select if eid == "select.pe_ui_history_day" else "As run"
    app._get_publisher = lambda: object()
    app._publish_state = lambda key, state, attrs=None: published.append((key, state, attrs))
    app.log = lambda *a, **k: None
    for name in ("_publish_history", "_on_history_day", "_on_history_select"):
        setattr(app, name, types.MethodType(getattr(pe.PowerEngine, name), app))
    return app, pe


def test_date_picker_shows_the_picked_day_until_the_select_is_used(monkeypatch, tmp_path):
    app, pe = _picker_app(monkeypatch, tmp_path)
    app._on_history_day("pe_history_day", {"date": "2026-03-04"}, {})
    assert app.published[-1][1] == "2026-03-04"
    assert app.published[-1][2]["latest"] == "2026-10-03" and app.published[-1][2]["earliest"] <= "2026-03-04"
    app._on_history_select("select.pe_ui_history_day", "state", "7 days ago", "Yesterday", {})
    assert app._history_date is None and app.published[-1][1] == "2026-10-02"


def test_date_picker_ignores_bad_future_and_too_old_days(monkeypatch, tmp_path):
    app, pe = _picker_app(monkeypatch, tmp_path)
    for bad in ("2026-10-04", "2025-01-01", "not a date", None):
        app._on_history_day("pe_history_day", {"date": bad}, {})
    app._on_history_day("pe_history_day", None, {})
    app._on_history_day("other_event", {"date": "2026-09-01"}, {})
    assert app._history_date is None and not app.published
