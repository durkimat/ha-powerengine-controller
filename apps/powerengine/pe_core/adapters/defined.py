"""A generic inverter driver, configured by a definition file (`devices/<name>.yml`).

`DefinedInverter` implements the `InverterAdapter` protocol (and the wider surface the app uses: slot map, reads,
RAM remote control, supervised tests, the clock) from a `Definition`. What is data lives in the file: entity
patterns, option names, slot layout, limits, staged parts, the clock roles, display names, capabilities. What is
algorithm stays in Python and is chosen by name in the file (`behaviour:`):

  timed_slots  timed_hhmm      window times as hour/minute numbers with an update button
                               (pe_core/control.py: the rolling window; pe_core/schedule.py: the three-slot plan)
  ram          override_select a mode select plus force-power numbers (pe_core/ramcontrol.py: the command for a
                               decision, the refresh and the following check; pe_core/rctest.py: the tests)
  clock        drift_button    the clock sensor's drift and a sync button (pe_core/clock.py)

Pure translation only, like the adapter it replaces: it reads Home Assistant solely through the `HomeAssistant`
object it is given and does no logging, journalling or MQTT. The neutral role names ("timed_charge_start_hour",
"rc_mode", "storage_mode" ...) are the app's own vocabulary and are the same for every inverter; a definition says
which entities and which option words they stand for on its inverter.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from .. import clock, ramcontrol
from ..control import KINDS, SELF_USE_MODE, Write, desired, window_end, writes_needed
from ..control import release as _rolling_release
from ..decide import Decision
from ..rctest import find_entities, missing_roles
from ..schedule import desired_state, slot_entities
from ..schedule import writes_for as _slot_writes_for
from ..testwrite import decision as _test_decision
from ..testwrite import end_time as _test_end_time
from .base import ControlMethod, HomeAssistant, InverterCapabilities
from .definition import RC_ROLES, Definition, load_definition

STORAGE_MODE = "storage_mode"           # the neutral role of the inverter's storage-mode select
RAM, TIMED = "ram_remote", "timed_windows"


@dataclass(frozen=True)
class SlotContext:
    """What `writes_for` needs to work out the three-slot writes for a decision."""

    pers: list
    have: dict
    now_local: datetime
    max_charge_w: float
    max_discharge_w: float


class DefinedInverter:
    """Translates decisions into one inverter's settings, as its definition file describes them."""

    def __init__(self, definition: Definition, ha: HomeAssistant, role_entity, volts: float = 52.0,
                 max_charge_w: float | None = None, max_discharge_w: float | None = None,
                 ram_max_w: float | None = None):
        self.definition = definition
        d = definition.data
        self.d = d
        self.name = d["name"]
        self.card_model = d["card_model"]       # the Sunsynk Power Flow Card's "inverter: model:" key
        self.ha = ha
        self.role_entity = role_entity
        self.volts = volts
        caps = d["capabilities"]
        self.max_charge_w = caps["max_charge_w"] if max_charge_w is None else max_charge_w
        self.max_discharge_w = caps["max_discharge_w"] if max_discharge_w is None else max_discharge_w
        ram = d.get("ram") or {}
        self.ram_max_w = ram.get("max_power_w") if ram_max_w is None else ram_max_w
        self._slots = d.get("timed_slots") or {}
        self._ram = ram
        self._clock = d.get("clock") or {}
        self._slot_cache = None
        self._rc_cache = None

    # --- reading ---------------------------------------------------------------------------

    def slot_map(self, now: datetime) -> tuple[dict | None, bool]:
        """{slot n: {role: entity}} when all the inverter's timed slots exist (slot n's entities are slot 1's plus
        the definition's suffix), else None, and whether the app should log its "windows 2 and 3 not found" warning
        right now (once per loss, not on every recheck)."""
        if not self._slots:
            return None, False
        first = {role: self.role_entity(role) for role in self._slots["first_slot_roles"]}
        if not all(first.values()):
            return None, False
        key = tuple(sorted(first.items()))
        cached = self._slot_cache
        recheck = self._slots["recheck_seconds"]
        if cached and cached[0] == key and (cached[1] is not None or (now - cached[2]).total_seconds() < recheck):
            return cached[1], False
        m = slot_entities(first, lambda e: self.ha.get_state(e) is not None, self._slots["count"],
                          self._slots["suffix"])
        warn = m is None and not (cached and cached[1] is None)
        self._slot_cache = (key, m, now)       # found: kept; not found: looked for again after `recheck`
        return m, warn

    def slot_keys(self, slots: dict) -> dict:
        keys = {f"{role}#{n}": eid for n, m in slots.items() for role, eid in m.items()}
        for role in (*self._slots["currents"], STORAGE_MODE):
            keys[role] = self.role_entity(role)
        return keys

    def read(self, entities: dict) -> dict:
        """Current state of `entities` (role/key -> entity id), skipping the write-only update button."""
        skip = self._slots.get("write_only_match")
        return {k: self.ha.get_state(e) for k, e in entities.items() if e and not (skip and skip in k)}

    # --- RAM remote control -----------------------------------------------------------------

    def rc_entities(self, now: datetime) -> dict:
        """The remote-control entities, found by name (looked up at most every `lookup_minutes`, kept only once
        all of them are found)."""
        if not self._ram:
            return {}
        cached = self._rc_cache
        if cached and now - cached[0] < timedelta(minutes=self._ram["lookup_minutes"]) \
                and len(cached[1]) == len(RC_ROLES):
            return cached[1]
        try:
            ids = list((self.ha.get_state() or {}).keys())
        except Exception:
            ids = []
        spec = {role: (e["domain"], e["tail"]) for role, e in self._ram["entities"].items()}
        found = find_entities(ids, spec, self._ram["prefer"])
        self._rc_cache = (now, found)
        return found

    def rc_missing(self, rc: dict) -> list[str]:
        """Which of the RC roles weren't found."""
        return [r for r in RC_ROLES if not rc.get(r)]

    def ram_command(self, action: str, power_w: float | None, max_charge_w: float, max_discharge_w: float,
                    cap_w: float | None = None) -> ramcontrol.Command:
        return ramcontrol.command_for(action, power_w, max_charge_w, max_discharge_w, cap_w)

    def ram_off_command(self) -> ramcontrol.Command:
        return ramcontrol.Command("Off")

    def _real_option(self, option: str) -> str:
        """The inverter's own name for one of the app's RC options (the same word on Solis)."""
        return self._ram.get("options", {}).get(option, option)

    def service_for(self, write) -> tuple[str, dict]:
        """The HA service call for one write: select or number, never a button (RAM control has none). A remote-
        control mode goes out under the inverter's own option name."""
        role = write.role if hasattr(write, "role") else write["role"]
        kind = write.kind if hasattr(write, "kind") else write["kind"]
        value = write.value if hasattr(write, "value") else write["value"]
        if kind == "select":
            return "select/select_option", {"option": self._real_option(value) if role == "rc_mode" else value}
        return "number/set_value", {"value": value}

    # --- supervised test writes -------------------------------------------------------------

    def test_roles(self) -> list[str]:
        """Every control role a supervised test (timed-window or RC) needs mapped."""
        return list(self._slots["test_roles"])

    def test_window(self, req: dict, now_local: datetime, max_c: float, max_d: float) -> dict:
        """The timed-window settings for a supervised (non-RC) test."""
        return self._localise(desired(_test_decision(req), now_local, _test_end_time(now_local, req["minutes"]),
                                      self.volts, max_c, max_d))

    def rc_test_problem(self, rc: dict, action: str) -> str | None:
        """Why an RC test can't start: entities not found, or the mode select doesn't offer the option."""
        gone = missing_roles(rc, action)
        if gone:
            return "remote-control entities not found in HA: " + ", ".join(gone)
        opts = self.ha.get_state(rc.get("rc_mode"), attribute="options") if rc.get("rc_mode") else None
        option = self._real_option(self._ram["tests"][action])
        if isinstance(opts, list) and option not in opts:
            return f"{rc['rc_mode']} has no '{option}' option"
        return None

    def rc_test_writes(self, test: str, power: float) -> tuple[str, str, list]:
        """(power role, option, writes) to start an RC test: the force power, then the mode."""
        roles = self._ram["power_roles"]
        prole = roles["discharge"] if test == "rc_discharge" else roles["charge"]
        option = self._ram["tests"][test]
        return prole, option, [Write(prole, power, "number"), Write("rc_mode", option, "select")]

    # --- inverter clock ----------------------------------------------------------------------

    def clock_entities(self) -> tuple[str | None, str | None]:
        if not self._clock:
            return None, None
        return self.role_entity(self._clock["clock_role"]), self.role_entity(self._clock["sync_role"])

    def clock_status(self, now: datetime, tz) -> dict:
        """drift (s, + = inverter ahead), when it was last synced, whether a sync is due, and the raw reading."""
        eid, button = self.clock_entities()
        st = (self.ha.get_state(eid, attribute="all") or {}) if eid else {}
        read_at = st.get("last_updated") or st.get("last_changed")
        read_at = datetime.fromisoformat(read_at) if read_at else None
        drift = clock.drift_seconds(st.get("state"), read_at, tz)
        synced = clock.last_sync(self.ha.get_state(button), tz) if button else None
        due = clock.sync_due(drift, synced, now)
        return {"drift": drift, "synced": synced, "due": due, "inverter_time": st.get("state")}

    def clock_sync_write(self) -> Write:
        return Write(self._clock["sync_role"], None, "button")

    # --- the storage-mode word ----------------------------------------------------------------

    def _self_use(self) -> str:
        return self._slots.get("self_use_option", SELF_USE_MODE)

    def _localise(self, want: dict) -> dict:
        """The plain-Self-Use storage mode under this inverter's own option name (a no-op on Solis)."""
        if self._self_use() != SELF_USE_MODE and want.get(STORAGE_MODE) == SELF_USE_MODE:
            want = dict(want, **{STORAGE_MODE: self._self_use()})
        return want

    def _localise_writes(self, writes: list) -> list:
        if self._self_use() == SELF_USE_MODE:
            return writes
        return [replace(w, value=self._self_use()) if w.role == STORAGE_MODE and w.value == SELF_USE_MODE else w
                for w in writes]

    # --- three-slot strategy ----------------------------------------------------------------

    def slot_writes(self, pers: list, have: dict, now_local: datetime, action: str, power_w: float | None,
                    max_charge_w: float, max_discharge_w: float) -> tuple[dict, list]:
        want = self._localise(desired_state(pers, have, now_local, action, power_w, self.volts, max_charge_w,
                                            max_discharge_w))
        return want, _slot_writes_for(want, have)

    def release_slots(self, entities: dict, have: dict) -> list:
        """The writes that close all the charge/discharge windows and set Self-Use."""
        skip = self._slots["write_only_match"]
        want = {k: 0 for k in entities if "#" in k and skip not in k}
        want[STORAGE_MODE] = self._self_use()
        return _slot_writes_for(want, have)

    # --- rolling single-window strategy ------------------------------------------------------

    def rolling(self, decision: Decision, now_local: datetime, current_end: datetime | None,
                block_end: datetime | None, strategy: str, max_charge_w: float,
                max_discharge_w: float) -> tuple[str | None, datetime | None, dict]:
        kind = KINDS.get(decision.action)
        end = window_end(now_local, current_end, strategy, block_end) if kind else None
        want = self._localise(desired(decision, now_local, end, self.volts, max_charge_w, max_discharge_w))
        return kind, end, want

    def release_rolling(self) -> dict:
        return self._localise(_rolling_release())

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
        """"staged" for window times the integration keeps in HA until the update button sends them, else
        "eeprom" (a real inverter write, including a button press)."""
        role = write.role if hasattr(write, "role") else write["role"]
        s = (role or "").lower()
        return "staged" if any(part in s for part in self._slots.get("staged_parts", ())) else "eeprom"

    # --- capabilities / protocol conformance --------------------------------------------------

    def display_names(self) -> dict[str, str]:
        return dict(self.d["display_names"])

    def capabilities(self) -> InverterCapabilities:
        caps = self.d["capabilities"]
        methods = []
        if caps["supports_ram"]:                                    # RAM first: the preferred method
            methods.append(ControlMethod(name=RAM, storage=self._ram.get("storage", "ram"),
                                         failsafe_min=self._ram["failsafe_min"], max_power_w=self.ram_max_w))
        if caps["supports_timed_slots"]:
            methods.append(ControlMethod(name=TIMED, storage=self._slots.get("storage", "eeprom"),
                                         failsafe_min=None, max_power_w=None))
        return InverterCapabilities(methods=tuple(methods), actions=frozenset(caps["actions"]),
                                    max_charge_w=self.max_charge_w, max_discharge_w=self.max_discharge_w,
                                    never_touch=tuple(caps["never_touch"]))

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
        return self._localise_writes(writes_needed(self.release_rolling(), {}))


def defined_factory(name: str):
    """A registry factory for the inverter definition called `name`: `factory(ha, role_entity, ..., firmware=None)`."""
    def factory(ha, role_entity, *args, firmware: str | None = None, **kwargs):
        return DefinedInverter(load_definition(name, firmware), ha, role_entity, *args, **kwargs)
    factory.__name__ = f"{name}_inverter"
    return factory


__all__ = ["DefinedInverter", "SlotContext", "defined_factory"]
