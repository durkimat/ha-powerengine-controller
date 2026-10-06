"""The day to replay: the cost records of one local day as a one-day pack, the forecast snapshot, and what the day's
real cost and control were (for the calibration).

Refuses a day it can't replay fairly, with a reason: no snapshot (the forecasts as they were), or records that don't
cover the day.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from ..demo.pack import day_complete, day_from_records
from .snapfeed import Snapshot, load_snapshot

KEY = "replay"                      # the day's name inside its one-day pack


@dataclass
class DayInput:
    day: date
    tz: ZoneInfo
    start: datetime                  # local midnight (aware)
    records: list
    pack: dict
    snapshot: Snapshot
    in_control: str | None           # "v1" | "v2" | "mixed" | None (no records)
    live: bool                       # an engine was really sending commands (Active, not paused)
    metered_gbp: float


class Refused(Exception):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


def read_records(costs_dir: str, day: date) -> list[dict]:
    try:
        with open(os.path.join(costs_dir, f"{day.isoformat()}.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def day_engine(records: list[dict]) -> tuple[str | None, bool]:
    """(engine, live): "v1" / "v2" / "mixed" from the records marked live; with none live, the engine chosen (the
    commonest; a record without an `engine` counts as v1) and live False. None for no records."""
    if not records:
        return None, False
    live = [r for r in records if r.get("live")]
    pool = live or records
    engines = {r.get("engine") or "v1" for r in pool}
    if len(engines) > 1:
        return "mixed", bool(live)
    return engines.pop(), bool(live)


def metered_cost(records: list[dict]) -> float:
    """What the day cost as metered, GBP, no standing charge: grid import x the rate in force, less grid export x the
    export rate, less the grid-event pay (event energy is paid at its own rate, not the export rate)."""
    total = 0.0
    for r in records:
        v = r.get("v") or {}
        imp, exp = float(r.get("grid_import") or 0.0), float(r.get("grid_export") or 0.0)
        act, exr = float(v.get("act") or 0.0), float(v.get("exp") or 0.0)
        ev = pay = 0.0
        if r.get("axle"):
            ev = min(float(v.get("event_kwh") or 0.0), exp)
            pay = float(v.get("event_gross") or 0.0) * (ev / float(v["event_kwh"]) if v.get("event_kwh") else 0.0)
        total += imp * act - (exp - ev) * exr - pay
    return total


def load_day(costs_dir: str, day: date, tz_name: str | None = None) -> DayInput:
    """Everything a replay of `day` needs, or raises Refused(status, reason)."""
    data = load_snapshot(costs_dir, day)
    if data is None:
        raise Refused("no_snapshot", "no forecast record for the day")
    snap = Snapshot(data)
    tz = ZoneInfo(str(snap.tz or tz_name or "Europe/London"))
    start = datetime.combine(day, time(0), tzinfo=tz)
    why = snap.refusal(start)
    if why:
        raise Refused("no_snapshot", why)
    records = read_records(costs_dir, day)
    why = day_complete(records, tz) if records else "no cost records for the day"
    if why:
        raise Refused("incomplete", f"records incomplete: {why}")
    pack = {"version": 1, "tz": str(tz.key), "source": "real day",
            "days": {KEY: day_from_records(records, "", "", 1.0, tz)}}
    engine, live = day_engine(records)
    return DayInput(day, tz, start, records, pack, snap, engine, live, metered_cost(records))
