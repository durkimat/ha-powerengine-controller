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
    profile: str | None = None       # engine v2's settings profile the records name ("mixed" if it changed), if any


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


def day_profile(records: list[dict]) -> str | None:
    """The engine v2 settings profile the day's live v2 records name, "mixed" if more than one, None if none do."""
    names = {r["profile"] for r in records if r.get("profile") is not None and r.get("engine") == "v2"
             and r.get("live")}
    return (names.pop() if len(names) == 1 else "mixed") if names else None


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


REPLAY_MIN_COVER = 0.90          # a day measured for this much of its seconds can be replayed (thin half-hours scaled)
MIN_HALF_SECONDS = 600           # ...but a half-hour measured for less than this (10 minutes) is too thin to scale
SCALED = ("s_h", "s_c", "s_b", "s_e", "b_h", "b_c", "b_e", "g_h", "g_c", "g_b", "grid_import", "grid_export", "house",
          "car", "solar", "battery_in", "battery_out", "unallocated_src", "unallocated_sink")


def fill_thin(records: list[dict]) -> tuple[list[dict], int]:
    """The records with each half-hour measured for under its full 1800 seconds scaled up to the whole half-hour (the
    energy measured in the seconds that were read, taken as typical of the rest). Returns (records, how many were
    scaled). A half-hour with under MIN_HALF_SECONDS is left as it is; `thin_reason` refuses the day for those."""
    out, n = [], 0
    for r in records:
        sec = float(r.get("seconds") or 0.0)
        if MIN_HALF_SECONDS <= sec < 1795:
            k = 1800.0 / sec
            r = {**r, **{f: r[f] * k for f in SCALED if isinstance(r.get(f), (int, float))}, "seconds": 1800.0}
            n += 1
        out.append(r)
    return out, n


def thin_reason(records: list[dict]) -> str | None:
    thin = [r for r in records if float(r.get("seconds") or 0.0) < MIN_HALF_SECONDS]
    return f"{len(thin)} half-hour(s) measured for under {MIN_HALF_SECONDS // 60} minutes" if thin else None


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
    why = day_complete(records, tz, REPLAY_MIN_COVER) if records else "no cost records for the day"
    if why:
        raise Refused("incomplete", f"records incomplete: {why}")
    why = thin_reason(records)
    if why:
        raise Refused("incomplete", f"records incomplete: {why}")
    records, _scaled = fill_thin(records)
    pack = {"version": 1, "tz": str(tz.key), "source": "real day",
            "days": {KEY: day_from_records(records, "", "", 1.0, tz)}}
    engine, live = day_engine(records)
    return DayInput(day, tz, start, records, pack, snap, engine, live, metered_cost(records), day_profile(records))
