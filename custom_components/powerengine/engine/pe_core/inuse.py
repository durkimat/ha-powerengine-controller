"""What the plan really uses for the figures PowerEngine can measure or learn, and why (the "In use" lines).

A figure typed under Your system may be replaced: by the measured one (the "Use measured" tick), or by a learned one
(the Learn switches). The settings only ever showed what was typed, so this builds one sentence per figure, from the
same numbers the plan reads: the typed value, the measured or learned one, the one in use and the reason it is not
the measured one. Pure: the app passes the numbers in.
"""

from __future__ import annotations

from typing import Any

CAPACITY_SAMPLES = 100           # half-hours of charge movement before a measured capacity counts (costbook)
CAPACITY_WINDOW = (0.7, 1.3)     # a measured capacity this far from the typed one is not believed
RATE_WINDOW = (0.5, 1.2)         # the same for a measured charge or discharge rate (learned overrides)


def _kwh(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") + " kWh"


def _kw(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") + " kW"


def _pct(v: float) -> str:
    return f"{v * 100:.1f}".rstrip("0").rstrip(".") + "%"


def _entry(used: str, text: str, measured: str | None, source: str) -> dict[str, Any]:
    return {"used": used, "text": text, "measured": measured, "source": source}


def capacity(configured: float, measured: float | None, samples: int, accepted: bool, used: float,
             use_flag: bool, min_days: int) -> dict[str, Any]:
    typed = _kwh(configured)
    if measured is None:
        return _entry(_kwh(used), f"In use: {typed}, as entered (not measured yet).", None, "configured")
    m = _kwh(measured)
    if accepted and use_flag:
        return _entry(_kwh(used), f"In use: {m}, measured (you entered {typed}).", m, "measured")
    if not use_flag:
        why = "Use measured is off"
    elif not CAPACITY_WINDOW[0] * configured <= measured <= CAPACITY_WINDOW[1] * configured:
        why = (f"it is outside {CAPACITY_WINDOW[0]:.0%} to {CAPACITY_WINDOW[1]:.0%} of what you entered, "
               "so it is not believed")
    elif samples < CAPACITY_SAMPLES:
        why = f"only {samples} of the {CAPACITY_SAMPLES} readings needed so far"
    else:
        why = f"fewer than {min_days} full days are recorded"
    return _entry(_kwh(used), f"In use: {typed}, as entered. The measured {m} is not used: {why}.", m, "configured")


def round_trip(configured_one_way: float, measured_rte: float | None, battery_one_way: float, grid_one_way: float,
               use_flag: bool, min_days: int) -> dict[str, Any]:
    """Round trips are one-way squared. `battery_one_way` is the battery's own figure in use; `grid_one_way` adds
    the inverter's learned conversion losses, which is what the plan prices a stored kWh with."""
    typed = _pct(configured_one_way ** 2)
    grid = f" With the inverter's conversion losses the plan uses {_pct(grid_one_way ** 2)}, grid to grid."
    if grid_one_way == battery_one_way:
        grid = ""
    if measured_rte is None:
        text = f"In use: {typed}, as entered (not measured yet, it needs {min_days} full days)."
        return _entry(_pct(battery_one_way ** 2), text + grid, None, "configured")
    m = _pct(measured_rte)
    if use_flag:
        return _entry(_pct(battery_one_way ** 2), f"In use: {m}, measured (you entered {typed}).{grid}", m, "measured")
    return _entry(_pct(battery_one_way ** 2),
                  f"In use: {typed}, as entered. The measured {m} is not used: Use measured is off.{grid}", m,
                  "configured")


def rate(configured: float, measured: float | None, used: float, use_flag: bool, discharge: bool) -> dict[str, Any]:
    typed = _kw(configured)
    tail = f" The inverter is still asked for {typed}: the measured rate only shapes the plan."
    if measured is None:
        return _entry(_kw(used), f"In use: {typed}, as entered (not measured yet).", None, "configured")
    m = _kw(measured)
    if not use_flag:
        return _entry(_kw(used), f"In use: {typed}, as entered. The measured {m} is not used: Use measured is off.",
                      m, "configured")
    if not RATE_WINDOW[0] * configured <= measured <= RATE_WINDOW[1] * configured:
        return _entry(_kw(used), f"In use: {typed}, as entered. The measured {m} is not used: it is outside "
                      f"{RATE_WINDOW[0]:.0%} to {RATE_WINDOW[1]:.0%} of what you entered.", m, "configured")
    return _entry(_kw(used), f"In use for the plan: {m}, measured (you entered {typed}).{tail}", m, "measured")


def learned(configured: str, learned_value: str | None, used: str, switch: str, on: bool,
            why_not: str) -> dict[str, Any]:
    """A figure the Learn switch may replace (car rate, export limit, reserve). `why_not`: what makes a learned
    figure unusable while the switch is on."""
    if learned_value is None:
        return _entry(used, f"In use: {configured}, as entered (nothing learned yet).", None, "configured")
    if used == configured and learned_value != configured:
        why = f"the '{switch}' switch is off" if not on else why_not
        return _entry(used, f"In use: {configured}, as entered. The learned {learned_value} is not used: {why}.",
                      learned_value, "configured")
    if used == configured:
        return _entry(used, f"In use: {configured}, as entered (what was learned agrees).", learned_value,
                      "configured")
    return _entry(used, f"In use: {used}, learned (you entered {configured}).", learned_value, "learned")
