"""The Solcast forecast adapter: the `detailedForecast` attribute of the Solcast PV Forecast integration's sensors.

Each item is {"period_start": ISO time, "pv_estimate": kW average over the half hour, and optionally
"pv_estimate10" / "pv_estimate90" (the low and high bands)}. `points`, `day_kwh` and `read` are pure; the
`ForecastAdapter` protocol method wraps them over the `HomeAssistant` door, using the entity mapped to each role.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, timezone, tzinfo
from typing import Any

from ..parsing import _num, parse_time
from .base import ForecastPoint, HomeAssistant
from .registry import register

ROLES = ("solar_forecast_today", "solar_forecast_tomorrow", "solar_forecast_day3")


def _kwh_or_none(value: Any) -> float | None:
    try:
        return float(value) * 0.5
    except (TypeError, ValueError):
        return None


class SolcastForecast:
    """Reads a Solcast forecast. See module docstring."""

    name = "solcast"
    attribute = "detailedForecast"

    def __init__(self, role_entity: Callable[[str], str | None] | None = None, tz: tzinfo | None = None):
        self.role_entity = role_entity
        self.tz = tz or timezone.utc

    def points(self, items: Iterable[Any] | None) -> list[ForecastPoint]:
        """Half-hourly points (kWh = pv_estimate kW x 0.5), in the given order. An item with no readable start time or
        a non-numeric `pv_estimate` is skipped (a missing `pv_estimate` counts as 0); non-dict items are skipped. The
        bands come from `pv_estimate10`/`pv_estimate90` when they are numbers, else None. Starts are as given, not
        snapped to a slot."""
        out = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            start = parse_time(item.get("period_start"))
            try:
                kw = float(item.get("pv_estimate") or 0.0)
            except (TypeError, ValueError):
                continue
            if start:
                out.append(ForecastPoint(start, kw * 0.5, _kwh_or_none(item.get("pv_estimate10")),
                                         _kwh_or_none(item.get("pv_estimate90"))))
        return out

    @staticmethod
    def day_kwh(items: Any) -> float | None:
        """Total kWh of a `detailedForecast` list (pv_estimate is kW over 30 min), rounded to 3 places; None when it
        isn't a non-empty list."""
        if not isinstance(items, list) or not items:
            return None
        return round(sum((_num(i.get("pv_estimate")) or 0.0) * 0.5 for i in items if isinstance(i, dict)), 3)

    def read(self, get_attribute: Callable[[str, str], Any], entity_ids: Iterable[str | None]) -> list:
        """The raw items of every given forecast entity, joined in order (get_attribute(entity_id, attribute))."""
        items: list = []
        for eid in entity_ids:
            if eid:
                items += get_attribute(eid, self.attribute) or []
        return items

    def half_hourly(self, ha: HomeAssistant, day: date) -> list[ForecastPoint]:
        """The points whose start falls on `day` (in the adapter's time zone), earliest first."""
        if self.role_entity is None:
            raise RuntimeError("SolcastForecast needs a role_entity function to read through Home Assistant")
        raw = self.read(lambda eid, attr: ha.get_state(eid, attribute=attr), [self.role_entity(r) for r in ROLES])
        return sorted((p for p in self.points(raw) if p.start.astimezone(self.tz).date() == day),
                      key=lambda p: p.start)


register("forecast", "solcast", SolcastForecast)
