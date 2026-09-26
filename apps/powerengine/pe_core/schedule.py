"""Programming the inverter's three charge and three discharge windows from the plan (the "slots" strategy).

The plan's next charge periods (grid charge or hold) and discharge periods (sell or Axle) within 24 hours are set
into the inverter's timed windows in one go, so a normal night is written once and repeats without further writes.
A window is only rewritten when its period has passed and the slot is needed for another, or when the plan moves it
by more than TOLERANCE (periods starting within NEAR are always set exactly). Windows can't cross midnight, so a
period across it takes two slots. Charge windows share one current (hold = 0 A), discharge windows another; they're
set for whichever window is running, or the next one due, so a switch between hold and charge is one write.
If PowerEngine stops, the windows keep running as planned; the HA watchdog automation closes them after 15 minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .decide import EXPORT, FORCE_DISCHARGE, GRID_CHARGE, HOLD

SLOTS = 3
HORIZON = timedelta(hours=24)
TOLERANCE = timedelta(minutes=30)
NEAR = timedelta(hours=2)
CHARGE_ACTIONS = (GRID_CHARGE, HOLD)
DISCHARGE_ACTIONS = (EXPORT, FORCE_DISCHARGE)
CLOSED = (0, 0, 0, 0)


@dataclass
class Period:
    kind: str                  # "charge" | "discharge"
    start: datetime            # local
    end: datetime              # local
    first_action: str          # the action it starts with (hold or charge sets the charge current)
    power_w: float | None = None


def kind_of(action: str) -> str | None:
    if action in CHARGE_ACTIONS:
        return "charge"
    if action in DISCHARGE_ACTIONS:
        return "discharge"
    return None


def periods(plan_slots, now_local: datetime, tz, current_action: str | None = None) -> list[Period]:
    """Charge and discharge periods from now to 24 h ahead, split at local midnight. The half-hour in progress
    takes the live decision's action (overrides included)."""
    out: list[Period] = []
    limit = now_local + HORIZON
    for i, ps in enumerate(plan_slots):
        start = ps.slot.start.astimezone(tz)
        end = (ps.slot.start + timedelta(minutes=30)).astimezone(tz)
        if end <= now_local:
            continue
        if start >= limit:
            break
        action = current_action if (i == 0 or start <= now_local < end) and current_action else ps.action
        k = kind_of(action)
        if k is None:
            continue
        last = out[-1] if out else None
        if last and last.kind == k and last.end == start and start.date() == last.start.date() \
                and not (start.hour == 0 and start.minute == 0):
            last.end = end
            continue
        out.append(Period(k, start, end, action))
    for p in out:                      # windows can't reach midnight: end at 23:59
        if p.end.date() != p.start.date():
            p.end = p.end.replace(hour=0, minute=0) - timedelta(minutes=1)
    return out


def _tod(t: datetime) -> tuple[int, int]:
    return t.hour, t.minute


def window_of(p: Period) -> tuple[int, int, int, int]:
    return (*_tod(p.start), *_tod(p.end))


def _close(a: tuple, b: tuple) -> bool:
    def mins(h, m):
        return h * 60 + m
    return (abs(mins(a[0], a[1]) - mins(b[0], b[1])) <= TOLERANCE.seconds // 60
            and abs(mins(a[2], a[3]) - mins(b[2], b[3])) <= TOLERANCE.seconds // 60)


def assign(wanted: list[Period], programmed: list[tuple], now_local: datetime) -> list[tuple]:
    """New contents of the SLOTS windows of one kind. Keeps what's already programmed where it matches (exactly, or
    within the tolerance for periods starting more than NEAR ahead); fills free slots; closes the rest."""
    wanted = wanted[:SLOTS]
    result: list[tuple | None] = [None] * SLOTS
    todo = []
    for p in wanted:
        w = window_of(p)
        exact = next((i for i in range(SLOTS) if result[i] is None and tuple(programmed[i]) == w), None)
        loose = None
        if exact is None and p.start - now_local > NEAR:
            loose = next((i for i in range(SLOTS) if result[i] is None and tuple(programmed[i]) != CLOSED
                          and _close(tuple(programmed[i]), w)), None)
        i = exact if exact is not None else loose
        if i is None:
            todo.append(w)
        else:
            result[i] = tuple(programmed[i])
    for w in todo:                     # into the free slot needing the fewest changed values (fewest writes)
        free = [i for i in range(SLOTS) if result[i] is None]
        i = min(free, key=lambda k: (sum(1 for x, y in zip(programmed[k], w, strict=True) if x != y), k))
        result[i] = w
    return [r if r is not None else CLOSED for r in result]


def slot_entities(first: dict[str, str], exists) -> dict[int, dict[str, str]] | None:
    """{slot n: {role: entity}} for slots 1-3, from slot 1's entities with SolaX Modbus's '_2'/'_3' suffixes, or
    None if any of them is missing (then the single-window rolling strategy is used)."""
    out = {1: dict(first)}
    for n in (2, 3):
        m = {}
        for role, eid in first.items():
            e = f"{eid}_{n}"
            if not exists(e):
                return None
            m[role] = e
        out[n] = m
    return out


TIME_KEYS = {k: (f"timed_{k}_start_hour", f"timed_{k}_start_minute", f"timed_{k}_end_hour", f"timed_{k}_end_minute")
             for k in ("charge", "discharge")}
STEP_A = 5


def _amps(w: float, volts: float) -> int:
    return int(round(w / volts / STEP_A) * STEP_A)


def programmed(have: dict, kind: str) -> list[tuple]:
    """The windows of one kind as currently held (have: key -> value, keys 'role#n')."""
    out = []
    for n in range(1, SLOTS + 1):
        vals = []
        for role in TIME_KEYS[kind]:
            try:
                vals.append(int(float(have.get(f"{role}#{n}"))))
            except (TypeError, ValueError):
                vals.append(-1)                  # unknown: never matches, so it gets written
        out.append(tuple(vals))
    return out


def desired_state(pers: list[Period], have: dict, now_local: datetime, current_action: str | None,
                  current_power_w: float | None, volts: float, max_charge_w: float, max_discharge_w: float) -> dict:
    """key -> value wanted for all six windows, both currents and the storage mode."""
    from .control import SELF_USE_MODE
    want: dict = {"storage_mode": SELF_USE_MODE}
    for kind in ("charge", "discharge"):
        slots = assign([p for p in pers if p.kind == kind], programmed(have, kind), now_local)
        for n, w in enumerate(slots, start=1):
            for role, v in zip(TIME_KEYS[kind], w, strict=True):
                want[f"{role}#{n}"] = v
    cur_kind = kind_of(current_action) if current_action else None
    nxt = {k: next((p for p in pers if p.kind == k), None) for k in ("charge", "discharge")}
    if cur_kind == "charge":
        want["timed_charge_current"] = 0 if current_action == HOLD else _amps(current_power_w or max_charge_w, volts)
    elif nxt["charge"] is not None:
        want["timed_charge_current"] = 0 if nxt["charge"].first_action == HOLD else _amps(max_charge_w, volts)
    if cur_kind == "discharge":
        want["timed_discharge_current"] = _amps(current_power_w or max_discharge_w, volts)
    elif nxt["discharge"] is not None:
        want["timed_discharge_current"] = _amps(max_discharge_w, volts)
    return want


def writes_for(want: dict, have: dict):
    """Writes to go from have to want: values that differ, then each changed slot's update button."""
    from .control import Write, _same
    out, slots_changed = [], set()
    for key, value in want.items():
        if _same(value, have.get(key)):
            continue
        if key == "storage_mode":
            out.append(Write(key, value, "select"))
            continue
        out.append(Write(key, value, "number"))
        if "#" in key:
            slots_changed.add(int(key.split("#")[1]))
    for n in sorted(slots_changed):
        out.append(Write(f"timed_update_button#{n}", None, "button"))
    return out
