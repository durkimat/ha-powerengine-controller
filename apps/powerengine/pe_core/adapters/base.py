"""Protocols and data shapes for the adapter layer.

`pe_core` decides what to do in neutral terms (a `Decision`, a set of action names, plain readings). Adapters
translate those neutral terms into and out of a specific piece of hardware or a specific supplier's API. Nothing
here talks to Home Assistant or AppDaemon directly except through the `HomeAssistant` protocol below, so adapters
stay testable without either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class HomeAssistant(Protocol):
    """The only door an adapter has into Home Assistant. Nothing here talks to AppDaemon directly."""

    def get_state(self, entity_id: str, attribute: str | None = None) -> Any:
        """Read an entity's state, or one of its attributes when `attribute` is given."""
        ...

    def call_service(self, service: str, **data: Any) -> Any:
        """Call an HA service (e.g. "number/set_value") with the given data."""
        ...


@dataclass(frozen=True)
class ControlMethod:
    """One way an inverter can be told what to do, in order of preference within `InverterCapabilities`."""

    name: str                      # e.g. "timed_windows", "ram_remote"
    storage: str                   # "eeprom" | "ram" | "cloud"
    failsafe_min: float | None     # minutes until the inverter reverts to self-use on its own, None if it doesn't
    max_power_w: float | None      # register cap for this method, None if uncapped


@dataclass(frozen=True)
class InverterCapabilities:
    """What one inverter can do: its control methods, the actions it supports, and what it must never do."""

    methods: tuple[ControlMethod, ...]     # in order of preference
    actions: frozenset[str]                # subset of pe_core.decide's action names
    max_charge_w: float
    max_discharge_w: float
    never_touch: tuple[str, ...] = ()      # modes/options never to select, e.g. ("Backup", "Off-Grid")

    def method(self, name: str) -> ControlMethod | None:
        """The named control method, or None if this inverter doesn't have it."""
        for m in self.methods:
            if m.name == name:
                return m
        return None


@runtime_checkable
class InverterAdapter(Protocol):
    """Translates a neutral `Decision` into the writes one inverter needs, and reads its capabilities back."""

    name: str

    def capabilities(self) -> InverterCapabilities:
        """What this inverter can do."""
        ...

    def writes_for(self, decision: Any, now: datetime, context: Any) -> list:
        """The writes that would carry out `decision` right now. Pure: no HA calls, just `pe_core.control.Write`s."""
        ...

    def release(self) -> list:
        """The writes that hand the inverter back to self-use."""
        ...

    def verify(self, writes: list, ha: HomeAssistant) -> list[str]:
        """Read back `writes` from HA; return one message per mismatch, or an empty list if all took."""
        ...

    def write_storage(self, write: Any) -> str:
        """What writing this costs: "eeprom" (wears out), "ram" (cheap), or "staged" (not sent yet)."""
        ...


@dataclass(frozen=True)
class Dispatch:
    """A pre-agreed cheap-charging window from a supplier (EDF "smart slot", Octopus "intelligent dispatch")."""

    start: datetime
    end: datetime
    kwh: float | None = None
    for_car: bool = True


@dataclass(frozen=True)
class GridEvent:
    """A supplier- or aggregator-run event to shift load (Axle export event, a saving session)."""

    start: datetime
    end: datetime
    direction: str                  # "export" | "import_reduction"
    value_per_kwh: float | None = None


@runtime_checkable
class TariffAdapter(Protocol):
    """Reads a supplier's rates, dispatches and grid events, and says what that supplier calls each of them."""

    name: str

    def display_names(self) -> dict[str, str]:
        """Vocabulary term -> this supplier's own name for it, e.g. {"dispatch": "EDF smart slot"}."""
        ...

    def import_rates(self, ha: HomeAssistant, now: datetime) -> list:
        """Upcoming import-rate windows, as a list of `pe_core.readings.Window`."""
        ...

    def export_rate(self, ha: HomeAssistant, now: datetime) -> float | None:
        """The current export rate (GBP/kWh), or None if there isn't a flat one."""
        ...

    def dispatches(self, ha: HomeAssistant, now: datetime) -> list[Dispatch]:
        """Upcoming pre-agreed cheap-charging windows."""
        ...

    def grid_events(self, ha: HomeAssistant, now: datetime) -> list[GridEvent]:
        """Upcoming supplier/aggregator events (export or import-reduction)."""
        ...


@dataclass(frozen=True)
class EVState:
    """A car charger's state, in neutral terms."""

    plugged: bool
    charging: bool
    complete: bool
    power_w: float | None = None


@runtime_checkable
class EVAdapter(Protocol):
    """Reads one EV charger's state in neutral terms."""

    name: str

    def display_names(self) -> dict[str, str]:
        """Vocabulary term -> this charger's own name for it."""
        ...

    def state(self, ha: HomeAssistant) -> EVState:
        """The charger's current state."""
        ...


@dataclass(frozen=True)
class ForecastPoint:
    """One half-hourly point of a generation forecast, with an optional low/high band."""

    start: datetime
    kwh: float
    low_kwh: float | None = None
    high_kwh: float | None = None


@runtime_checkable
class ForecastAdapter(Protocol):
    """Reads a generation forecast in neutral terms."""

    name: str

    def half_hourly(self, ha: HomeAssistant, day: date) -> list[ForecastPoint]:
        """Half-hourly forecast points covering `day`."""
        ...


__all__ = [
    "HomeAssistant",
    "ControlMethod",
    "InverterCapabilities",
    "InverterAdapter",
    "Dispatch",
    "GridEvent",
    "TariffAdapter",
    "EVState",
    "EVAdapter",
    "ForecastPoint",
    "ForecastAdapter",
]
