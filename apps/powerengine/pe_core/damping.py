"""Dampening tuning: holding inverter writes back briefly when the wanted settings are likely to change again.

Two optional rules (config page, Dampening tuning):
  - Restart hold-off (on by default): for a few minutes after PowerEngine starts, or control resumes or goes
    Active, nothing is written. Straight after a restart the plan and its inputs are still settling, and the inverter
    keeps running the windows PowerEngine already set, so waiting costs nothing.
  - Burst damping (off by default, to evaluate): the first change to a slot, current or the mode goes straight
    through, but another change to something written in the last few minutes waits until the wanted settings have
    been steady for a while, so a burst of changes becomes one write.
Neither ever delays a safety-driven change (Axle, free power, the car charging, the minimum reserve), nor pausing
or leaving Active (those return the inverter to Self-Use through their own path).
"""

from __future__ import annotations

from datetime import datetime, timedelta

URGENT_RULES = frozenset({"axle_active", "pre_axle", "free_power", "car_charging", "reserve"})


def write_keys(writes) -> set[str]:
    """What each write touches: a window slot ('slot2'), a current or the mode."""
    keys = set()
    for w in writes:
        role = w.role
        if "#" in role:
            keys.add("slot" + role.split("#")[1])
        else:
            keys.add(role)
    return keys


def stable(pending: tuple | None, want: dict, now: datetime, need: timedelta) -> tuple[bool, tuple]:
    """(steady long enough?, new pending): the same wanted settings for at least `need`."""
    key = tuple(sorted((k, str(v)) for k, v in want.items()))
    if pending is None or pending[0] != key:
        return need <= timedelta(0), (key, now)
    return now - pending[1] >= need, pending


class Damper:
    def __init__(self):
        self.hold_until: datetime | None = None
        self.last_write: dict[str, datetime] = {}
        self.pending: tuple | None = None
        self.held: dict[str, int] = {}          # day -> changes held back (for the Health tab)
        self.last_reason: str | None = None

    def restarted(self, now: datetime, minutes: float) -> None:
        """PowerEngine started, or control resumed / went Active: hold writes for `minutes`."""
        until = now + timedelta(minutes=minutes)
        if self.hold_until is None or until > self.hold_until:
            self.hold_until = until

    def filter(self, now: datetime, day: str, writes: list, want: dict, rule: str | None, restart_on: bool,
               bursts_on: bool, window_min: float, settle_min: float) -> list:
        """The writes to make now (all, or none while held back)."""
        self.last_reason = None
        if not writes:
            self.pending = None
            return writes
        if rule in URGENT_RULES:
            self.pending = None
            return writes
        if restart_on and self.hold_until is not None and now < self.hold_until:
            return self._hold(day, f"restart hold-off until {self.hold_until.isoformat(timespec='seconds')}")
        if bursts_on:
            recent = [k for k in write_keys(writes)
                      if k in self.last_write and now - self.last_write[k] < timedelta(minutes=window_min)]
            if recent:
                ok, self.pending = stable(self.pending, want, now, timedelta(minutes=settle_min))
                if not ok:
                    return self._hold(day, f"burst damping: {', '.join(sorted(recent))} changed in the last "
                                           f"{window_min:g} min; waiting for {settle_min:g} min of steady plan")
        self.pending = None
        return writes

    def _hold(self, day: str, reason: str) -> list:
        self.last_reason = reason
        return []

    def count_held(self, day: str) -> None:
        self.held[day] = self.held.get(day, 0) + 1
        self.held = {d: n for d, n in self.held.items() if d >= day}

    def wrote(self, now: datetime, writes) -> None:
        for k in write_keys(writes):
            self.last_write[k] = now
