from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pe_core.history import DAY_OPTIONS, PLAN_OPTIONS, chosen_day, chosen_plan, day_view

LON = ZoneInfo("Europe/London")
UTC = timezone.utc
TODAY = date(2026, 9, 26)


def test_day_choice():
    assert chosen_day("Today", TODAY) == TODAY
    assert chosen_day("Yesterday", TODAY) == date(2026, 9, 25)
    assert chosen_day("7 days ago", TODAY) == date(2026, 9, 19)
    assert chosen_day(None, TODAY) == date(2026, 9, 25)
    assert len(DAY_OPTIONS) == 31 and len(PLAN_OPTIONS) == 25


def test_plan_choice_falls_back_to_earlier_hour():
    sod, h = {"made_at": "a"}, {"00:00": {"made_at": "b"}, "06:00": {"made_at": "c"}}
    assert chosen_plan("Start of day", sod, h) == ("Start of day", sod)
    assert chosen_plan("09:00", sod, h) == ("06:00", h["06:00"])
    assert chosen_plan("06:00", sod, h)[0] == "06:00"
    assert chosen_plan("09:00", sod, {}) == ("Start of day", sod)
    assert chosen_plan("Start of day", None, h) == ("06:00", h["06:00"])
    assert chosen_plan(None, None, {}) == (None, None)


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
    v = day_view(day, recs, snap, "Start of day", LON, datetime(2026, 9, 26, 12, tzinfo=UTC), ["Start of day"])
    s = v["series"]
    assert len(s["t"]) == 48 and s["actual_soc"][0] == 50 and s["plan_soc"][0] == 51
    assert v["summary"]["half_hours"] == 48 and v["summary"]["soc_gap"] == 1.0
    assert v["summary"]["plan_cost"] == 2.4 and v["summary"]["actual_cost"] == 2.88
    first = datetime.fromtimestamp(s["x"][0] / 1000, LON)
    assert (first.date(), first.hour, first.minute) == (date(2026, 9, 26), 0, 0)   # moved onto today


def test_day_view_across_clock_change_keeps_local_times():
    day = date(2026, 10, 25)                                  # 25-hour day
    recs, snap = _day(day)
    v = day_view(day, recs, None, None, LON, datetime(2026, 10, 27, 12, tzinfo=UTC), [])
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
