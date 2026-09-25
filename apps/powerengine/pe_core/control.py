"""Turning a decision into Solis timed-slot settings (the Active design), and working out what to write.

Pure: given the decision, the time and what the inverter's control entities currently hold, return the
settings PowerEngine wants and the writes needed to get there. The app only executes writes in Active mode;
in Passive mode the same result is published as a preview so the mapping can be checked first.

Design (spec: Active mode design):
- Storage mode stays Self-Use; windows do the work.
- Grid charge / hold: a charge window from now to the window end, charge current = power / battery voltage
  (0 A for hold). Force discharge / export: a discharge window and discharge current.
- Self-use: both windows closed (00:00-00:00), as the legacy automations do.
- Window end depends on the strategy: "rolling" = at most 35 minutes ahead, extended as it nears its end (a
  stopped PowerEngine leaves nothing running for long); "block" = the end of the plan's block (fewer writes;
  needs the HA watchdog automation). Windows never span midnight.
- Times are written as hour/minute numbers and applied with the update button (pre-FB00 firmware); currents
  and the mode apply directly. Only values that differ are written; the button is pressed once after them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .decide import EXPORT, FORCE_DISCHARGE, GRID_CHARGE, HOLD, Decision

SELF_USE_MODE = "Self-Use"
ROLLING_MINUTES = 35
ROLL_BEFORE_MINUTES = 5
STEP_A = 5
KINDS = {GRID_CHARGE: "charge", HOLD: "charge", FORCE_DISCHARGE: "discharge", EXPORT: "discharge"}
TIME_ROLES = {k: (f"timed_{k}_start_hour", f"timed_{k}_start_minute", f"timed_{k}_end_hour", f"timed_{k}_end_minute")
              for k in ("charge", "discharge")}


@dataclass
class Write:
    role: str
    value: object
    kind: str               # "number" | "select" | "button"

    def as_dict(self) -> dict:
        return {"role": self.role, "value": self.value, "kind": self.kind}


def window_end(now_local: datetime, current_end: datetime | None, strategy: str,
               block_end: datetime | None) -> datetime:
    """When the window should end (local time), never past midnight."""
    midnight = (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    last = midnight - timedelta(minutes=1)
    if strategy == "block" and block_end is not None and block_end > now_local:
        return min(block_end, last)
    if current_end is not None and now_local < current_end - timedelta(minutes=ROLL_BEFORE_MINUTES):
        return min(current_end, last)
    return min(now_local + timedelta(minutes=ROLLING_MINUTES), last)


def desired(decision: Decision, now_local: datetime, end_local: datetime | None, battery_v: float,
            max_charge_w: float, max_discharge_w: float) -> dict:
    """role -> value PowerEngine wants for this decision."""
    out: dict = {"storage_mode": SELF_USE_MODE}
    kind = KINDS.get(decision.action)
    for k in ("charge", "discharge"):
        sh, sm, eh, em = TIME_ROLES[k]
        if kind == k and end_local is not None:
            start = now_local - timedelta(minutes=1)
            out.update({sh: start.hour, sm: start.minute, eh: end_local.hour, em: end_local.minute})
        else:
            out.update({sh: 0, sm: 0, eh: 0, em: 0})              # closed
    if kind == "charge":
        w = 0.0 if decision.action == HOLD else (decision.power_w or max_charge_w)
        out["timed_charge_current"] = int(round(w / battery_v / STEP_A) * STEP_A)
    elif kind == "discharge":
        w = decision.power_w or max_discharge_w
        out["timed_discharge_current"] = int(round(w / battery_v / STEP_A) * STEP_A)
    return out


def _same(want, have) -> bool:
    try:
        return abs(float(want) - float(have)) < 0.5
    except (TypeError, ValueError):
        return str(want) == str(have)


def writes_needed(want: dict, have: dict) -> list[Write]:
    """The writes to go from `have` (role -> current state) to `want`: numbers/select that differ, then one press
    of the update button if any window time changed."""
    out: list[Write] = []
    times_changed = False
    for role, value in want.items():
        if _same(value, have.get(role)):
            continue
        if role == "storage_mode":
            out.append(Write(role, value, "select"))
        else:
            out.append(Write(role, value, "number"))
            if role.startswith("timed_") and not role.endswith("_current"):
                times_changed = True
    if times_changed:
        out.append(Write("timed_update_button", None, "button"))
    return out
