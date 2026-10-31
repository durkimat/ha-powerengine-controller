"""Turn the heating down before a grid event, and back up when it ends.

The boiler's circulation pump draws a few hundred watts while the heating calls for heat and for a while after (its
overrun), and every unit the house doesn't use during a grid event is exported at the event rate. So a switch the
owner chooses (a Home Assistant input_boolean or switch that sets the heating back, e.g. `input_boolean.heating_off`)
is turned on `lead` minutes before the event starts, the pump overrun, so the pump has stopped by the time the event
begins, and turned off when the event ends. What "set back" means is the switch's business, not PowerEngine's.

`Setback` is pure: `step` is told whether the set-back is wanted and what the switch reads, and returns the action to
take (or None). The app does the reading, the writing and the saving. It remembers what it did (`held`), so:

* it never turns on a switch that was already on, and never turns off one it did not turn on;
* if you turn the switch off yourself during the set-back (the house is cold), that choice stands for the rest of
  that event: it is not switched on again, and nothing is switched off at the end;
* it turns the switch off at the end only if it still reads on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

_UNREADABLE = ("", "unknown", "unavailable", "none")


def window(start: datetime | None, end: datetime | None, lead_min: float) -> tuple[datetime, datetime] | None:
    """When the set-back runs for an event: from `lead_min` minutes before it starts until it ends."""
    if start is None or end is None or end <= start:
        return None
    return start - timedelta(minutes=lead_min), end


@dataclass
class Action:
    service: str                        # "turn_on" or "turn_off"
    text: str                           # for the log


class Setback:
    def __init__(self, held: bool = False, handled: str | None = None):
        self.held = held                # PowerEngine turned the switch on and has not yet turned it off
        self.handled = handled          # the event (its start, iso) already acted on or left alone: once is enough

    def to_dict(self) -> dict:
        return {"held": self.held, "handled": self.handled}

    @classmethod
    def from_dict(cls, data) -> Setback:
        data = data if isinstance(data, dict) else {}
        handled = data.get("handled")
        return cls(bool(data.get("held")), handled if isinstance(handled, str) else None)

    def step(self, want: bool, event: str | None, state: str | None, lead_min: float = 0) -> Action | None:
        """The action to take now, or None. `event` names the event (its start, iso) the set-back is for; `state` is
        what the switch reads."""
        cur = (state or "").strip().lower()
        if cur in _UNREADABLE:
            return None
        if want and event is not None:
            if self.handled != event:
                self.handled = event
                if cur == "on":
                    return None                             # already on, someone else's: leave it as it is
                self.held = True
                return Action("turn_on", f"grid event: heating set back {lead_min:g} min before it starts")
            if self.held and cur == "off":                  # turned off during the set-back: that choice stands
                self.held = False
            return None
        if self.held:
            self.held = False
            if cur == "on":
                return Action("turn_off", "grid event over: heating set-back ended")
        return None
