"""Stop the car charger during a grid event, and put its mode back afterwards.

A grid event pays for energy sent to the grid. A car charging at the same time takes that energy itself (6 Oct 2026: a
Zappi drew 7.2 kW through a one-hour event, the battery's 5 kW went into the car and the house, and the meter exported
almost nothing). So while an event runs the charger is set to Stopped, and when it ends the mode it had is restored.

`CarStop` is pure: `step` is told what is wanted and what the charger's mode reads now, and returns the mode to set (or
None). The app does the reading, the writing and the saving. It remembers what it changed (`held`, `previous`), so:

* it never stops a charger that was already stopped, and never restores one it did not stop;
* it restores the mode it found (not always Eco+), and only if the charger still reads Stopped (if you or the
  charger changed it since, that choice stands);
* if something sets the mode back during the event it stops it again, but only a few times and not more often than
  every couple of minutes, so it never fights you for the whole event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

STOPPED = "stopped"
FALLBACK = "Eco+"                       # when the mode before the event can't be offered back
MAX_STOPS_AFTER_FIRST = 3
MIN_GAP_S = 120
_UNREADABLE = ("", "unknown", "unavailable", "none")


@dataclass
class Action:
    option: str                         # the charge mode to select
    text: str                           # for the log


def _match(options: list[str] | None, wanted: str | None) -> str | None:
    """The option that reads as `wanted` (case and spaces ignored), as the charger spells it."""
    key = (wanted or "").replace(" ", "").lower()
    for o in options or []:
        if str(o).replace(" ", "").lower() == key:
            return str(o)
    return None


class CarStop:
    def __init__(self, held: bool = False, previous: str | None = None):
        self.held = held
        self.previous = previous
        self.again = 0
        self.last: datetime | None = None

    def to_dict(self) -> dict:
        return {"held": self.held, "previous": self.previous}

    @classmethod
    def from_dict(cls, data) -> CarStop:
        data = data if isinstance(data, dict) else {}
        previous = data.get("previous")
        return cls(bool(data.get("held")), previous if isinstance(previous, str) else None)

    def step(self, now: datetime, want_stop: bool, mode: str | None, options: list[str] | None = None) -> Action | None:
        """The mode to set now, or None. `mode` is what the charger reads; `options` the modes it offers."""
        cur = (mode or "").strip()
        unreadable = cur.lower() in _UNREADABLE
        stopped = _match(options, STOPPED) or "Stopped"
        is_stopped = cur.lower() == STOPPED
        if want_stop:
            if unreadable:
                return None
            if not self.held:
                if is_stopped:
                    return None                    # already stopped by someone else: leave it as it is
                self.held, self.previous, self.again, self.last = True, cur, 0, now
                return Action(stopped, f"grid event: the car charger was {cur}, now stopped until the event ends")
            if (not is_stopped and self.again < MAX_STOPS_AFTER_FIRST
                    and (self.last is None or (now - self.last).total_seconds() >= MIN_GAP_S)):
                self.again += 1
                self.last = now
                return Action(stopped, f"grid event: the car charger changed to {cur}; stopped again")
            return None
        if not self.held or unreadable:
            return None                            # nothing of ours to undo (or can't tell yet: stay held)
        previous, self.held, self.previous = self.previous, False, None
        if not is_stopped:
            return None                            # someone changed it since: that choice stands
        option = _match(options, previous) or _match(options, FALLBACK) or previous or FALLBACK
        return Action(option, f"grid event over: the car charger is back to {option}")
