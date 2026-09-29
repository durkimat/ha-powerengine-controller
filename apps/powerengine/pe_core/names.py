"""One map of what this user's suppliers and devices are called, built from the adapters' `display_names()`.

User-facing text must never hard-code a supplier or device name ("EDF", "Zappi", "Solcast", "Solis", "Axle"): it takes
the name from this map, so another supplier's user reads their own. Terms:

    supplier        "EDF"                   tariff          "EDF tariff"
    dispatch        "EDF smart slot"        dispatch_short  "EDF slot"
    smart_charge    "EDF smart charge"      ev_charger      "Zappi"
    forecast        "Solcast"               inverter        "Solis"
    event           "Axle"

In Python, `N("event")` gives the current name and `fill(text)` replaces `<<term>>` placeholders (used in static
tables such as role help and in the shipped dashboard). Pure functions read the module's current map; it starts out as
this user's words (so unit tests and the replay see them) and the app sets the real one with `set_current()` once it
knows its adapters. An unknown placeholder is an error, never shipped text.
"""

from __future__ import annotations

import re

from .adapters.vocabulary import (
    DEFAULT_NAMES,
    DISPATCH,
    DISPATCH_SHORT,
    EV_CHARGER,
    EVENT_SOURCE,
    FORECAST,
    INVERTER,
    SMART_CHARGE,
    SUPPLIER,
    TARIFF,
)

TERMS = (SUPPLIER, TARIFF, DISPATCH, DISPATCH_SHORT, SMART_CHARGE, EV_CHARGER, FORECAST, INVERTER, EVENT_SOURCE)
PLACEHOLDER = re.compile(r"<<([a-z_]+)>>")

_current: dict[str, str] | None = None


def neutral_names() -> dict[str, str]:
    """What every term reads as when no adapter says otherwise."""
    return {t: DEFAULT_NAMES[t] for t in TERMS}


def build_names(tariff=None, ev=None, forecast=None, inverter=None, events=None) -> dict[str, str]:
    """The names map from the given adapters (each may be None: then that adapter's terms are the neutral words)."""
    out = neutral_names()
    for adapter in (tariff, ev, forecast, inverter, events):
        if adapter is not None:
            out.update({k: v for k, v in adapter.display_names().items() if k in out})
    return out


def default_names() -> dict[str, str]:
    """This user's words: EDF, Zappi, Solcast, Solis, Axle."""
    from .adapters.axle import AxleEvents
    from .adapters.kraken import KrakenTariff
    from .adapters.myenergi import ZappiCharger
    from .adapters.solcast import SolcastForecast
    from .adapters.solis import SolisInverter
    out = build_names(KrakenTariff("edf"), ZappiCharger(), SolcastForecast(), None, AxleEvents())
    out.update(SolisInverter.DISPLAY_NAMES)
    return out


def current() -> dict[str, str]:
    global _current
    if _current is None:
        _current = default_names()
    return _current


def set_current(names: dict[str, str] | None) -> None:
    """Set the map pure functions read (None: back to this user's words)."""
    global _current
    _current = dict(names) if names else None


def N(term: str) -> str:
    """The current name for `term` (neutral if unknown)."""
    return current().get(term) or DEFAULT_NAMES.get(term, term)


def fill(text: str, names: dict[str, str] | None = None) -> str:
    """Replace `<<term>>` placeholders with names (the current map by default). An unknown term raises ValueError."""
    names = names if names is not None else current()

    def sub(m: re.Match) -> str:
        term = m.group(1)
        if term not in TERMS:
            raise ValueError(f"unknown name placeholder <<{term}>>")
        return names.get(term) or DEFAULT_NAMES[term]
    return PLACEHOLDER.sub(sub, text)


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))
