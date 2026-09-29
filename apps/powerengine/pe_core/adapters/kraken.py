"""The Kraken tariff adapter: rates, smart-charge dispatches, free-electricity sessions and the smart-charge request.

The attribute shapes here belong to the Home Assistant "Octopus Energy" integration (BottlecapDave), which also
serves EDF (both suppliers run on Kraken). `name` is "edf" or "octopus" and only changes what the supplier is called
(`display_names`); the parsing is the same.

The `read_*` methods are pure: each takes `state`, a function `role -> {"state": ..., "attributes": {...}} | None`
(the accessor `pe_core.readings.read()` builds from the config's mappings), and returns exactly what `read()` puts
in `Readings`. The `TariffAdapter` protocol methods are thin wrappers over them that read through the
`HomeAssistant` door using the entity mapped to each role. The policy (when to ask for slots, back-off, settling)
stays in `pe_core.smartcharge`; this class only says which entities and values a request writes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..parsing import State, Window, _is_on, _num, _rate, parse_time, parse_windows
from .base import Dispatch, GridEvent, HomeAssistant
from .registry import register
from .vocabulary import DISPATCH, GRID_EVENT, TARIFF

StateFn = Callable[[str], State | None]

SUPPLIERS = {
    "edf": {DISPATCH: "EDF smart slot", TARIFF: "EDF", GRID_EVENT: "EDF free-electricity session"},
    "octopus": {DISPATCH: "Octopus intelligent dispatch", TARIFF: "Octopus Energy",
                GRID_EVENT: "Octopus saving session"},
}

# the ready-by times offered when the entity doesn't list its own options (EDF currently offers mornings only)
DEFAULT_READY_BY_OPTIONS = [f"{h:02d}:{m:02d}" for h in range(4, 12) for m in (0, 30)][:15]


def supplier_of(entity_id: str | None) -> str:
    """"edf" if the entity comes from the EDF Energy integration, else "octopus"."""
    return "edf" if entity_id and "edf_energy" in entity_id else "octopus"


class KrakenTariff:
    """Reads a Kraken-based supplier's tariff data (Octopus Energy integration). See module docstring."""

    def __init__(self, name: str = "edf", role_entity: Callable[[str], str | None] | None = None):
        if name not in SUPPLIERS:
            raise ValueError(f"unknown Kraken supplier {name!r}; expected one of {sorted(SUPPLIERS)}")
        self.name = name
        self.role_entity = role_entity

    def display_names(self) -> dict[str, str]:
        return dict(SUPPLIERS[self.name])

    # --- pure readers (what read() puts into Readings) -----------------------------------

    @staticmethod
    def _attr(state: StateFn, role: str, name: str) -> Any:
        s = state(role)
        return ((s or {}).get("attributes") or {}).get(name)

    def read_import_rate(self, state: StateFn) -> float | None:
        return _rate(state("import_rate_now"))

    def read_export_rate(self, state: StateFn) -> float | None:
        return _rate(state("export_rate"))

    def read_standing_charge(self, state: StateFn) -> float | None:
        """GBP/day, converted from pence when the sensor says it is in pence."""
        sc = state("standing_charge")
        value = _num((sc or {}).get("state"))
        unit = ((sc or {}).get("attributes") or {}).get("unit_of_measurement")
        if value is not None and unit in ("p", "p/day"):
            value /= 100
        return value

    def read_rates(self, state: StateFn) -> list[Window]:
        """Today's and tomorrow's half-hourly import rates."""
        return (parse_windows(self._attr(state, "import_rates_today", "rates"))
                + parse_windows(self._attr(state, "import_rates_tomorrow", "rates")))

    def read_dispatches(self, state: StateFn) -> tuple[list[Window], list[Window]]:
        """(planned, completed) smart-charge dispatches; each window's value is `charge_in_kwh`."""
        return (parse_windows(self._attr(state, "smart_dispatches", "planned_dispatches"), ("charge_in_kwh",)),
                parse_windows(self._attr(state, "smart_dispatches", "completed_dispatches"), ("charge_in_kwh",)))

    def read_offpeak(self, state: StateFn) -> bool | None:
        off = state("offpeak_now")
        return _is_on(off) if off else None

    def read_free_sessions(self, state: StateFn) -> tuple[bool, datetime | None, datetime | None]:
        """(active now, next session start, next session end) for free-electricity sessions."""
        return (_is_on(state("free_power_active")),
                parse_time((state("free_power_next_start") or {}).get("state")),
                parse_time((state("free_power_next_end") or {}).get("state")))

    # --- the smart-charge request (which entities and values to write) -------------------

    @staticmethod
    def ready_by_options(state_all: dict | None) -> list[str]:
        """The ready-by times the entity offers (its `options` attribute), else the usual morning times."""
        return ((state_all or {}).get("attributes") or {}).get("options") or list(DEFAULT_READY_BY_OPTIONS)

    @staticmethod
    def ready_by_call(entity_id: str, value: str) -> tuple[str, dict]:
        """The service call that sets the car's ready-by time to `value` ("HH:MM")."""
        if entity_id.startswith("select."):
            return "select/select_option", {"entity_id": entity_id, "option": value}
        return "time/set_value", {"entity_id": entity_id, "time": f"{value}:00"}

    @staticmethod
    def charge_target_call(entity_id: str | None, current_state: Any) -> tuple[str, dict] | None:
        """The service call that puts the charge target back to 100%, or None if there is no such entity or it is
        already at 100."""
        if entity_id and str(current_state) not in ("100", "100.0"):
            return "number/set_value", {"entity_id": entity_id, "value": 100}
        return None

    # --- the TariffAdapter protocol (thin wrappers) --------------------------------------

    def _state_fn(self, ha: HomeAssistant) -> StateFn:
        if self.role_entity is None:
            raise RuntimeError("KrakenTariff needs a role_entity function to read through Home Assistant")

        def state(role: str) -> State | None:
            eid = self.role_entity(role)
            return ha.get_state(eid, attribute="all") if eid else None
        return state

    def import_rates(self, ha: HomeAssistant, now: datetime) -> list[Window]:
        return self.read_rates(self._state_fn(ha))

    def export_rate(self, ha: HomeAssistant, now: datetime) -> float | None:
        return self.read_export_rate(self._state_fn(ha))

    def dispatches(self, ha: HomeAssistant, now: datetime) -> list[Dispatch]:
        planned, _ = self.read_dispatches(self._state_fn(ha))
        return [Dispatch(w.start, w.end, abs(w.value) if w.value is not None else None) for w in planned]

    def grid_events(self, ha: HomeAssistant, now: datetime) -> list[GridEvent]:
        """Kraken's grid events come from the Axle adapter, not the tariff, so there are none here."""
        return []


register("tariff", "edf", lambda role_entity=None: KrakenTariff("edf", role_entity))
register("tariff", "octopus", lambda role_entity=None: KrakenTariff("octopus", role_entity))
