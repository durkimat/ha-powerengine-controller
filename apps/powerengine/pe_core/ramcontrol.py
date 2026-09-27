"""RAM remote control: driving the inverter through the Solis remote-control registers instead of timed windows.

Chosen on the config page (Inverter control: Control method). The SolaX Modbus "Battery control override" entities
set register 43135 (Off / Force charge / Force discharge) with the power in 43136 (charge) or 43129 (discharge).
These behave as a volatile command: tested on 27 Sep 2026 (S5-EH1P6K-L, firmware 420044), the inverter drops it and
goes back to Self-Use about 5 minutes after it stops being sent. So:

  - each decision maps to one command: charge -> Force charge at the planned power, hold -> Force charge at 0 W,
    sell / Axle -> Force discharge at the planned power, self-use -> Off;
  - a change is sent at once; while a force command is on it's re-sent every `refresh` (default 1 minute), well
    inside the inverter's timeout, so if PowerEngine, AppDaemon or HA stops, the inverter is back on Self-Use within
    about 5 minutes by itself;
  - the timed windows are closed once when this method takes over, and then left alone (no EEPROM writes);
  - PowerEngine checks the battery is following the command and reports it if not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .control import Write
from .decide import EXPORT, FORCE_DISCHARGE, GRID_CHARGE, HOLD
from .rctest import OPTION_CHARGE, OPTION_DISCHARGE, OPTION_OFF

METHODS = ("timed_windows", "ram_remote")
POWER_STEP_W = 100            # a power change smaller than this isn't re-sent early (the refresh carries it)
FOLLOW_GRACE = timedelta(seconds=90)     # after a change, time for the inverter to respond
FOLLOW_ALARM = timedelta(minutes=3)      # not following for this long: reported


@dataclass(frozen=True)
class Command:
    option: str                # Off / Force charge / Force discharge
    watts: int = 0

    @property
    def power_role(self) -> str | None:
        if self.option == OPTION_CHARGE:
            return "rc_charge_power"
        if self.option == OPTION_DISCHARGE:
            return "rc_discharge_power"
        return None

    def text(self) -> str:
        if self.option == OPTION_OFF:
            return "Off (Self-Use)"
        if self.option == OPTION_CHARGE and self.watts == 0:
            return "Hold (force charge at 0 W)"
        return f"{self.option} at {self.watts} W"

    def writes(self) -> list[Write]:
        """The power, the mode, then the power again (the adapter re-sends it a few seconds later too): on some
        firmware a power written just before or with the mode change doesn't take, and the inverter keeps the
        previous power."""
        out = []
        if self.power_role:
            out.append(Write(self.power_role, self.watts, "number"))
        out.append(Write("rc_mode", self.option, "select"))
        if self.power_role:
            out.append(Write(self.power_role, self.watts, "number"))
        return out


def command_for(action: str, power_w: float | None, max_charge_w: float, max_discharge_w: float) -> Command:
    if action == GRID_CHARGE:
        return Command(OPTION_CHARGE, int(round(min(power_w or max_charge_w, max_charge_w))))
    if action == HOLD:
        return Command(OPTION_CHARGE, 0)
    if action in (EXPORT, FORCE_DISCHARGE):
        return Command(OPTION_DISCHARGE, int(round(min(power_w or max_discharge_w, max_discharge_w))))
    return Command(OPTION_OFF, 0)


class RamController:
    def __init__(self):
        self.sent: Command | None = None
        self.sent_at: datetime | None = None
        self.changed_at: datetime | None = None
        self.bad_since: datetime | None = None
        self.follow = "n/a"
        self.changes: dict[str, int] = {}          # day -> command changes sent
        self.refreshes: dict[str, int] = {}        # day -> refreshes sent
        self.errors = 0

    def forget(self) -> None:
        """Something else touched remote control (a test, a restart): send the next command afresh."""
        self.sent, self.sent_at = None, None

    def step(self, now: datetime, want: Command, refresh: timedelta) -> tuple[list[Write], str | None]:
        """(writes, 'change' | 'refresh' | None) for this cycle."""
        s = self.sent
        if s is None or want.option != s.option or abs(want.watts - s.watts) >= POWER_STEP_W:
            return want.writes(), "change"
        if want.option != OPTION_OFF and (self.sent_at is None or now - self.sent_at >= refresh):
            return want.writes(), "refresh"
        return [], None

    def done(self, now: datetime, day: str, want: Command, why: str) -> None:
        if why == "change":
            self.changes[day] = self.changes.get(day, 0) + 1
            self.changed_at, self.bad_since = now, None
        else:
            self.refreshes[day] = self.refreshes.get(day, 0) + 1
        self.sent, self.sent_at = want, now
        for d in (self.changes, self.refreshes):
            for k in [k for k in d if k < day]:
                del d[k]

    def check_following(self, now: datetime, battery_w: float | None, soc: float | None,
                        floor_soc: float) -> str:
        """'ok', 'waiting', 'not following' or 'n/a'. battery_w is + discharging."""
        s = self.sent
        if s is None or s.option == OPTION_OFF or battery_w is None:
            self.bad_since, self.follow = None, "n/a"
            return self.follow
        if self.changed_at is not None and now - self.changed_at < FOLLOW_GRACE:
            self.follow = "waiting"
            return self.follow
        if s.option == OPTION_CHARGE and s.watts == 0:
            ok = battery_w <= 300                                  # hold: not discharging (solar may still charge)
        elif s.option == OPTION_CHARGE:
            ok = (soc is not None and soc >= 95) or s.watts < 300 or battery_w <= -0.5 * s.watts
        else:
            ok = (soc is not None and soc <= floor_soc + 5) or s.watts < 300 or battery_w >= 0.5 * s.watts
        if ok:
            self.bad_since = None
            self.follow = "ok"
        else:
            self.bad_since = self.bad_since or now
            self.follow = "not following" if now - self.bad_since >= FOLLOW_ALARM else "waiting"
        return self.follow
