"""Neutral terms the core uses for things that differ by supplier or hardware, and their default English names.

An adapter can say what its own brand calls a term (see `TariffAdapter.display_names` and `EVAdapter.display_names`
in `base.py`); `display()` falls back to a sensible default, and then to the term itself, so the core never has to
know whether an adapter is even present.
"""

from __future__ import annotations

DISPATCH = "dispatch"          # a pre-agreed cheap-charging window (EDF "smart slot", Octopus "intelligent dispatch")
GRID_EVENT = "grid_event"      # a supplier/aggregator event to shift load (Axle event, a saving session)
TARIFF = "tariff"              # the electricity supplier/contract
EV_CHARGER = "ev_charger"      # the car charger
INVERTER = "inverter"          # the battery inverter
SUPPLIER = "supplier"          # the electricity supplier's short name ("EDF")
DISPATCH_SHORT = "dispatch_short"   # a smart-charge slot in a few words ("EDF slot")
SMART_CHARGE = "smart_charge"  # the supplier's smart-charging service ("EDF smart charge")
EVENT_SOURCE = "event"         # who runs grid events, short ("Axle")
FORECAST = "forecast"          # the generation forecast provider

DEFAULT_NAMES: dict[str, str] = {
    DISPATCH: "smart-charge slot",
    GRID_EVENT: "grid event",
    TARIFF: "tariff",
    EV_CHARGER: "car charger",
    INVERTER: "inverter",
    FORECAST: "forecast",
    SUPPLIER: "your supplier",
    DISPATCH_SHORT: "smart slot",
    SMART_CHARGE: "smart charge",
    EVENT_SOURCE: "grid-services",
}


def display(term: str, names: dict[str, str] | None = None) -> str:
    """The adapter's own name for `term` if `names` has one, else the default, else the term itself."""
    if names and term in names:
        return names[term]
    return DEFAULT_NAMES.get(term, term)


__all__ = ["DISPATCH", "GRID_EVENT", "TARIFF", "EV_CHARGER", "INVERTER", "FORECAST", "SUPPLIER", "DISPATCH_SHORT",
           "SMART_CHARGE", "EVENT_SOURCE", "DEFAULT_NAMES", "display"]
