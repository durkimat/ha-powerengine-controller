"""The battery's own limits (BMS charge and discharge current limits) and what they mean for a remote-control command.

The Solis force-power registers take up to 5000 W, but the battery can take or give less: when it is cold, or nearly
full or empty, its BMS lowers the current it allows (SolaX Modbus: `sensor.solis_bms_battery_charge_limit`, normally
100 A). Asking for more than that is pointless, and a follow check that judges the battery against the command would
call a BMS-limited charge "not following". Everything here is pure; the app reads the states and passes them in.

Rules (the sensors are optional; with none mapped nothing here changes a command):

  - a limit in amps becomes watts with the battery voltage (the voltage sensor if mapped and plausible, else the
    nominal voltage the app uses elsewhere), rounded down to 100 W;
  - an unmapped, unavailable, unknown, non-numeric, negative, infinite or absurd (over 1000 A) limit is ignored: no
    cap from it. If the charge limit is ignored and the battery is cold by the cold-battery caution, that caution's
    charge factor caps the charge instead (the plan assumed that rate already);
  - a limit of exactly 0 is believed (a BMS does hold charging at 0 A when the pack is full or too cold): a charge
    command becomes Hold (force charge at 0 W), a discharge command becomes Off (Self-Use), never a 0 W force
    discharge. Unless the battery is visibly moving power that way (over 300 W), which contradicts a 0 limit: then the
    reading is treated as garbage and ignored.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .ramcontrol import Command
from .rctest import OPTION_CHARGE, OPTION_DISCHARGE, OPTION_OFF

MAX_PLAUSIBLE_A = 1000.0
PLAUSIBLE_V = (20.0, 120.0)
STEP_W = 100
CONTRADICTION_W = 300          # a zero limit is not believed while the battery moves this much power that way
BAD_WORDS = ("", "unavailable", "unknown", "none", "nan", "inf", "-inf")


def parse_amps(value) -> float | None:
    """A limit in amps from a state value, or None for anything that is not a sane, non-negative number."""
    if value is None or str(value).strip().lower() in BAD_WORDS:
        return None
    try:
        a = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(a) or a < 0 or a > MAX_PLAUSIBLE_A:
        return None
    return a


def parse_volts(value, nominal: float) -> tuple[float, str]:
    """(volts, 'sensor' | 'nominal'): the battery voltage if the sensor gives a plausible one, else the nominal."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return nominal, "nominal"
    if not math.isfinite(v) or not PLAUSIBLE_V[0] <= v <= PLAUSIBLE_V[1]:
        return nominal, "nominal"
    return v, "sensor"


def amps_to_w(amps: float, volts: float) -> float:
    """Watts for a current limit, rounded down to a whole STEP_W so a limit that wobbles does not re-send."""
    return math.floor(amps * volts / STEP_W) * STEP_W


@dataclass(frozen=True)
class Limits:
    """What the battery will take / give now, in watts (None: no limit known, so none applied)."""
    charge_w: float | None = None
    discharge_w: float | None = None
    charge_a: float | None = None
    discharge_a: float | None = None
    volts: float | None = None
    volts_source: str = "nominal"
    charge_source: str = "none"      # "bms" | "cold caution" | "none"
    discharge_source: str = "none"   # "bms" | "none"

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def read_limits(charge_value, discharge_value, volts_value, nominal_v: float, battery_w: float | None = None,
                cold_charge_w: float | None = None) -> Limits:
    """charge_value / discharge_value / volts_value: the raw states (None if not mapped). battery_w is + discharging.
    cold_charge_w: the charge power the cold-battery caution allows right now (None: not cold or caution off), used
    only when the BMS charge limit is unusable."""
    volts, vsrc = parse_volts(volts_value, nominal_v)
    c_a, d_a = parse_amps(charge_value), parse_amps(discharge_value)
    if c_a == 0 and battery_w is not None and battery_w <= -CONTRADICTION_W:
        c_a = None                                   # "0 A" while it is charging: the reading is not to be believed
    if d_a == 0 and battery_w is not None and battery_w >= CONTRADICTION_W:
        d_a = None
    c_w = amps_to_w(c_a, volts) if c_a is not None else None
    d_w = amps_to_w(d_a, volts) if d_a is not None else None
    c_src = "bms" if c_w is not None else "none"
    if c_w is None and cold_charge_w is not None and cold_charge_w > 0:
        c_w, c_src = math.floor(cold_charge_w / STEP_W) * STEP_W, "cold caution"
    return Limits(c_w, d_w, c_a, d_a, round(volts, 1), vsrc, c_src, "bms" if d_w is not None else "none")


def limit_for(cmd: Command, limits: Limits | None) -> float | None:
    """The limit in watts that applies to this command's direction (None: none known)."""
    if limits is None:
        return None
    if cmd.option == OPTION_CHARGE:
        return limits.charge_w
    if cmd.option == OPTION_DISCHARGE:
        return limits.discharge_w
    return None


def cap_command(cmd: Command, limits: Limits | None) -> tuple[Command, str | None]:
    """(the command the battery can take, a short reason or None when nothing was capped). A zero charge limit gives
    Hold, a zero discharge limit gives Off (see the module text)."""
    lim = limit_for(cmd, limits)
    if lim is None or cmd.watts <= lim:
        return cmd, None
    src = limits.charge_source if cmd.option == OPTION_CHARGE else limits.discharge_source
    why = f"limited to {int(lim)} W by the {'battery (BMS)' if src == 'bms' else src}"
    if lim <= 0:
        if cmd.option == OPTION_CHARGE:
            return Command(OPTION_CHARGE, 0), why.replace("limited to 0 W", "no charge allowed")
        return Command(OPTION_OFF, 0), why.replace("limited to 0 W", "no discharge allowed")
    return Command(cmd.option, int(lim)), why


def expected_w(cmd: Command | None, limits: Limits | None) -> float | None:
    """The power the battery should really be doing for what was sent: the smaller of the command and its limit.
    None for Off or nothing sent."""
    if cmd is None or cmd.option == OPTION_OFF:
        return None
    lim = limit_for(cmd, limits)
    return float(cmd.watts if lim is None else min(cmd.watts, lim))


@dataclass
class SampleRing:
    """A short history for the diagnostics export: command, expected and actual battery power, the BMS limits. One
    row every `every` (and whenever the command changes), so a first cold spell can be read back afterwards."""
    size: int = 180
    every: timedelta = field(default_factory=lambda: timedelta(minutes=2))
    rows: deque = field(default_factory=deque)
    last_at: datetime | None = None
    last_cmd: str | None = None

    def __post_init__(self):
        self.rows = deque(self.rows, maxlen=self.size)

    def add(self, now: datetime, cmd_text: str, row: dict) -> bool:
        if self.last_at is not None and cmd_text == self.last_cmd and now - self.last_at < self.every:
            return False
        self.rows.append({"t": now.isoformat(timespec="seconds"), "command": cmd_text, **row})
        self.last_at, self.last_cmd = now, cmd_text
        return True

    def as_list(self) -> list[dict]:
        return list(self.rows)
