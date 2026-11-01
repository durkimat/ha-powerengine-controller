from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pe_core.compare import sensor, why

LONDON = ZoneInfo("Europe/London")


def _halves(costs, events=()):
    """48 half-hours of 29 Oct (BST), each with `costs[i]` (default 0) and in an event if i is in `events`."""
    out = []
    for i in range(48):
        start = datetime(2026, 10, 29, i // 2, i % 2 * 30, tzinfo=LONDON).astimezone(timezone.utc)
        out.append((start.isoformat(), costs.get(i, 0.0), i in events))
    return out


def _run(costs, events=(), **kw):
    return {"halves": _halves(costs, events), "adj_cost_gbp": sum(costs.values()) - kw.pop("endval", 0.0),
            "endval_gbp": kw.get("endval_gbp", 0.0), **kw}


def test_the_gap_is_split_by_part_of_the_day_and_the_event():
    v2 = _run({40: 0.5, 38: 0.1, 36: -1.0}, events={36, 37}, end_soc=45.0, event_kwh=3.0, endval_gbp=0.2)
    bound = _run({40: 0.2, 38: 0.1, 36: -3.0}, events={36, 37}, end_soc=30.0, event_kwh=6.0, endval_gbp=0.1)
    v2["adj_cost_gbp"] = 0.5 + 0.1 - 1.0 - 0.2
    bound["adj_cost_gbp"] = 0.2 + 0.1 - 3.0 - 0.1
    w = why.build(v2, bound, LONDON, real_event_kwh=3.5)
    parts = {p["key"]: p for p in w["parts"]}
    assert parts["event"]["gap"] == 2.0 and parts["evening"]["gap"] == 0.3 and parts["night"]["gap"] == 0
    assert w["end"] == -0.1                                   # v2 ended with more charge: worth 10p more
    assert round(w["gap"], 2) == round(2.0 + 0.3 - 0.1, 2)   # the parts and the end level add up to the gap
    text = " ".join(w["lines"])
    assert "£2.20 less than the best possible" in text and "grid event £2.00" in text
    assert "v2 exported 3.0 kWh, the best possible 6.0 kWh (the real system sent 3.5 kWh)" in text


def test_a_close_day_says_so_and_no_halves_gives_nothing():
    run = _run({10: 0.5}, end_soc=40.0)
    w = why.build(run, run, LONDON)
    assert w["gap"] == 0 and w["lines"] == ["Engine v2 was within 5p of the best possible this day."]
    assert why.build({}, run, LONDON) is None


def test_the_sensor_row_carries_the_why():
    res = {"day": "2026-10-29", "status": "ok", "in_control": "v2", "live": True, "v1": {"saving": 1.0},
           "v2": {"saving": 1.5}, "bound": {"saving": 2.0}, "why": {"gap": 0.5, "parts": [], "lines": ["x"]}}
    row = sensor.day_row(datetime(2026, 10, 29).date(), res, True)
    assert row["why"]["lines"] == ["x"]
