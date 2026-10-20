"""Engine v2 history (pe_core/v2history.py) and the engine tag on cost records (costbook.day_engine)."""
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from test_costs import CHEAP, T0, R, day_rates

from pe_core import v2history
from pe_core.costbook import CostBook, _write_json
from pe_core.energy import Recorder
from pe_core.history import PASSIVE_LABEL, chosen_plan
from pe_core.v2history import V2History

LON = ZoneInfo("Europe/London")
DAY = date(2026, 10, 7)


def t(h, m=0, s=0, day=7):
    return datetime(2026, 10, day, h, m, s, tzinfo=LON)


def path_from(start, n=200, step=15, base=50.0, slope=0.1):
    return {"start": start.isoformat(), "step_min": step, "mid": [round(base + slope * i, 1) for i in range(n)],
            "low": [], "high": []}


def read(p):
    return json.load(open(p, encoding="utf-8"))


# --- the expected path ---------------------------------------------------------------------------------------------

def test_expected_points_interpolate_to_half_hour_boundaries():
    pts = v2history.expected_points(path_from(t(0, 7), step=15, base=50, slope=1.0), DAY, LON)
    assert pts[0][0] == "2026-10-07T00:30:00+01:00"            # 00:00 is before the path starts: not there
    assert pts[0][1] == round(50 + (23 / 15), 1)               # 23 minutes after the start, 1 point per 15 min
    assert pts[-1][0] == "2026-10-08T00:00:00+01:00"
    assert len(pts) == 48
    assert v2history.expected_points(None, DAY, LON) == []
    assert v2history.expected_points(path_from(t(0), n=4), DAY, LON)[-1][0] == "2026-10-07T00:30:00+01:00"  # path ends


def test_a_clock_change_day_has_fifty_half_hours():
    assert len(v2history.half_hours(date(2026, 10, 25), LON)) == 50
    assert len(v2history.half_hours(date(2026, 3, 29), LON)) == 46


# --- recording -----------------------------------------------------------------------------------------------------

def run_ticks(h, start, minutes, step_s=60, **kw):
    out = []
    for i in range(int(minutes * 60 / step_s) + 1):
        out.append(h.tick(start + timedelta(seconds=i * step_s), **kw))
    return out


def test_half_hours_are_recorded_as_each_one_ends(tmp_path):
    h = V2History(str(tmp_path), LON)
    pth = path_from(t(0, 0, 5))
    # 00:00-00:30 self use (preview), 00:30-01:00 mostly charge, sent
    run_ticks(h, t(0, 0, 5), 29, mode="self_use", why="cheap rate soon", level=50.4, import_p=30.28, export_p=15.0,
              value_p=9.5, sent=False, preview=True, path=pth)
    assert read(tmp_path / "2026-10-07.json")["ran"] == []                 # not before the half-hour ends
    run_ticks(h, t(0, 30, 5), 8, mode="self_use", level=51.0, import_p=6.99, export_p=15.0, value_p=9.6, sent=True,
              path=pth)
    run_ticks(h, t(0, 38, 5), 20, mode="grid_charge", why="cheap", level=55.5, import_p=6.99, export_p=15.0,
              value_p=10.12, sent=True, path=pth)
    data = read(tmp_path / "2026-10-07.json")
    assert len(data["ran"]) == 1 and data["ran"][0]["start"] == "2026-10-07T00:00:00+01:00"
    first = data["ran"][0]
    assert first == {"start": "2026-10-07T00:00:00+01:00", "mode": "self_use", "level_end": 50.4, "import_p": 30.28,
                     "export_p": 15.0, "value_p": 9.5, "sent": False, "preview": True}
    run_ticks(h, t(0, 58, 5), 3, mode="grid_charge", level=56.0, import_p=6.99, export_p=15.0, value_p=10.2, sent=True,
              path=pth)
    second = read(tmp_path / "2026-10-07.json")["ran"][1]
    assert second["start"] == "2026-10-07T00:30:00+01:00" and second["mode"] == "grid_charge"   # most of the half-hour
    assert second["sent"] is True and second["preview"] is False and second["level_end"] == 56.0
    assert [c["mode"] for c in read(tmp_path / "2026-10-07.json")["changes"]] == ["self_use", "grid_charge"]


def test_expected_is_saved_from_the_first_timeline_and_each_hours_first(tmp_path):
    h = V2History(str(tmp_path), LON)
    first, later = path_from(t(0, 0, 5), base=50), path_from(t(1, 0, 0), base=60)
    h.tick(t(0, 0, 5), mode="hold", path=first)
    h.tick(t(0, 30, 5), mode="hold", path=later)                           # same hour: nothing new
    h.tick(t(1, 0, 20), mode="hold", path=later)
    h.tick(t(2, 10), mode="hold", path=None)                               # no timeline: no entry for that hour
    exp = read(tmp_path / "2026-10-07.json")["expected"]
    assert exp["made_at"] == "2026-10-07T00:00:05+01:00"
    assert exp["day"][0][1] == pytest.approx(50.2, abs=0.05)                # from the first timeline, 00:30
    assert set(exp["hours"]) == {"00:00", "01:00"}
    assert exp["hours"]["01:00"][0][0] == "2026-10-07T01:00:00+01:00"      # the later path starts at 01:00
    assert exp["hours"]["01:00"][0][1] == 60.0


def test_mode_changes_are_listed_capped_and_not_repeated_after_a_restart(tmp_path):
    h = V2History(str(tmp_path), LON)
    h.tick(t(0, 0, 5), mode="hold", why="a")
    h.tick(t(0, 1), mode="hold", why="a")
    h.tick(t(0, 2), mode="export", why="b")
    h2 = V2History(str(tmp_path), LON)                                     # AppDaemon restarted
    h2.tick(t(0, 3), mode="export", why="b")
    h2.tick(t(0, 4), mode="hold", why="c")
    ch = read(tmp_path / "2026-10-07.json")["changes"]
    assert [(c["at"][11:16], c["mode"]) for c in ch] == [("00:00", "hold"), ("00:02", "export"), ("00:04", "hold")]
    for i in range(300):
        h2.tick(t(5, 0) + timedelta(seconds=10 * i), mode="hold" if i % 2 else "export")
    assert len(read(tmp_path / "2026-10-07.json")["changes"]) == v2history.CHANGES_CAP


def test_a_half_hour_ending_after_midnight_goes_in_its_own_day(tmp_path):
    h = V2History(str(tmp_path), LON)
    run_ticks(h, t(23, 40), 25, mode="self_use", level=40.0, import_p=7.0, export_p=15.0, value_p=9.0)
    assert [r["start"] for r in read(tmp_path / "2026-10-07.json")["ran"]] == ["2026-10-07T23:30:00+01:00"]
    assert [c["at"][:10] for c in read(tmp_path / "2026-10-08.json")["changes"]] == ["2026-10-08"]   # the day's mode


def test_the_attributes_pair_ran_with_expected_and_flag_previews(tmp_path):
    h = V2History(str(tmp_path), LON)
    pth = path_from(t(0, 0, 5), base=50, slope=0.4)
    run_ticks(h, t(0, 0, 5), 61, mode="self_use", level=50.5, import_p=7.0, export_p=15.0, value_p=9.0, sent=False,
              preview=True, path=pth)
    a = h.attributes(DAY, DAY, "v1", True)
    assert a["date"] == "2026-10-07" and a["latest"] == "2026-10-07" and a["earliest"] == "2026-10-07"
    assert a["in_control"] == "v1" and a["live"] is True and a["preview_only"] is True and a["note"]
    rows = {r["t"]: r for r in a["series"]}
    r0 = rows["2026-10-07T00:00:00+01:00"]
    assert r0["mode"] == "self_use" and r0["level"] == 50.5 and r0["preview"] is True and r0["sent"] is False
    assert r0["expected"] == pytest.approx(50 + 0.4 * (30 * 60 - 5) / 900, abs=0.1)     # at the half-hour's end
    ahead = rows["2026-10-07T05:00:00+01:00"]                                        # not run yet: expected only
    assert ahead["level"] is None and ahead["mode"] is None and ahead["expected"] is not None
    assert a["changes"][0]["mode"] == "self_use"
    assert h.attributes(date(2026, 9, 1), DAY, None)["series"] == []                   # a day with no file


def test_a_half_hour_in_a_smart_slot_is_flagged_so_the_chart_can_draw_its_price_dashed(tmp_path):
    h = V2History(str(tmp_path), LON)
    run_ticks(h, t(0, 0, 5), 61, mode="grid_charge", level=50.5, import_p=7.0, export_p=15.0, value_p=9.0,
              sent=True, slot=True)
    run_ticks(h, t(1, 0, 5), 61, mode="self_use", level=50.5, import_p=30.0, export_p=15.0, value_p=9.0, sent=True)
    rows = {r["t"]: r for r in h.attributes(DAY, DAY, "v2", True)["series"]}
    assert rows["2026-10-07T00:00:00+01:00"]["slot"] is True
    assert "slot" not in rows["2026-10-07T01:00:00+01:00"]
    recent = {r["t"]: r for r in h.recent_attributes(t(2, 10))["series"]}
    assert recent["2026-10-07T00:00:00+01:00"]["slot"] is True and "slot" not in recent["2026-10-07T01:00:00+01:00"]


def test_the_sensor_attributes_stay_under_15kb_on_the_busiest_day(tmp_path):
    h = V2History(str(tmp_path), LON)
    pth = path_from(t(0, 0, 5))
    h.tick(t(0, 0, 5), mode="hold", path=pth)
    for i in range(48 * 30):                                                       # every minute for a day
        now = t(0, 0, 5) + timedelta(minutes=i)
        if now.date() != DAY:
            break
        h.tick(now, mode=("export", "grid_charge", "hold", "self_use")[(i // 7) % 4],
               why="Selling at 15.0p: the stored kWh is worth only 9.55p, so selling now beats keeping it " * 3,
               level=40 + (i % 40) * 0.37, import_p=30.28, export_p=15.0, value_p=9.55, sent=True, path=pth)
    data = read(tmp_path / "2026-10-07.json")
    assert len(data["changes"]) == v2history.CHANGES_CAP and len(data["ran"]) >= 46
    a = h.attributes(DAY, DAY, "v2", True, )
    size = len(json.dumps(a, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    assert size <= v2history.ATTR_LIMIT, size                  # the runs may go past the 12 KB target
    assert len(a["series"]) == 48 and a["changes"]                                   # the whole day, the latest changes
    assert a["changes"][-1]["at"] == data["changes"][-1]["at"]


def test_retention_and_summary(tmp_path):
    h = V2History(str(tmp_path), LON)
    for d in ("2026-08-01", "2026-09-20", "2026-10-06"):
        (tmp_path / f"{d}.json").write_text(json.dumps({"version": 1, "ran": [{"sent": True}], "changes": [1],
                                                        "expected": {"day": [[1, 2]]}}))
    assert h.prune(date(2026, 10, 7)) == 1 and h.days() == ["2026-09-20", "2026-10-06"]
    s = v2history.summary(str(tmp_path), LON)
    assert s["days_kept"] == 2 and s["days"][-1] == {"day": "2026-10-06", "half_hours": 1, "changes": 1, "sent": 1,
                                                     "preview": 0, "has_expected": True,
                                                     "bytes": s["days"][-1]["bytes"]}


def test_a_broken_file_starts_a_fresh_day(tmp_path):
    (tmp_path / "2026-10-07.json").write_text("{not json")
    h = V2History(str(tmp_path), LON)
    h.tick(t(1, 0), mode="hold")
    assert read(tmp_path / "2026-10-07.json")["changes"][0]["mode"] == "hold"
    assert not list(tmp_path.glob("*.tmp"))


# --- plan history label ---------------------------------------------------------------------------------------------

def test_chosen_plan_says_engine_v1_was_passive_when_v2_ran():
    sod, ran = {"slots": [1]}, {"slots": [2]}
    assert chosen_plan(sod, ran, "v2") == (PASSIVE_LABEL, sod)
    assert PASSIVE_LABEL == "Start of day (engine v1 was passive)"
    assert chosen_plan(None, ran, "v2") == (None, None)
    assert chosen_plan(sod, ran, "v1") == ("As run", ran) and chosen_plan(sod, ran, "mixed") == ("As run", ran)
    assert chosen_plan(sod, ran) == ("As run", ran)


# --- the engine tag on cost records --------------------------------------------------------------------------------

def test_records_carry_engine_and_live_and_survive_a_revalue(tmp_path):
    rates = day_rates(T0) + day_rates(T0 + timedelta(days=1))
    book = CostBook(str(tmp_path / "b"), timezone.utc)
    rec = Recorder()
    for i in range(61 * 2):
        r = R(T0 + timedelta(hours=1, seconds=30 * i), grid_power=1000, battery_power=0, house_power=1000,
              import_rate=CHEAP, rates=rates, battery_soc=50)
        hh = rec.add(r)
        if hh:
            out = book.add(hh, r, capacity=18, eff=0.95, floor_soc=12, max_kw=4.8, includes_ev=True,
                           engine="v2", live=False)
            assert out["engine"] == "v2" and out["live"] is False
            untagged = book.add(hh, r, capacity=18, eff=0.95, floor_soc=12, max_kw=4.8, includes_ev=True,
                                keep_existing=True)                        # a backfill: no tag, nothing to replace
            assert untagged is None
    book.revalue(capacity=18, eff=0.95, floor_soc=12, max_kw=4.8, includes_ev=True)
    recs = book.day_records(T0.date())
    assert recs and all(x["engine"] == "v2" and x["live"] is False for x in recs)


def put(book, day, rows):
    _write_json(book._day_path(day), [{"start": f"{day.isoformat()}T{i // 2:02d}:{30 * (i % 2):02d}:00+00:00", **r}
                                      for i, r in enumerate(rows)])


def test_day_engine(tmp_path):
    book = CostBook(str(tmp_path), timezone.utc)
    d = date(2026, 10, 1)
    assert book.day_engine(d) is None and book.day_engine_info(d) is None
    put(book, d, [{"engine": "v1", "live": True}] * 3)
    assert book.day_engine(d) == "v1" and book.day_engine_info(d) == {"engine": "v1", "live": True}
    d = date(2026, 10, 2)
    put(book, d, [{"engine": "v2", "live": True}] * 3)
    assert book.day_engine(d) == "v2"
    d = date(2026, 10, 3)                                                  # switched during the day
    put(book, d, [{"engine": "v1", "live": True}] * 2 + [{"engine": "v2", "live": True}] * 4)
    assert book.day_engine(d) == "mixed"
    d = date(2026, 10, 4)                                                  # v2 chosen but passive all day
    put(book, d, [{"engine": "v2", "live": False}] * 3)
    assert book.day_engine_info(d) == {"engine": "v2", "live": False}
    d = date(2026, 10, 5)                                                  # passive v1 hours don't count against v2
    put(book, d, [{"engine": "v1", "live": False}] * 2 + [{"engine": "v2", "live": True}] * 4)
    assert book.day_engine_info(d) == {"engine": "v2", "live": True}
    d = date(2026, 10, 6)                                                  # old records: no keys, count as v1
    put(book, d, [{}] * 4)
    assert book.day_engine_info(d) == {"engine": "v1", "live": True}
    d = date(2026, 10, 7)                                                  # a backfill from history is untagged
    put(book, d, [{"engine": "v2", "live": True}] * 3 + [{"source": "history"}] * 2)
    assert book.day_engine(d) == "v2"


# --- the last hours as run (sensor.pe_v2_recent) -----------------------------------------------------------------

def _ran(start, mode="self_use", level=50.0, **extra):
    row = {"start": start.isoformat(timespec="seconds"), "mode": mode, "level_end": level, "import_p": 7.0,
           "export_p": 15.0, "value_p": 9.0, "sent": True, "preview": False}
    row.update(extra)
    return row


def test_recent_crosses_midnight_and_leaves_out_the_half_hour_now(tmp_path):
    h = V2History(str(tmp_path), LON)
    yesterday = h.load(date(2026, 10, 6))
    today = h.load(date(2026, 10, 7))
    yesterday["ran"] = [_ran(t(h_, m, day=6), level=40.0 + h_) for h_ in range(0, 24) for m in (0, 30)]
    today["ran"] = [_ran(t(h_, m), level=60.0 + h_) for h_ in range(0, 9) for m in (0, 30)]
    now = t(8, 20)                                               # the 08:00 half-hour is still running
    got = h.recent_attributes(now, 18)
    starts = [r["t"] for r in got["series"]]
    assert starts[0] == t(14, 0, day=6).isoformat() and starts[-1] == t(7, 30).isoformat()
    assert len(starts) == 36 and starts == sorted(starts)
    assert got["hours"] == 18 and got["step_min"] == 30 and got["until"] == t(8, 0).isoformat()
    row = got["series"][-1]
    assert row == {"t": t(7, 30).isoformat(), "mode": "self_use", "level": 67.0, "import_p": 7.0, "export_p": 15.0,
                   "value_p": 9.0, "sent": True}
    assert len(json.dumps(got, separators=(",", ":")).encode("utf-8")) < 6_000


def test_recent_marks_previews_and_is_empty_without_records(tmp_path):
    h = V2History(str(tmp_path), LON)
    assert h.recent_attributes(t(12), 18)["series"] == []
    h.load(date(2026, 10, 7))["ran"] = [_ran(t(10), sent=False, preview=True), _ran(t(10, 30))]
    got = h.recent_attributes(t(12), 18)["series"]
    assert got[0]["preview"] is True and got[0]["sent"] is False and "preview" not in got[1]


def test_day_engine_names_the_settings_profile_engine_v2_ran(tmp_path):
    book = CostBook(str(tmp_path), timezone.utc)
    d = date(2026, 10, 6)
    put(book, d, [{"engine": "v2", "live": True, "profile": "2.1 beta"}] * 4)
    assert book.day_engine_info(d) == {"engine": "v2", "live": True, "profile": "2.1 beta"}
    d = date(2026, 10, 7)                                                  # the profile changed during the day
    put(book, d, [{"engine": "v2", "live": True, "profile": "defaults"}] * 2
        + [{"engine": "v2", "live": True, "profile": "2.1 beta"}] * 2)
    assert book.day_engine_info(d) == {"engine": "v2", "live": True, "profile": "mixed"}
    d = date(2026, 10, 8)                                                  # v1 and old v2 records: none
    put(book, d, [{"engine": "v1", "live": True}] * 2 + [{"engine": "v2", "live": True}] * 2)
    assert "profile" not in book.day_engine_info(d)


def test_the_comparison_names_the_profile_the_day_ran_on():
    from pe_core.compare.day import day_profile
    from pe_core.compare.sensor import day_row
    live = {"engine": "v2", "live": True, "profile": "2.1 beta"}
    assert day_profile([live, live]) == "2.1 beta"
    assert day_profile([live, {**live, "profile": "defaults"}]) == "mixed"
    assert day_profile([{"engine": "v1", "live": True}, {**live, "live": False}]) is None
    ok = {"status": "ok", "in_control": "v2", "live": True, "v1": {}, "v2": {}, "profile": "2.1 beta"}
    assert day_row(__import__("datetime").date(2026, 10, 9), ok, True)["profile"] == "2.1 beta"
    assert "profile" not in day_row(__import__("datetime").date(2026, 10, 9), {**ok, "profile": None}, True)


def test_runs_are_the_day_as_it_ran_to_the_second(tmp_path):
    h = V2History(str(tmp_path), LON)
    pth = path_from(t(0, 0, 5))
    start = t(10, 0, 0)
    steps = (("charge", 50.0), ("charge", 55.0), ("export", 60.2), ("export", 55.0), ("self_use", 50.4))
    for i, (mode, level) in enumerate(steps):
        h.tick(start + timedelta(minutes=7 * i + 1), mode=mode, level=level, sent=i != 4, preview=i == 4, path=pth)
    a = h.attributes(DAY, DAY, "v2", True)
    assert [r[1] for r in a["runs"]] == ["charge", "export", "self_use"]
    assert a["runs"][0][0] == (start + timedelta(minutes=1)).astimezone(LON).isoformat(timespec="seconds")
    assert a["runs"][1][2] == 60.2 and a["runs"][2][3] == 1 and a["runs"][0][3] == 0     # level at the start, preview
    assert a["runs_until"] is None                                                # today's: the card's clock
    rec = h.recent_attributes(start + timedelta(minutes=30))
    assert [r[1] for r in rec["runs"]] == ["charge", "export", "self_use"] and rec["runs_until"] is None
    later = h.recent_attributes(start + timedelta(hours=3), hours=1)  # the run going on then is first
    assert [r[1] for r in later["runs"]] == ["self_use"]


def test_a_half_hour_keeps_lines_grid_and_car_energy_and_the_day_its_replans(tmp_path):
    h = V2History(str(tmp_path), LON)
    for i in range(0, 1800, 10):             # 30 min in 10 s ticks: 2 kW in, then 1 kW out, car 7 kW
        now = t(8, 0) + timedelta(seconds=i)
        h.tick(now, mode="grid_charge", level=50.0, import_p=7.0, export_p=15.0, value_p=9.0, buy_line_p=7.4,
               sell_line_p=14.2, grid_w=2000 if i < 900 else -1000, car_w=7000, replanned=(i == 600))
    h.tick(t(8, 30), mode="grid_charge", level=51.0)             # closes the half-hour
    rec = h.load(date(2026, 10, 7))["ran"][0]
    assert rec["buy_line_p"] == 7.4 and rec["sell_line_p"] == 14.2
    assert abs(rec["grid_in_kwh"] - 0.5) < 0.01 and abs(rec["grid_out_kwh"] - 0.25) < 0.01
    assert abs(rec["car_kwh"] - 3.5) < 0.05
    attrs = h.attributes(date(2026, 10, 7), date(2026, 10, 7), "v2")
    row = attrs["series"][0]
    assert row["buy_p"] == 7.4 and row["sell_p"] == 14.2 and abs(row["car"] - 3.5) < 0.05
    assert abs(row["grid_in"] - 0.5) < 0.03 and abs(row["grid_out"] - 0.25) < 0.03
    assert attrs["replans"] == [t(8, 10).isoformat()]
    rec_attrs = h.recent_attributes(t(9, 0), 18)
    assert rec_attrs["replans"] == [t(8, 10).isoformat()] and rec_attrs["series"][0]["value_p"] == 9.0
    assert rec_attrs["series"][0]["buy_p"] == 7.4


def test_an_older_record_without_the_new_figures_publishes_without_them(tmp_path):
    h = V2History(str(tmp_path), LON)
    h.load(date(2026, 10, 7))["ran"] = [_ran(t(7, 30))]
    row = h.attributes(date(2026, 10, 7), date(2026, 10, 7), "v2")["series"][0]
    assert "buy_p" not in row and "car" not in row
    assert h.attributes(date(2026, 10, 7), date(2026, 10, 7), "v2")["replans"] == []


# --- real timing of the grid and car (v2flows) ------------------------------------------------------------------

def _reading(minute, grid_w, car_w=None, hour=0):
    from datetime import datetime, timezone

    from pe_core.readings import Readings
    return Readings(now=datetime(2026, 10, 3, hour, minute, tzinfo=timezone.utc), grid_power=grid_w, ev_power=car_w)


def test_real_flows_say_when_the_grid_and_car_moved():
    from datetime import date
    from zoneinfo import ZoneInfo

    from pe_core.v2flows import real_flows
    london = ZoneInfo("Europe/London")                         # BST: midnight local is 23:00 UTC the day before
    samples = ([_reading(m, 3000, 2000, hour=1) for m in (0, 1, 2, 3, 4)]       # 01:00 UTC: importing 3 kW, car 2 kW
               + [_reading(m, -4000, hour=1) for m in (5, 6, 7, 8, 9)]           # then exporting 4 kW
               + [_reading(m, 100, hour=1) for m in (10, 11)]                    # then 0.1 kW: kept (rounds to 0.05)
               + [_reading(m, 0, hour=1) for m in (15, 16)]                      # quiet: left out
               + [_reading(m, None, hour=1) for m in (20, 21)])                  # no grid figure: skipped
    f = real_flows(samples, london, date(2026, 10, 3))
    assert f["date"] == "2026-10-03" and f["bucket"] == 5 and f["start"] == "2026-10-03T00:00:00+01:00"
    assert f["m"] == [120, 125, 130]                           # 02:00 local is 120 minutes after midnight
    assert f["imp"] == [1.5, 0.0, 0.05] and f["exp"] == [0.0, 2.0, 0.0] and f["car"] == [1.0, 0.0, 0.0]


def test_real_flows_with_nothing_read_is_none_and_all_quiet_is_empty_but_real():
    from datetime import date, timezone

    from pe_core.v2flows import real_flows
    assert real_flows([], timezone.utc, date(2026, 10, 3)) is None
    assert real_flows([_reading(0, None)], timezone.utc, date(2026, 10, 3)) is None
    quiet = real_flows([_reading(0, 0)], timezone.utc, date(2026, 10, 3))
    assert quiet["m"] == [] and quiet["imp"] == []


def test_real_flows_also_carry_the_sun_and_the_house_when_they_were_read():
    from datetime import date, timezone

    from pe_core.v2flows import real_flows
    a = _reading(0, 200, hour=1)
    a.solar_power, a.house_power = 3000.0, 800.0
    f = real_flows([a], timezone.utc, date(2026, 10, 3))
    assert f["sun"] == [1.5] and f["house"] == [0.4]
    assert "sun" not in real_flows([_reading(0, 200, hour=1)], timezone.utc, date(2026, 10, 3))
    dark = _reading(0, 0, hour=1)
    dark.solar_power, dark.house_power = 0.0, 0.0
    assert real_flows([dark], timezone.utc, date(2026, 10, 3))["m"] == []        # nothing moving: still left out
