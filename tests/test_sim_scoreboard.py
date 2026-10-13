"""The simulator's plan-following metrics (tools/sim/scoreboard.py), on a hand-made day."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "sim"))

import scoreboard  # noqa: E402

TZ = "+01:00"


def _item(mode, a, b, l0, l1):
    return [mode, f"2026-10-08T{a}{TZ}", f"2026-10-08T{b}{TZ}", l0, l1, "", ""]


def day(changes, trace, timeline):
    return {
        "status": "ok",
        "day": "2026-10-08",
        "series": {
            "tz": "Europe/London",
            "changes": changes,
            "trace": [[t, lvl, m, 0, 0] for t, lvl, m in trace],
            "plans": [{"at": f"2026-10-08T00:00:00{TZ}", "timeline": timeline}],
        },
    }


def test_a_charge_that_overshoots_then_holds_at_its_target():
    r = day(
        changes=[["00:00:00", "charge"], ["00:30:00", "hold"], ["01:00:00", "self"]],
        trace=[("00:00", 50, "charge"), ("00:30", 81.5, "charge"), ("00:35", 81.5, "hold"), ("01:00", 81.5, "self")],
        timeline=[_item("charge", "00:00", "00:30", 50, 80), _item("hold", "00:30", "01:00", 80, 80)],
    )
    m = scoreboard.run_metrics(r)
    assert m["holds"] == 1 and m["runs"] == 1 and m["cut_early"] == 0
    assert m["overshoot_mean"] == 1.5 and m["undershoot_mean"] is None
    assert m["hold_at_target_min"] == 0  # 81.5 is 1.5 from the 80 target: not "reached"


def test_a_run_cut_short_is_counted_not_scored_and_hold_at_target_is_timed():
    r = day(
        changes=[["00:00:00", "charge"], ["00:10:00", "hold"], ["00:40:00", "self"]],
        trace=[("00:00", 50, "charge"), ("00:10", 60, "charge"), ("00:15", 60, "hold"), ("00:40", 60, "self")],
        timeline=[_item("charge", "00:00", "00:30", 50, 80)],
    )
    m = scoreboard.run_metrics(r)
    assert m["cut_early"] == 1 and m["runs"] == 0
    assert scoreboard.run_metrics({"status": "failed"}) is None
    assert "plan following" in scoreboard.metrics_table([r])


def _rows(*specs):
    """specs: (start, import p/kWh, car kWh) per half hour."""
    return [{"start": f"2026-10-08T{t}:00{TZ}", "act": a, "exp": 0.15, "car": car} for t, a, car in specs]


def _held_at_target(rows):
    r = day(
        changes=[["00:00:00", "charge"], ["00:20:00", "hold"], ["01:10:00", "self"]],
        trace=[("00:00", 50, "charge"), ("00:20", 80, "charge"), ("00:25", 80, "hold"), ("01:10", 80, "self")],
        timeline=[_item("charge", "00:00", "00:30", 50, 80)],
    )
    r["series"]["rows"] = rows
    return scoreboard.run_metrics(r)


def test_a_hold_at_target_is_split_into_waiting_for_a_price_car_and_idle():
    flat = [("00:00", 0.07, 0), ("00:30", 0.07, 0), ("01:00", 0.07, 0)]
    m = _held_at_target(_rows(*flat))
    assert (m["hold_at_target_min"], m["hold_wait_min"], m["hold_car_min"], m["hold_idle_min"]) == (50, 0, 0, 50)
    m = _held_at_target(_rows(("00:00", 0.07, 0), ("00:30", 0.07, 0), ("01:00", 0.30, 0)))  # the price rises at 01:00
    assert (m["hold_wait_min"], m["hold_car_min"], m["hold_idle_min"]) == (50, 0, 0)
    m = _held_at_target(_rows(("00:00", 0.07, 0), ("00:30", 0.07, 2.0), ("01:00", 0.07, 2.0)))  # the car charges
    assert (m["hold_wait_min"], m["hold_car_min"], m["hold_idle_min"]) == (0, 50, 0)
