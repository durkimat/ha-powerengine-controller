"""The Solis inverter adapter: translates neutral decisions into the timed-window settings the Solis
S5-EH1P6K-L (via SolaX Modbus) understands.

Pure translation only: this reads Home Assistant solely through the `HomeAssistant` protocol object it is
given (the app itself satisfies it), and does no logging, journalling or MQTT of its own. The app still owns
the journal, the write budget, dampening and the `_ctl` rolling-window state; this class only works out what
the inverter's registers should hold and what writes would get them there.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .. import ramcontrol
from ..control import KINDS, SELF_USE_MODE, desired, window_end, writes_needed
from ..control import release as _rolling_release
from ..decide import SELF_USE, Decision
from ..journal import is_staged
from ..rctest import OPTION_OFF, find_entities
from ..schedule import desired_state, slot_entities
from ..schedule import writes_for as _slot_writes_for
from .base import ControlMethod, HomeAssistant, InverterCapabilities
from .registry import register


@dataclass(frozen=True)
class SlotContext:
    """What `SolisInverter.writes_for` needs to work out the three-slot writes for a decision."""

    pers: list
    have: dict
    now_local: datetime
    max_charge_w: float
    max_discharge_w: float


class SolisInverter:
    """Translates decisions into Solis timed-window settings. See module docstring."""

    name = "solis"

    TIME_ROLES_1 = ("timed_charge_start_hour", "timed_charge_start_minute", "timed_charge_end_hour",
                    "timed_charge_end_minute", "timed_discharge_start_hour", "timed_discharge_start_minute",
                    "timed_discharge_end_hour", "timed_discharge_end_minute", "timed_update_button")

    def __init__(self, ha: HomeAssistant, role_entity, volts: float = 52.0,
                 max_charge_w: float = 6000, max_discharge_w: float = 6000, ram_max_w: float = 5000):
        self.ha = ha
        self.role_entity = role_entity
        self.volts = volts
        self.max_charge_w = max_charge_w
        self.max_discharge_w = max_discharge_w
        self.ram_max_w = ram_max_w
        self._slot_cache = None
        self._rc_cache = None

    # --- reading ---------------------------------------------------------------------------

    def slot_map(self, now: datetime) -> tuple[dict | None, bool]:
        """{slot n: {role: entity}} when all three inverter slots exist (SolaX '_2'/'_3' names), else None, and
        whether the app should log its "windows 2 and 3 not found" warning right now (once per loss, not on
        every recheck)."""
        first = {role: self.role_entity(role) for role in self.TIME_ROLES_1}
        if not all(first.values()):
            return None, False
        key = tuple(sorted(first.items()))
        cached = self._slot_cache
        if cached and cached[0] == key and (cached[1] is not None or (now - cached[2]).total_seconds() < 600):
            return cached[1], False
        m = slot_entities(first, lambda e: self.ha.get_state(e) is not None)
        warn = m is None and not (cached and cached[1] is None)
        self._slot_cache = (key, m, now)       # found: kept; not found: looked for again in 10 minutes
        return m, warn

    def slot_keys(self, slots: dict) -> dict:
        keys = {f"{role}#{n}": eid for n, m in slots.items() for role, eid in m.items()}
        for role in ("timed_charge_current", "timed_discharge_current", "storage_mode"):
            keys[role] = self.role_entity(role)
        return keys

    def read(self, entities: dict) -> dict:
        """Current state of `entities` (role/key -> entity id), skipping the update button (write-only)."""
        return {k: self.ha.get_state(e) for k, e in entities.items() if e and "update_button" not in k}

    # --- RAM remote control -----------------------------------------------------------------

    def rc_entities(self, now: datetime) -> dict:
        """SolaX Modbus's remote-control entities, found by name (looked up at most every 10 minutes, kept only
        once all three are found)."""
        cached = self._rc_cache
        if cached and now - cached[0] < timedelta(minutes=10) and len(cached[1]) == 3:
            return cached[1]
        try:
            ids = list((self.ha.get_state() or {}).keys())
        except Exception:
            ids = []
        found = find_entities(ids)
        self._rc_cache = (now, found)
        return found

    def rc_missing(self, rc: dict) -> list[str]:
        """Which of the three RC roles weren't found."""
        return [r for r in ("rc_mode", "rc_charge_power", "rc_discharge_power") if not rc.get(r)]

    def ram_command(self, action: str, power_w: float | None, max_charge_w: float, max_discharge_w: float,
                    cap_w: float | None = None) -> ramcontrol.Command:
        return ramcontrol.command_for(action, power_w, max_charge_w, max_discharge_w, cap_w)

    def ram_off_command(self) -> ramcontrol.Command:
        return ramcontrol.Command(OPTION_OFF)

    def service_for(self, write) -> tuple[str, dict]:
        """The HA service call for one write: select or number, never a button (RAM control has none)."""
        kind = write.kind if hasattr(write, "kind") else write["kind"]
        value = write.value if hasattr(write, "value") else write["value"]
        if kind == "select":
            return "select/select_option", {"option": value}
        return "number/set_value", {"value": value}

    # --- three-slot strategy ----------------------------------------------------------------

    def slot_writes(self, pers: list, have: dict, now_local: datetime, action: str, power_w: float | None,
                     max_charge_w: float, max_discharge_w: float) -> tuple[dict, list]:
        want = desired_state(pers, have, now_local, action, power_w, self.volts, max_charge_w, max_discharge_w)
        return want, _slot_writes_for(want, have)

    def release_slots(self, entities: dict, have: dict) -> list:
        """The writes that close all three charge/discharge windows and set Self-Use."""
        want = {k: 0 for k in entities if "#" in k and "update_button" not in k}
        want["storage_mode"] = SELF_USE_MODE
        return _slot_writes_for(want, have)

    # --- rolling single-window strategy ------------------------------------------------------

    def rolling(self, decision: Decision, now_local: datetime, current_end: datetime | None,
                block_end: datetime | None, strategy: str, max_charge_w: float,
                max_discharge_w: float) -> tuple[str | None, datetime | None, dict]:
        kind = KINDS.get(decision.action)
        end = window_end(now_local, current_end, strategy, block_end) if kind else None
        want = desired(decision, now_local, end, self.volts, max_charge_w, max_discharge_w)
        return kind, end, want

    def release_rolling(self) -> dict:
        return _rolling_release()

    # --- confirming writes -------------------------------------------------------------------

    def confirmed(self, entity_id: str, value: Any) -> bool:
        """Whether `entity_id` has read back `value` (HA sometimes reports a number as "N.0")."""
        state = self.ha.get_state(entity_id)
        return str(state) in (str(value), f"{value}.0")

    def verify(self, writes: list, ha: HomeAssistant, entities: dict | None = None) -> list[str]:
        """Read back `writes` from `ha`; return one message per mismatch. `entities` resolves a write's role to
        its entity id (needed for slot keys like "timed_charge_start_hour#2"); without it, `role_entity` is used
        (the rolling window's roles, which map onto entities directly)."""
        msgs = []
        for w in writes:
            role = w.role if hasattr(w, "role") else w["role"]
            kind = w.kind if hasattr(w, "kind") else w["kind"]
            value = w.value if hasattr(w, "value") else w["value"]
            if kind == "button":
                continue
            entity = entities.get(role) if entities is not None else self.role_entity(role)
            if not entity:
                continue
            state = ha.get_state(entity)
            if str(state) not in (str(value), f"{value}.0"):
                msgs.append(f"{role} did not read back (wanted {value}, have {state})")
        return msgs

    def write_storage(self, write) -> str:
        """"staged" for window times SolaX Modbus keeps in HA until the update button sends them, else
        "eeprom" (a real inverter write, including a button press)."""
        role = write.role if hasattr(write, "role") else write["role"]
        return "staged" if is_staged(role) else "eeprom"

    # --- capabilities / protocol conformance --------------------------------------------------

    def capabilities(self) -> InverterCapabilities:
        return InverterCapabilities(
            methods=(
                ControlMethod(name="ram_remote", storage="ram", failsafe_min=5, max_power_w=self.ram_max_w),
                ControlMethod(name="timed_windows", storage="eeprom", failsafe_min=None, max_power_w=None),
            ),
            actions=frozenset(KINDS) | {SELF_USE},
            max_charge_w=self.max_charge_w,
            max_discharge_w=self.max_discharge_w,
            never_touch=("Backup", "Off-Grid"),
        )

    def writes_for(self, decision: Decision, now: datetime, context: SlotContext) -> list:
        """Protocol conformance: the three-slot writes for `decision` given `context`. Not used by the app yet
        (which calls `slot_writes`/`rolling` directly, since it needs the `want` dict too, for the diagnostics
        display)."""
        _want, writes = self.slot_writes(context.pers, context.have, context.now_local, decision.action,
                                         decision.power_w, context.max_charge_w, context.max_discharge_w)
        return writes

    def release(self) -> list:
        """Protocol conformance: the writes that hand the inverter back to Self-Use via the rolling window's
        settings, from an empty `have` (so every field is written). Not used by the app yet, which calls
        `release_slots`/`release_rolling` directly since it has real `have` state to diff against."""
        return writes_needed(self.release_rolling(), {})


register("inverter", "solis", SolisInverter)

__all__ = ["SolisInverter", "SlotContext"]
