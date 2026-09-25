"""Inverter write tracking: how fast settings writes would use up the inverter's EEPROM.

Inverter settings live in EEPROM, typically rated for about 100,000 writes. Two counts are kept per day:
  observed  writes by whatever controls the inverter now (Predbat, automations): each state change of a mapped
            control entity; a press of the timed-window update button counts as one write.
            (Setting a number to the value it already has doesn't change its state, so this can undercount.)
  would     writes PowerEngine's Active design would make for its own decisions (see WriteModel).
From each daily average comes a lifespan: how many years 100,000 writes would last at that rate.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from .decide import EXPORT, FORCE_DISCHARGE, GRID_CHARGE, HOLD, Decision

BUDGET = 100_000
WINDOW_MINUTES = 35        # a window never reaches further ahead than this (dead man's switch)
ROLL_BEFORE_MINUTES = 5    # extend the window this long before it ends
ACTIONS_WITH_WINDOW = {GRID_CHARGE: "charge", HOLD: "charge", FORCE_DISCHARGE: "discharge", EXPORT: "discharge"}


class WriteModel:
    """The writes Active mode would make, following the design: one short timed window at a time.

    Opening, rolling forward or closing a window = one press of the update button (one block write).
    A change of charge/discharge current = one write. Self-use needs no writes.
    """

    def __init__(self, battery_v: float = 52.0, step_a: int = 5):
        self.battery_v, self.step_a = battery_v, step_a
        self.kind: str | None = None          # "charge" | "discharge" while a window is open
        self.end: datetime | None = None
        self.current: dict[str, int | None] = {"charge": None, "discharge": None}

    def _amps(self, d: Decision, default_w: float) -> int:
        if d.action == HOLD:
            return 0
        w = d.power_w if d.power_w is not None else default_w
        return int(round(w / self.battery_v / self.step_a) * self.step_a)

    def step(self, now: datetime, d: Decision | None, max_charge_w: float = 4800,
             max_discharge_w: float = 4800) -> list[str]:
        events: list[str] = []
        kind = ACTIONS_WITH_WINDOW.get(d.action) if d else None
        if kind is None:
            if self.kind is not None:
                events.append(f"{self.kind}_window")          # close
                self.kind, self.end = None, None
            return events
        amps = self._amps(d, max_charge_w if kind == "charge" else max_discharge_w)
        if self.kind != kind:
            if self.kind is not None:
                events.append(f"{self.kind}_window")          # close the other kind first
            events.append(f"{kind}_window")                   # open
            self.kind, self.end = kind, now + timedelta(minutes=WINDOW_MINUTES)
        elif self.end is not None and now >= self.end - timedelta(minutes=ROLL_BEFORE_MINUTES):
            events.append(f"{kind}_window")                   # roll forward
            self.end = self.end + timedelta(minutes=30)
        if self.current[kind] != amps:
            events.append(f"{kind}_current")
            self.current[kind] = amps
        return events


class WriteLog:
    def __init__(self, path: str | None = None):
        self.path = path
        self.data: dict = {"observed": {}, "would": {}, "by_entity": {}, "since": None}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    self.data.update(json.load(fh))
            except (OSError, ValueError):
                pass
        self._dirty = False

    def _add(self, kind: str, day: date, n: int = 1) -> None:
        if n <= 0:
            return
        key = day.isoformat()
        self.data[kind][key] = self.data[kind].get(key, 0) + n
        self.data["since"] = self.data["since"] or key
        self._dirty = True

    def would(self, day: date, n: int) -> None:
        self._add("would", day, n)

    def observed(self, day: date, entity_id: str) -> None:
        self._add("observed", day)
        self.data["by_entity"][entity_id] = self.data["by_entity"].get(entity_id, 0) + 1

    def save(self, force: bool = False) -> None:
        if not self.path or not (self._dirty or force):
            return
        cutoff = (date.today() - timedelta(days=400)).isoformat()
        for kind in ("observed", "would"):
            self.data[kind] = {d: n for d, n in self.data[kind].items() if d >= cutoff}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh)
        os.replace(tmp, self.path)
        self._dirty = False

    def summary(self, today: date, days: int = 14) -> dict:
        since = self.data.get("since")
        out: dict = {"budget": BUDGET, "since": since, "by_entity": self.data["by_entity"]}
        for kind in ("observed", "would"):
            counts = self.data[kind]
            window = [(today - timedelta(days=i)).isoformat() for i in range(days, 0, -1)]
            full = [d for d in window if since and d > since]          # skip the first (partial) day
            total = sum(counts.get(d, 0) for d in full)
            per_day = total / len(full) if full else None
            out[kind] = {
                "today": counts.get(today.isoformat(), 0),
                "per_day": round(per_day, 1) if per_day is not None else None,
                "days": len(full),
                "total": sum(counts.values()),
                "years": round(BUDGET / (per_day * 365), 1) if per_day else None,
            }
        return out
