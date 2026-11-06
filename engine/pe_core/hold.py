"""Keep the last good reading of a list while its sensor is briefly unavailable.

The supplier's integration drops its sensors for a minute or two now and then (a refresh, a re-login). The planned
smart-charge slots and the rate list are attributes of those sensors, so while one is unavailable they read as empty,
which looks like "the slots were withdrawn" and sets off a re-plan, and another when they come back. `Hold` keeps the
last list that was read while its sensor was usable and hands it back while the sensor is not, for up to `HOLD_S`. A
sensor that is available and says "no slots" is a real answer and passes straight through; so does an unavailable one
after `HOLD_S`, on the view that a long outage is not a blip.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

HOLD_S = 15 * 60          # how long an unavailable sensor's last good list is kept

_UNUSABLE = (None, "", "unknown", "unavailable", "None")


def usable(state: dict | None) -> bool:
    """Is this entity's state one to believe (it exists and is not unknown or unavailable)?"""
    return state is not None and str(state.get("state")) not in _UNUSABLE


class Hold:
    def __init__(self, log: Callable[..., Any] | None = None, hold_s: float = HOLD_S):
        self._log = log or (lambda *a, **k: None)
        self._hold_s = hold_s
        self._good: dict[str, Any] = {}          # key -> the last value read while its sensor was usable
        self._out: dict[str, dict] = {}          # key -> {"since": when it went, "gave_up": bool} while it is out

    def keep(self, key: str, value: Any, ok: bool, now: datetime, what: str) -> Any:
        """`value` if the sensor is usable (and remembered), else the last good one while it is within the hold."""
        if ok:
            out = self._out.pop(key, None)
            if out is not None:
                self._log(f"{what} are back after {(now - out['since']).total_seconds():.0f} s.")
            self._good[key] = value
            return value
        out = self._out.get(key)
        if out is None:
            out = self._out[key] = {"since": now, "gave_up": False}
            if key in self._good:
                self._log(f"{what} are unavailable: keeping the last known for up to {self._hold_s / 60:.0f} minutes.")
        if key not in self._good:
            return value                                  # nothing good has been seen yet: nothing to keep
        age = (now - out["since"]).total_seconds()
        if age <= self._hold_s:
            return self._good[key]
        if not out["gave_up"]:
            out["gave_up"] = True
            self._log(f"{what} have been unavailable for {age / 60:.0f} minutes: no longer keeping the last known.",
                      level="WARNING")
        return value
