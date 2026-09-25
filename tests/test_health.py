from datetime import datetime, timedelta, timezone

from pe_core.health import accuracy, data_findings, input_findings, overall

T0 = datetime(2026, 9, 22, tzinfo=timezone.utc)


def day(**over):
    recs = []
    for i in range(48):
        r = {"start": (T0 + timedelta(minutes=30 * i)).isoformat(), "seconds": 1800, "solar": 0.2, "grid_import": 0.4,
             "battery_out": 0.1, "battery_in": 0.1, "house": 0.5, "car": 0.0, "grid_export": 0.08,
             "soc_end": 50 + (i % 10) * 4, "v": {"correction_kwh": 0.0}}
        r.update(over)
        recs.append(r)
    return recs


def test_a_normal_day_is_clean():
    assert data_findings(day(), "Tue") == []


def test_unsigned_battery_sensor_is_a_problem():
    f = data_findings(day(battery_in=0.0, battery_out=0.3), "Tue")
    assert any(x["level"] == "problem" and "charging" in x["title"] for x in f)
    assert overall(f) == "problems"


def test_energy_that_goes_nowhere_is_flagged():
    f = data_findings(day(house=0.3), "Tue")                  # 0.2 kWh a half-hour unaccounted (~28%)
    assert any("unaccounted" in x["title"] for x in f)


def test_gaps_and_ledger_corrections_are_flagged():
    f = data_findings(day(seconds=1200, v={"correction_kwh": 0.1}), "Tue")
    titles = " ".join(x["title"] for x in f)
    assert "of the day recorded" in titles and "ledger corrected" in titles


def test_input_findings_ignore_ok_and_unmapped():
    f = input_findings({"a": {"status": "ok"}, "b": {"status": "unmapped"}, "c": {"status": "stale", "message": "m"},
                        "d": {"status": "missing", "message": "gone"}})
    assert [(x["level"], x["title"]) for x in f] == [("warning", "Input c: stale"), ("problem", "Input d: missing")]


def test_accuracy_against_the_days_first_plan():
    recs = day()
    snap = {"made_at": "x", "slots": [{"start": r["start"], "soc": r["soc_end"] - 5, "load_kwh": 0.4,
                                       "solar_kwh": 0.25} for r in recs]}
    a = accuracy(recs, snap)
    assert a["half_hours"] == 48 and a["load_actual"] == 24.0 and a["load_forecast"] == 19.2
    assert abs(a["load_mae"] - 0.1) < 1e-9 and a["soc_mean_error"] == 5.0
    assert accuracy(recs, None) is None
