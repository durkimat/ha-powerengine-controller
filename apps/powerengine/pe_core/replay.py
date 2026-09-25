"""Replaying Home Assistant history as if PowerEngine had been running.

Used to backfill the cost book: each mapped entity's recorded states become a timeline, and `read()` is called at
30-second steps with a get_state that answers from those timelines. The readings are therefore built exactly as
they are live (units, inverts, car removed from house load, solar plants summed).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator
from datetime import datetime, timedelta

from .config import Config
from .forecast import flatten_history
from .readings import Readings, parse_time, read

STEP = timedelta(seconds=30)

# roles the cost book needs from history (attribute roles are fetched with attributes)
COST_ROLES = ("battery_soc", "battery_power", "battery_charge_power", "battery_discharge_power", "grid_power",
              "house_load_power", "ev_charge_power", "ev_plug_status",
              "import_rate_now", "export_rate", "standing_charge", "axle_event_active", "free_power_active")
ATTRIBUTE_ROLES = ("import_rates_today",)


def history_entities(cfg: Config) -> tuple[list[str], list[str]]:
    """(entities fetched without attributes, entities fetched with attributes) for a replay."""
    plain, full = [], []
    for role in COST_ROLES:
        spec = cfg.inputs.get(role) or {}
        if "entity" in spec:
            plain.append(spec["entity"])
    for plant in cfg.solar_plants:
        if plant.enabled and "entity" in plant.power:
            plain.append(plant.power["entity"])
    for role in ATTRIBUTE_ROLES:
        spec = cfg.inputs.get(role) or {}
        if "entity" in spec:
            full.append(spec["entity"])
    return sorted(set(plain)), sorted(set(full))


class Timeline:
    """One entity's states over time."""

    def __init__(self, rows, attrs: dict | None = None):
        pts = []
        for row in flatten_history(rows):
            t = parse_time(row.get("last_changed") or row.get("last_updated"))
            if t is not None:
                pts.append((t, row.get("state"), row.get("attributes")))
        pts.sort(key=lambda x: x[0])
        self.times = [p[0] for p in pts]
        self.points = pts
        self.attrs = dict(attrs or {})       # current attributes (units) for rows that carry none

    def at(self, t: datetime) -> dict | None:
        i = bisect_right(self.times, t) - 1
        if i < 0:
            return None
        _, state, attrs = self.points[i]
        return {"state": state, "attributes": {**self.attrs, **(attrs or {})}}

    def __len__(self) -> int:
        return len(self.points)


def replay(cfg: Config, timelines: dict[str, Timeline], start: datetime, end: datetime) -> Iterator[Readings]:
    """Readings every 30 s from `start` to `end` (exclusive), rebuilt from history."""
    def get_state(eid: str):
        tl = timelines.get(eid)
        return tl.at(t) if tl else None

    t = start
    while t < end:
        yield read(cfg, get_state, t)
        t += STEP
