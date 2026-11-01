"""Run chosen switches while there is free power.

EDF's free-electricity sessions make every unit the house draws free, so the owner lists switches (an immersion heater,
say) under `free_power_loads` in config.yaml and PowerEngine turns each on when a session starts and off when it ends.

`FreeLoadState` is pure, one per switch: `step` is told whether free power is wanted now and what the switch reads, and
returns the action to take (or None). The app does the reading, the writing and the saving. It remembers what it did
(`held`), the same rules as the heating set-back (setback.py):

* it never turns on a switch that was already on, and never turns off one it did not turn on;
* if you turn the switch off yourself during the session, that choice stands for the rest of the session: it is not
  switched on again;
* a session is one run of `want`: the next one starts afresh.
"""

from __future__ import annotations

from dataclasses import dataclass

_UNREADABLE = ("", "unknown", "unavailable", "none")


@dataclass
class Action:
    service: str                        # "turn_on" or "turn_off"
    text: str                           # for the log


class FreeLoadState:
    def __init__(self, held: bool = False, seen: bool = False):
        self.held = held                # PowerEngine turned the switch on and has not yet turned it off
        self.seen = seen                # this session has been dealt with: once is enough

    def to_dict(self) -> dict:
        return {"held": self.held, "seen": self.seen}

    @classmethod
    def from_dict(cls, data) -> FreeLoadState:
        data = data if isinstance(data, dict) else {}
        return cls(bool(data.get("held")), bool(data.get("seen")))

    def step(self, want: bool, state: str | None) -> Action | None:
        cur = (state or "").strip().lower()
        if cur in _UNREADABLE:
            return None
        if want:
            if self.held and cur == "off":              # turned off during the session: that choice stands
                self.held = False
            if self.seen:
                return None
            self.seen = True
            if cur == "on":
                return None                             # already on, someone else's: leave it as it is
            self.held = True
            return Action("turn_on", "free power: switched on")
        self.seen = False
        if self.held:
            self.held = False
            if cur == "on":
                return Action("turn_off", "free power over: switched off")
        return None
