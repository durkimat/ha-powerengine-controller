"""The adapter layer: the boundary between PowerEngine's neutral core and specific hardware and suppliers.

`pe_core` never names a brand (no "Solis", "EDF", "Zappi" in the decision logic) -- it thinks in terms of actions,
readings and the neutral vocabulary in `vocabulary.py`. Adapters live on the other side of that boundary: one per
inverter, tariff, EV charger and forecast provider, each translating between Home Assistant's entities and
PowerEngine's neutral terms. `base.py` defines what an adapter looks like (as `typing.Protocol`s) and the small
data shapes adapters pass across the boundary; `registry.py` is where concrete adapters register themselves so the
app can pick one by name.
"""

from __future__ import annotations

from .base import (
    ControlMethod,
    Dispatch,
    EVAdapter,
    EVState,
    ForecastAdapter,
    ForecastPoint,
    GridEvent,
    GridEventAdapter,
    HomeAssistant,
    InverterAdapter,
    InverterCapabilities,
    TariffAdapter,
)
from .registry import get, names, register
from .vocabulary import (
    DEFAULT_NAMES,
    DISPATCH,
    EV_CHARGER,
    FORECAST,
    GRID_EVENT,
    INVERTER,
    TARIFF,
    display,
)

__all__ = [
    "HomeAssistant",
    "ControlMethod",
    "InverterCapabilities",
    "InverterAdapter",
    "Dispatch",
    "GridEvent",
    "TariffAdapter",
    "GridEventAdapter",
    "EVState",
    "EVAdapter",
    "ForecastPoint",
    "ForecastAdapter",
    "register",
    "get",
    "names",
    "DISPATCH",
    "GRID_EVENT",
    "TARIFF",
    "EV_CHARGER",
    "INVERTER",
    "FORECAST",
    "DEFAULT_NAMES",
    "display",
]
