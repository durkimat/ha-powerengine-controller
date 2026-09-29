"""The demo data pack (demo plan C1): the builder, its scrub rule, and the pure pack module.

Synthetic day files only: no private data in tests."""
import importlib.util
import json
import pathlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pe_core.demo import pack as dp

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_demo_pack", ROOT / "tools" / "build_demo_pack.py")
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)

LONDON = ZoneInfo("Europe/London")


def record(day, i, *, solar=0.0, car=0.0, axle=False, export=0.0, slot=False, seconds=1800.0):
    """One synthetic half-hour; `day` is a local BST date (UTC = local - 1 h)."""
    start = datetime(day.year, day.month, day.day, tzinfo=LONDON) + timedelta(minutes=30 * i)
    return {
        "start": start.astimezone(timezone.utc).isoformat(), "house": 0.3, "car": car, "solar": solar,
        "grid_import": 0.1,
        "grid_export": export, "battery_in": 0.0, "battery_out": 0.0, "soc_start": 50.0, "soc_end": 50.0,
        "seconds": seconds, "axle": axle, "free": False, "import_rate": 0.3, "export_rate": 0.15, "standing": 0.5,
        "fv": "2-80802d68", "source": "history",
        "v": {"act": 0.3, "std": 0.3, "ovn": 0.07, "exp": 0.15, "slot": slot, "event": "axle" if axle else None,
              "event_kwh": export if axle else 0.0},
    }


def day_records(day, *, solar=0.0, car=0.0, axle_kwh=0.0, slots=(), n=48):
    recs = []
    for i in range(n):
        recs.append(record(day, i, solar=solar / 48 if 12 <= i < 36 else 0.0, car=car / 48,
                           axle=bool(axle_kwh) and i in (34, 35), export=axle_kwh / 2 if i in (34, 35) else 0.0,
                           slot=i in slots))
    return recs


def write_days(tmp_path, spec_by_day):
    files = []
    for d, kw in spec_by_day.items():
        f = tmp_path / f"{d.isoformat()}.json"
        f.write_text(json.dumps(day_records(d, **kw)))
        files.append(str(f))
    return files


D = [date(2026, 9, 10 + i) for i in range(1, 7)]


# ---- selection -----------------------------------------------------------------------------------------------------

def test_selection_rules_pick_the_four_by_their_measure(tmp_path):
    files = write_days(tmp_path, {
        D[0]: {"solar": 20}, D[1]: {"solar": 2}, D[2]: {"solar": 8, "axle_kwh": 5}, D[3]: {"solar": 9, "car": 12},
        D[4]: {"solar": 10}})
    pack = bp.build(files, log=lambda *_: None)
    assert list(pack["days"]) == ["sunny", "dull", "axle", "car"]
    rec = {k: v["recorded"] for k, v in pack["days"].items()}
    assert rec == {"sunny": "2026-09-11", "dull": "2026-09-12", "axle": "2026-09-13", "car": "2026-09-14"}


def test_a_day_that_wins_two_rules_gives_the_later_rule_its_next_best():
    stats = {
        "a": {"solar": 20, "axle_kwh": 9, "car": 1},      # wins sunny and axle
        "b": {"solar": 1, "axle_kwh": 4, "car": 2},       # wins dull, next-best axle
        "c": {"solar": 5, "axle_kwh": 3, "car": 8},       # wins car
        "d": {"solar": 6, "axle_kwh": 0, "car": 3},
    }
    picks = bp.select_days(stats)
    assert picks == {"sunny": "a", "dull": "b", "axle": "c", "car": "d"}   # axle: a and b taken -> c; car: c taken -> d


def test_ties_go_to_the_earliest_date():
    stats = {f"2026-09-1{i}.json": {"solar": 10, "axle_kwh": 1, "car": 1} for i in range(1, 6)}
    picks = bp.select_days(stats)
    assert picks["sunny"] == "2026-09-11.json" and picks["dull"] == "2026-09-12.json"
    assert len(set(picks.values())) == 4


def test_too_few_days_is_an_error():
    with pytest.raises(ValueError):
        bp.select_days({"a": {"solar": 1, "axle_kwh": 0, "car": 0}})


def test_incomplete_days_are_skipped(tmp_path):
    good = write_days(tmp_path, {D[i]: {"solar": 5 + i} for i in range(5)})
    short = tmp_path / "2026-09-20.json"
    short.write_text(json.dumps(day_records(date(2026, 9, 20), solar=99, n=16)))
    thin = tmp_path / "2026-09-21.json"
    thin.write_text(json.dumps([{**r, "seconds": 600.0} for r in day_records(date(2026, 9, 21), solar=99)]))
    dup = day_records(date(2026, 9, 22), solar=99)
    dup[5]["start"] = dup[4]["start"]
    (tmp_path / "2026-09-22.json").write_text(json.dumps(dup))
    said = []
    pack = bp.build(good + [str(short), str(thin), str(tmp_path / "2026-09-22.json")], log=said.append)
    assert "2026-09-20" not in " ".join(pack["days"][k]["recorded"] for k in pack["days"])
    assert all(pack["days"][k]["recorded"] < "2026-09-20" for k in pack["days"])
    assert len([s for s in said if s.startswith("skipped")]) == 3


# ---- scrub ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("edit", [
    lambda r: r.update(entity="sensor.solis_battery_soc"),
    lambda r: r.update(account="A-1234ABCD"),
    lambda r: r["v"].update(meter="1012345678901"),
    lambda r: r.update(source="serial-2107"),
    lambda r: r.update(fv="not-a-tag"),
    lambda r: r["v"].update(event="somebody"),
    lambda r: r.update(nested={"deep": ["mpan 123"]}),
])
def test_builder_refuses_any_string_outside_the_whitelist(tmp_path, edit):
    files = write_days(tmp_path, {D[i]: {"solar": 5 + i, "car": i, "axle_kwh": i % 2 * 3} for i in range(5)})
    recs = json.loads(pathlib.Path(files[2]).read_text())
    edit(recs[7])
    pathlib.Path(files[2]).write_text(json.dumps(recs))
    with pytest.raises(bp.ScrubError):
        bp.build(files, log=lambda *_: None)


def test_the_pack_holds_only_numbers_times_and_flags(tmp_path):
    files = write_days(tmp_path, {D[i]: {"solar": 5 + i, "car": i, "axle_kwh": i % 2 * 3} for i in range(5)})
    pack = bp.build(files, log=lambda *_: None)
    for day in pack["days"].values():
        for k, v in day.items():
            if k in ("title", "rule", "recorded"):
                continue
            flat = v if isinstance(v, list) else (list(v.values()) if isinstance(v, dict) else [v])
            for x in flat:
                assert not isinstance(x, str) and not (isinstance(x, list) and any(isinstance(y, str) for y in x))
    bp.scrub_pack(pack)
    pack["days"]["sunny"]["entity"] = "sensor.x"
    with pytest.raises(bp.ScrubError):
        bp.scrub_pack(pack)


def test_the_forecast_is_derived_and_within_ten_percent():
    solar = [0.0] * 12 + [0.2, 0.5, 0.8, 1.0, 1.1, 1.0, 0.9, 0.7, 0.4, 0.1] + [0.0] * 26
    for scale in bp.FORECAST_SCALE:
        f = bp.forecast(solar, scale)
        assert len(f) == 48
        assert abs(sum(f) / sum(solar) - scale) < 0.001 and 0.9 <= scale <= 1.1
    assert bp.forecast([0.0] * 48, 1.0) == [0.0] * 48


# ---- pure module: shifting, looping, merging -----------------------------------------------------------------------

def tiny_pack():
    col = lambda f: [f(i) for i in range(48)]  # noqa: E731
    day = {c: col(lambda i: float(i)) for c in dp.COLUMNS}
    day["slot"] = col(lambda i: 1 if i in (2, 3, 4, 10, 46, 47) else 0)
    day["axle"] = col(lambda i: 1 if i in (34, 35) else 0)
    day["free"] = col(lambda i: 0)
    return {"version": 1, "days": {"x": day}}


def test_day_at_shifts_to_the_date_in_a_normal_week():
    rows = dp.day_at(tiny_pack(), "x", date(2026, 9, 29), LONDON)
    assert len(rows) == 48
    assert rows[0]["start"] == datetime(2026, 9, 29, 0, 0, tzinfo=LONDON)
    assert rows[47]["end"] == datetime(2026, 9, 30, 0, 0, tzinfo=LONDON)
    assert rows[17]["house"] == 17.0 and rows[17]["start"].hour == 8 and rows[17]["start"].minute == 30
    assert all(r["start"].tzinfo is not None for r in rows)
    assert all(a["end"] == b["start"] for a, b in zip(rows, rows[1:], strict=False))


def test_spring_forward_skips_the_hour_that_does_not_exist():
    rows = dp.day_at(tiny_pack(), "x", date(2026, 3, 29), LONDON)          # 01:00 GMT -> 02:00 BST
    assert len(rows) == 46
    assert [r["index"] for r in rows][:5] == [0, 1, 4, 5, 6]
    starts = [r["start"].astimezone(timezone.utc) for r in rows]
    assert len(set(starts)) == 46
    assert all(b - a == timedelta(minutes=30) for a, b in zip(starts, starts[1:], strict=False))
    assert rows[2]["start"].hour == 2 and rows[2]["start"].utcoffset() == timedelta(hours=1)   # index 4: 02:00 BST
    assert rows[-1]["end"] == datetime(2026, 3, 30, 0, 0, tzinfo=LONDON)


def test_autumn_back_plays_the_repeated_hour_twice():
    rows = dp.day_at(tiny_pack(), "x", date(2026, 10, 25), LONDON)         # 02:00 BST -> 01:00 GMT
    assert len(rows) == 50
    starts = [r["start"].astimezone(timezone.utc) for r in rows]
    assert len(set(starts)) == 50
    assert all(b - a == timedelta(minutes=30) for a, b in zip(starts, starts[1:], strict=False))
    idx = [r["index"] for r in rows]
    assert idx[:8] == [0, 1, 2, 3, 2, 3, 4, 5]                            # 01:00 and 01:30 twice, in time order
    assert rows[2]["start"].utcoffset() == timedelta(hours=1) and rows[4]["start"].utcoffset() == timedelta(0)
    assert rows[-1]["end"] == datetime(2026, 10, 26, 0, 0, tzinfo=LONDON)


def test_a_bst_change_between_two_days_keeps_wall_clock_times():
    before = dp.day_at(tiny_pack(), "x", date(2026, 10, 24), LONDON)
    after = dp.day_at(tiny_pack(), "x", date(2026, 10, 26), LONDON)
    assert before[30]["start"].hour == after[30]["start"].hour == 15
    assert before[30]["start"].utcoffset() == timedelta(hours=1) and after[30]["start"].utcoffset() == timedelta(0)


def test_row_at_loops_daily_and_covers_the_moment():
    p = tiny_pack()
    a = dp.row_at(p, "x", datetime(2026, 9, 29, 8, 45, tzinfo=LONDON), LONDON)
    b = dp.row_at(p, "x", datetime(2027, 1, 3, 8, 59, 59, tzinfo=LONDON), LONDON)
    assert a["index"] == b["index"] == 17 and a["house"] == 17.0
    assert a["start"] == datetime(2026, 9, 29, 8, 30, tzinfo=LONDON)
    assert a["end"] == datetime(2026, 9, 29, 9, 0, tzinfo=LONDON)
    utc = datetime(2026, 9, 29, 7, 45, tzinfo=timezone.utc)                   # same moment, another zone
    assert dp.row_at(p, "x", utc, LONDON)["index"] == 17
    last = dp.row_at(p, "x", datetime(2026, 9, 29, 23, 59, tzinfo=LONDON), LONDON)
    assert last["index"] == 47 and last["end"] == datetime(2026, 9, 30, 0, 0, tzinfo=LONDON)
    assert dp.row_at(p, "x", datetime(2026, 9, 30, 0, 0, tzinfo=LONDON), LONDON)["index"] == 0


def test_row_at_on_the_repeated_hour_keeps_the_fold():
    first = datetime(2026, 10, 25, 0, 45, tzinfo=timezone.utc)                 # 01:45 BST
    second = datetime(2026, 10, 25, 1, 45, tzinfo=timezone.utc)                # 01:45 GMT
    r1, r2 = dp.row_at(tiny_pack(), "x", first, LONDON), dp.row_at(tiny_pack(), "x", second, LONDON)
    assert r1["index"] == r2["index"] == 3
    assert r1["start"].astimezone(timezone.utc) < r2["start"].astimezone(timezone.utc)
    assert r1["end"].astimezone(timezone.utc) == r2["start"].astimezone(timezone.utc) - timedelta(minutes=30)


def test_slots_and_events_merge_into_windows():
    rows = dp.day_at(tiny_pack(), "x", date(2026, 9, 29), LONDON)
    slots = dp.smart_slots(rows)
    t = lambda h, m=0, d=29: datetime(2026, 9, d, h, m, tzinfo=LONDON)  # noqa: E731
    assert slots == [(t(1), t(2, 30)), (t(5), t(5, 30)), (t(23), t(0, d=30))]
    assert dp.axle_events(rows) == [(t(17), t(18))]
    assert dp.smart_slots([]) == [] and dp.axle_events([]) == []


def test_slots_merge_across_the_repeated_hour():
    p = tiny_pack()
    p["days"]["x"]["slot"] = [1 if i in (1, 2, 3, 4) else 0 for i in range(48)]
    rows = dp.day_at(p, "x", date(2026, 10, 25), LONDON)
    wins = dp.smart_slots(rows)
    assert len(wins) == 1 and wins[0][0] == datetime(2026, 10, 25, 0, 30, tzinfo=LONDON)
    assert (wins[0][1].astimezone(timezone.utc) - wins[0][0].astimezone(timezone.utc)) == timedelta(hours=3)


# ---- the committed pack --------------------------------------------------------------------------------------------

def test_the_committed_pack_has_four_days_of_48_rows():
    pack = dp.load_pack()
    assert dp.DEFAULT_PATH.exists() and dp.DEFAULT_PATH.parent.parent == ROOT / "apps" / "powerengine"
    assert dp.days(pack) == ["sunny", "dull", "axle", "car"]
    assert pack["source"] == "recorded, scrubbed" and "derived" in pack["forecast"]
    for name in dp.days(pack):
        assert all(len(pack["days"][name][c]) == 48 for c in dp.COLUMNS)
        assert len(dp.day_at(pack, name, date(2026, 9, 29), LONDON)) == 48
    assert dp.DEFAULT_PATH.stat().st_size < 150 * 1024
    assert dp.smart_slots(dp.day_at(pack, "sunny", date(2026, 9, 29), LONDON))
    assert dp.axle_events(dp.day_at(pack, "axle", date(2026, 9, 29), LONDON))
    bp.scrub_pack(pack)


def test_load_pack_refuses_a_file_that_is_not_a_pack(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"version": 2, "days": {"x": {}}}))
    with pytest.raises(ValueError):
        dp.load_pack(f)
    f.write_text(json.dumps({"version": 1, "days": {"x": {"house": [1]}}}))
    with pytest.raises(ValueError):
        dp.load_pack(f)
