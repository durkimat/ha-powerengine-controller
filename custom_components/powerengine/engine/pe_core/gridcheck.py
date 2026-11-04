"""Grid meter cross-check: the inverter's grid meter against a second, independent one (a Zappi's grid CT, say).

Found on 27 Sep 2026: while the battery charges from the grid, the Solis meter reads about 2 kW more import than
the battery takes in, and the inverter books the difference as house load. A second meter says which one is
right. Readings are compared by what the battery is doing (charging, discharging, idle), because the gap only
showed while charging. Pure functions and a small accumulator, so it tests without Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

BATTERY_BUSY_W = 1500          # |battery| above this is charging / discharging; below 300 is idle
BATTERY_IDLE_W = 300
MAX_GAP = timedelta(minutes=3)  # the reference must have reported within this (myenergi polls about once a minute)
KEEP_DAYS = 3
FLAG_W = 400                   # an average disagreement above this in one state is reported


def battery_state(battery_w: float | None) -> str | None:
    """'charging' | 'discharging' | 'idle' | None (in between, or unknown). battery_w is + discharging."""
    if battery_w is None:
        return None
    if battery_w <= -BATTERY_BUSY_W:
        return "charging"
    if battery_w >= BATTERY_BUSY_W:
        return "discharging"
    if abs(battery_w) <= BATTERY_IDLE_W:
        return "idle"
    return None


@dataclass
class Bucket:
    n: int = 0
    inverter: float = 0.0
    reference: float = 0.0

    def add(self, inverter_w: float, reference_w: float) -> None:
        self.n += 1
        self.inverter += inverter_w
        self.reference += reference_w

    def merge(self, other: Bucket) -> None:
        self.n += other.n
        self.inverter += other.inverter
        self.reference += other.reference

    def summary(self) -> dict:
        if not self.n:
            return {"samples": 0}
        inv, ref = self.inverter / self.n, self.reference / self.n
        return {"samples": self.n, "inverter_w": round(inv), "reference_w": round(ref),
                "difference_w": round(inv - ref)}


@dataclass
class GridCheck:
    days: dict[str, dict[str, Bucket]] = field(default_factory=dict)   # day -> state -> bucket

    def add(self, day: str, now: datetime, battery_w: float | None, inverter_w: float | None,
            reference_w: float | None, reference_at: datetime | None) -> str | None:
        """Record one paired reading; returns the battery state it was filed under (None if skipped)."""
        state = battery_state(battery_w)
        if state is None or inverter_w is None or reference_w is None:
            return None
        if reference_at is None or abs(now - reference_at) > MAX_GAP:
            return None
        self.days.setdefault(day, {}).setdefault(state, Bucket()).add(inverter_w, reference_w)
        for d in sorted(self.days)[:-KEEP_DAYS]:
            del self.days[d]
        return state

    def summary(self) -> dict:
        total: dict[str, Bucket] = {}
        for by_state in self.days.values():
            for state, b in by_state.items():
                total.setdefault(state, Bucket()).merge(b)
        out = {s: total[s].summary() for s in ("charging", "discharging", "idle") if s in total}
        out["verdict"] = verdict(out)
        return out


def verdict(summary: dict) -> str:
    """Plain words on what the comparison shows so far."""
    diffs = {s: v["difference_w"] for s, v in summary.items()
             if isinstance(v, dict) and v.get("samples", 0) >= 20 and "difference_w" in v}
    if not diffs:
        return "Collecting readings (needs about 20 minutes each of charging, discharging and idle)."
    off = {s: d for s, d in diffs.items() if abs(d) > FLAG_W}
    if not off:
        return f"The two meters agree (within {FLAG_W} W) in every battery state seen so far."
    parts = ", ".join(f"{s}: inverter meter {'+' if d > 0 else ''}{d} W vs the reference" for s, d in off.items())
    return "The meters disagree — " + parts + "."
