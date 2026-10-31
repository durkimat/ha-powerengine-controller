import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from pe_core import forecastlog as fl
from pe_core.engine_v2.types import Forecast, Segment, Spread

T0 = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
M = timedelta(minutes=1)


def segment(start, minutes=30, slot_prob=None, slot_p=None, import_p=28.84, sun=0.2, load=0.3):
    return Segment(start=start, end=start + minutes * M, import_p=import_p, export_p=15.0,
                   solar_kwh=Spread(sun * 0.5, sun, sun * 1.5), load_kwh=Spread(load * 0.8, load, load * 1.2),
                   slot_prob=slot_prob, slot_import_p=slot_p)


def value_result(slot_from=1, slot_to=6):
    segs = tuple(segment(T0 + n * 30 * M, slot_prob=0.9 if slot_from <= n < slot_to else None,
                         slot_p=6.66 if slot_from <= n < slot_to else None) for n in range(0, 12))
    path = {"start": T0.isoformat(), "step_min": 15, "mid": [80 - n for n in range(40)],
            "low": [78 - n for n in range(40)], "high": [82 - n for n in range(40)]}
    return SimpleNamespace(path=path, forecast=Forecast(made_at=T0, segments=segs), because="Smart slots changed")


def test_plan_row_keeps_levels_at_each_horizon_and_the_slots_counted_on():
    row = fl.plan_row(T0 + 5 * M, 80.0, value_result())
    assert row["k"] == "p" and row["lvl"] == 80.0
    assert row["h"]["60"] == [74.0, 76.0, 78.0] and set(row["h"]) == {"30", "60", "120", "240"}
    hs = [r[0] for r in row["sl"]]
    assert hs[0] == (T0 + 30 * M).isoformat(timespec="seconds") and len(hs) == 5     # half-hours 1..5 of the plan
    assert row["sl"][0][1:] == [0.9, 6.66, 28.84]
    assert len(row["su"]) == 8 and row["su"][0][1:] == [0.1, 0.2, 0.3]
    assert row["lo"][0][1:] == [0.24, 0.3, 0.36]


def test_a_segment_shorter_than_a_half_hour_is_shared_out_by_time():
    segs = (segment(T0, 15, sun=0.4), segment(T0 + 15 * M, 45, sun=0.9))
    vr = SimpleNamespace(path={"step_min": 15, "mid": [1] * 20, "low": [1] * 20, "high": [1] * 20},
                         forecast=Forecast(made_at=T0, segments=segs), because="x")
    row = fl.plan_row(T0 - 5 * M, 50.0, vr)                          # first half-hour counted is 12:00
    assert row["su"][0][2] == round(0.4 + 0.9 * 15 / 45, 3)
    assert row["su"][1][2] == round(0.9 * 30 / 45, 3)


def actuals(start, minutes, **kw):
    out = []
    for m in range(0, minutes, 5):
        row = {"k": "a", "t": (start + m * M).isoformat(timespec="seconds"), "lvl": 80.0, "imp": 28.84, "sol": 400,
               "hou": 600}
        row.update({k: (v(m) if callable(v) else v) for k, v in kw.items()})
        out.append(row)
    return out


def test_score_compares_the_level_the_slots_and_the_energy_with_what_happened():
    plan = fl.plan_row(T0 + 5 * M, 80.0, value_result())
    plan["t"] = T0.isoformat(timespec="seconds")
    rows = [plan] + actuals(T0, 300, lvl=lambda m: 80 - m / 15,
                            imp=lambda m: 6.66 if 30 <= m < 90 else 28.84)
    sc = fl.score(rows)
    two_h = sc["level"]["+120 min"]
    assert two_h["n"] == 1 and two_h["mean_abs_error"] == 0.0 and two_h["bias"] == 0.0
    lo = fl.score([dict(plan, h={"60": [60.0, 62.0, 64.0]})] + rows[1:])["level"]["+60 min"]
    assert lo["mean_abs_error"] == 14.0 and lo["bias"] == -14.0
    assert lo["below_low_pct"] == 0 and lo["above_high_pct"] == 100
    slots = sc["slots"]
    arrived = sum(d["n"] * d["arrived_pct"] / 100 for d in slots.values())
    assert sum(d["n"] for d in slots.values()) == 5 and round(arrived) == 2      # 12:30 and 13:00 had the slot price
    assert slots["next 30 min"]["assumed_pct"] == 90 and slots["next 30 min"]["arrived_pct"] == 100
    sun = sc["sun"]["0-1 h"]
    assert sun["n"] == 2 and sun["actual_over_expected"] == 1.0 and sun["inside_range_pct"] == 100
    assert sc["house"]["0-1 h"]["actual_over_expected"] == 1.0


def test_score_skips_what_has_not_happened_yet_or_has_no_readings():
    plan = fl.plan_row(T0 + 5 * M, 80.0, value_result())
    plan["t"] = T0.isoformat(timespec="seconds")
    sc = fl.score([plan] + actuals(T0, 40))                              # only 40 minutes of readings
    assert "+120 min" not in sc["level"] and "+30 min" in sc["level"]
    assert fl.score([plan])["level"] == {} and fl.score([plan])["slots"] == {}


def test_log_writes_plans_and_five_minute_actuals_and_prunes(tmp_path):
    log = fl.ForecastLog(str(tmp_path / "forecastlog"))
    rd = SimpleNamespace(battery_soc=81.0, import_rate=0.0666, solar_power=500, house_power=700)
    log.plan(T0, 80.0, value_result())
    assert log.actual(T0, rd) and not log.actual(T0 + M, rd) and log.actual(T0 + 5 * M, rd)
    assert not log.actual(T0 + 10 * M, SimpleNamespace(battery_soc=None))
    rows = log.rows(1, T0.date())
    assert [r["k"] for r in rows] == ["p", "a", "a"] and rows[1]["imp"] == 6.66
    (tmp_path / "forecastlog" / "2026-09-01.jsonl").write_text("{}\n")
    assert log.prune(date(2026, 10, 31)) == 1
    with open(tmp_path / "forecastlog" / "2026-10-31.jsonl", "a") as fh:
        fh.write('{"k":"a","t":')                                         # a half-written last line is ignored
    assert len(log.rows(1, T0.date())) == 3
    assert json.dumps(fl.summary(str(tmp_path / "forecastlog"), T0.date()))
