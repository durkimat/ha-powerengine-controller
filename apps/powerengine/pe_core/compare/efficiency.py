"""The round trip the replay's battery should have: what the owner's own days show, grid to grid.

The app plans with the battery's measured efficiency times the inverter's AC/DC conversion each way, learned from
the recorded half-hours (`pe_core/learn.py`): 76% grid to grid on 31 Oct 2026 (94% at the battery, 89% in and 90% out
through the inverter). The replay's world had only the config's figure (90% by default, "all losses included"), so a
replayed day earned about £1.45 a day more than the same day metered. `round_trip` works out the grid-to-grid figure
from the cost records before the day being replayed, the same way the app does, so the replay world loses what the
real system loses.
"""

from __future__ import annotations

import collections
import json
import os
from datetime import date, timedelta

from .. import learn as learning
from ..costbook import MEASURE_DAYS, MIN_MEASURE_DAYS, CostBook


def _records(costs_dir: str, day: date, days: int) -> list[dict]:
    out: list[dict] = []
    for n in range(days, 0, -1):
        try:
            with open(os.path.join(costs_dir, f"{(day - timedelta(days=n)).isoformat()}.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, list):
            out += data
    return out


def round_trip(costs_dir: str, day: date, battery: dict) -> dict | None:
    """{"round_trip": 0.76, "battery": 0.94, "charge_conv": 0.894, "discharge_conv": 0.904, "days": 30, and when
    measured "capacity_kwh", "max_charge_w", "max_discharge_w"} from the cost
    records of the days before `day`, or None when they do not show enough (fewer than MIN_MEASURE_DAYS complete days,
    too little cycling, or too few full-rate half-hours to learn the conversion). `battery` is `settings.battery_of`."""
    records = _records(costs_dir, day, MEASURE_DAYS)
    if not records:
        return None
    flow = collections.Counter(r.get("fv") for r in records).most_common(1)[0][0]
    book = CostBook(costs_dir)
    book.flow_id = str(flow)
    measured = book.measure(day, battery["capacity_kwh"])
    if not measured.get("measured") or measured.get("days", 0) < MIN_MEASURE_DAYS:
        return None
    halves = book.halves(day)
    lr = learning.learn(halves, battery["charge_limit_w"] / 1000, battery["discharge_limit_w"] / 1000,
                        battery["floor_pct"], 6.0, learning.ColdSettings())
    if not (lr.charge_conv and lr.discharge_conv):
        return None
    one_way = measured["efficiency"] * (lr.charge_conv * lr.discharge_conv) ** 0.5
    if not 0.7 <= one_way <= 1.0:
        return None
    out = {"round_trip": round(one_way * one_way, 4), "battery": measured["efficiency"],
           "charge_conv": lr.charge_conv, "discharge_conv": lr.discharge_conv, "days": measured["days"]}
    if measured.get("capacity_measured") and measured.get("capacity_kwh"):
        out["capacity_kwh"] = measured["capacity_kwh"]               # what the battery holds, not what was typed in
    rated_c, rated_d = battery["charge_limit_w"], battery["discharge_limit_w"]
    if lr.max_charge_kw and 0.5 * rated_c <= lr.max_charge_kw * 1000 <= 1.2 * rated_c:
        out["max_charge_w"] = round(lr.max_charge_kw * 1000)       # the fastest it has really charged
    if lr.max_discharge_kw and 0.5 * rated_d <= lr.max_discharge_kw * 1000 <= 1.2 * rated_d:
        out["max_discharge_w"] = round(lr.max_discharge_kw * 1000)
    return out
