"""Small parsing helpers shared by `readings` and the tariff/event adapters (kept here so the adapters don't import
`readings`, which imports them)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

State = dict[str, Any]

_ON = {"on", "true", "yes", "1", "active"}


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    value: float | None = None           # rate (GBP/kWh), or kWh for dispatches


def _num(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None           # drop NaN


def parse_time(value: Any) -> datetime | None:
    if value in (None, "", "unknown", "unavailable", "None"):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _rate(state: State | None) -> float | None:
    if not state:
        return None
    n = _num(state.get("state"))
    if n is None:
        return None
    return n / 100 if (state.get("attributes") or {}).get("unit_of_measurement") == "p/kWh" else n


def parse_windows(items: Any, value_keys: tuple[str, ...] = ("value_inc_vat", "value")) -> list[Window]:
    """Parse [{start, end, value...}] lists (EDF/Octopus rates, dispatches)."""
    out: list[Window] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        start, end = parse_time(it.get("start")), parse_time(it.get("end"))
        if not start:
            continue
        end = end or start + timedelta(minutes=30)
        value = next((_num(it[k]) for k in value_keys if k in it and _num(it[k]) is not None), None)
        out.append(Window(start, end, value))
    return out


def _is_on(state: State | None) -> bool:
    return bool(state) and str(state.get("state", "")).lower() in _ON


def _power_w(state: State | None, invert: bool = False) -> float | None:
    if not state:
        return None
    n = _num(state.get("state"))
    if n is None:
        return None
    unit = (state.get("attributes") or {}).get("unit_of_measurement")
    if unit == "kW":
        n *= 1000
    return -n if invert else n


def _energy_kwh(state: State | None) -> float | None:
    if not state:
        return None
    n = _num(state.get("state"))
    if n is None:
        return None
    return n / 1000 if (state.get("attributes") or {}).get("unit_of_measurement") == "Wh" else n
