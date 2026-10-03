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


class Shadow:
    """A virtual inverter run alongside the real one with a given dampening setting, to measure what each setting
    saves: it follows the same plan and decisions, the same settle rule for later windows, and counts the real writes
    (update-button presses, currents, mode) it would make."""

    def __init__(self, restart: bool, bursts: bool):
        self.restart, self.bursts = restart, bursts
        self.damper = Damper()
        self.virt: dict | None = None
        self.pending: tuple | None = None

    def step(self, now: datetime, now_local: datetime, pers, action: str, power_w, rule: str | None, have: dict,
             volts: float, max_charge_w: float, max_discharge_w: float, day: str, window_min: float,
             settle_min: float, slots: int = 3, button_role: str = "timed_update_button") -> int:
        from .journal import is_staged
        from .schedule import desired_state, settled, urgent, writes_for
        if self.virt is None:
            self.virt = dict(have)
        want = desired_state(pers, self.virt, now_local, action, power_w, volts, max_charge_w, max_discharge_w, slots)
        writes = writes_for(want, self.virt, button_role)
        if writes and not urgent(writes, want, self.virt, now_local):
            ok, self.pending = settled(self.pending, want, now)
            if not ok:
                writes = []
        else:
            self.pending = None
        writes = self.damper.filter(now, day, writes, want, rule, self.restart, self.bursts, window_min, settle_min)
        for w in writes:
            if w.kind != "button":
                self.virt[w.role] = w.value
        if writes:
            self.damper.wrote(now, writes)
        return sum(1 for w in writes if not is_staged(w.role))


SHADOWS = {"damp_none": (False, False), "damp_restart": (True, False), "damp_both": (True, True)}


def week_summary(counts: dict[str, dict[str, int]], days: list[str]) -> dict:
    """Modelled writes over `days` with no dampening, restart hold-off only, and both; and what each saves."""
    tot = {k: sum(counts.get(k, {}).get(d, 0) for d in days) for k in SHADOWS}
    return {"days": len(days), "none": tot["damp_none"], "restart": tot["damp_restart"], "both": tot["damp_both"],
            "saved_restart": tot["damp_none"] - tot["damp_restart"],
            "saved_bursts": tot["damp_restart"] - tot["damp_both"],
            "since": min((d for k in SHADOWS for d, n in counts.get(k, {}).items() if d in days), default=None)}
