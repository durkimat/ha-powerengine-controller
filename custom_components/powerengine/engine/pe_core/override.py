"""A manual override of the inverter's mode for a while (docs/plans/mode-override.md).

The owner picks a mode and a period from the Monitoring page; until the period ends the controller does that instead
of the plan. Periods end on a half-hour boundary so they line up with the plan's slots, or never (permanent, until
cancelled). Pure: parsing, expiry, storage and the decision; the app only wires events and the file.

Still applied over an override (they sit around `decide`, not inside it): Pause, the fuse limit, BMS limits and the
write budget. Inside: a grid event in progress wins (the app's `decide` checks it first), and the minimum reserve
stops Self-use and Export, then holds.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .decide import EXPORT, GRID_CHARGE, HOLD, SELF_USE, Decision

HALF = timedelta(minutes=30)
MAX_HOURS = 12
MAX_SLOTS = MAX_HOURS * 2
LATCH_BAND_SOC = 2.0                  # a charge held at its target resumes this far below it

MODES = {SELF_USE: "Self-use", HOLD: "Hold (grid runs the house)", GRID_CHARGE: "Charge", EXPORT: "Export"}
SHORT = {SELF_USE: "Self-use", HOLD: "Hold", GRID_CHARGE: "Charge", EXPORT: "Export"}


@dataclass(frozen=True)
class Override:
    mode: str
    until: datetime | None            # None: permanent
    set_at: datetime

    def as_dict(self) -> dict:
        return {"mode": self.mode, "until": self.until.isoformat() if self.until else None,
                "set_at": self.set_at.isoformat()}


def next_boundary(now: datetime) -> datetime:
    """The next half-hour boundary after `now` (the end of the half-hour now running)."""
    base = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
    return base + HALF


def parse(data: dict, now: datetime, window_end: datetime | None = None) -> tuple[Override | None, str]:
    """(override, "") or (None, why not). The period is one of `permanent: true`, `window: true` (until the plan
    window now running ends, `window_end`), `slots: N` (N half-hours counting the one running) or `until` (an ISO time
    on a half-hour boundary). Never more than 12 hours, except permanent."""
    mode = data.get("mode")
    if mode not in MODES:
        return None, f"Choose one of: {', '.join(SHORT[m] for m in MODES)}."
    if data.get("permanent"):
        return Override(mode, None, now), ""
    first = next_boundary(now)
    if data.get("window"):
        if window_end is None:
            return None, "There is no plan window running now; choose a number of half-hours or a time."
        until = window_end
    elif data.get("slots") is not None:
        try:
            n = int(data["slots"])
        except (TypeError, ValueError):
            return None, "The number of half-hours isn't a number."
        if not 1 <= n <= MAX_SLOTS:
            return None, f"Choose 1 to {MAX_SLOTS} half-hours."
        until = first + (n - 1) * HALF
    elif data.get("until"):
        try:
            until = datetime.fromisoformat(str(data["until"]))
        except ValueError:
            return None, "That time isn't understood."
        if until.tzinfo is None:
            return None, "The time needs a timezone."
        if until.minute % 30 or until.second or until.microsecond:
            return None, "The end must be on the hour or half past."
    else:
        return None, "Choose how long: a plan window, a number of half-hours, a time or permanent."
    until = until.astimezone(now.tzinfo or timezone.utc)
    if until <= now:
        return None, "That time has already passed."
    if until - now > timedelta(hours=MAX_HOURS, minutes=30):
        return None, f"An override lasts at most {MAX_HOURS} hours; use permanent to stay until cancelled."
    return Override(mode, until, now), ""


def expired(ov: Override | None, now: datetime) -> bool:
    return ov is not None and ov.until is not None and now >= ov.until


def describe(ov: Override, tz=None) -> str:
    if ov.until is None:
        return f"{SHORT[ov.mode]} until cancelled"
    end = ov.until.astimezone(tz) if tz else ov.until
    return f"{SHORT[ov.mode]} until {end:%H:%M}"


def decide(ov: Override, soc: float, price: str, target: float, reserve: float, tz=None,
           previous: Decision | None = None) -> Decision:
    """What the controller does under the override. `price` is the rate in words, `target` the charge target."""
    why = f"manual override ({describe(ov, tz)})"
    if ov.mode == HOLD:
        return Decision(HOLD, "override", f"{why}: the grid runs the house at {price}")
    if ov.mode in (SELF_USE, EXPORT) and soc <= reserve:
        return Decision(HOLD, "override", f"{why}, but the battery is at its {reserve:.0f}% minimum reserve")
    if ov.mode == SELF_USE:
        return Decision(SELF_USE, "override", f"{why}: the battery covers the house")
    if ov.mode == EXPORT:
        return Decision(EXPORT, "override", f"{why}: selling from the battery")
    # Charge: up to the target, then hold; resume a little below it, so a SoC reading a point lower while charging
    # does not flip between Force charge and Hold
    held = previous is not None and previous.rule == "override" and previous.action == HOLD \
        and previous.details.get("reached")
    if soc >= target or (held and soc > target - LATCH_BAND_SOC):
        return Decision(HOLD, "override", f"{why}: reached the {target:.0f}% charge target", target_soc=target,
                        details={"reached": True})
    return Decision(GRID_CHARGE, "override", f"{why}: charging at {price}", target_soc=target)


def load(path: str, now: datetime) -> Override | None:
    """The saved override, unless it has expired or the file is unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        until = datetime.fromisoformat(d["until"]) if d.get("until") else None
        ov = Override(d["mode"], until, datetime.fromisoformat(d["set_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None if ov.mode not in MODES or expired(ov, now) else ov


def save(path: str, ov: Override | None) -> None:
    if ov is None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ov.as_dict(), fh)
    os.replace(tmp, path)
