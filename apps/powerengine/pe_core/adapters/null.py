"""Null adapters: what a site that leaves a part out ("none") runs on.

Each is the real adapter with its reads emptied, so every caller keeps working and simply finds nothing: no charger
readings, no forecast, no grid events. Their display names are empty, so the names map falls back to the neutral
words ("car charger", "forecast", "grid-services") in the texts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .axle import AxleEvents
from .base import GridEvent
from .myenergi import ZappiCharger
from .solcast import SolcastForecast


class NoCharger(ZappiCharger):
    name = "none"

    def display_names(self) -> dict[str, str]:
        return {}

    def read(self, state) -> dict[str, Any]:
        return {"power_w": None, "plug": None, "status": None, "mode": None, "session_kwh": None}


class NoForecast(SolcastForecast):
    name = "none"

    def display_names(self) -> dict[str, str]:
        return {}

    @staticmethod
    def day_kwh(items: Any) -> float | None:
        return None

    def read(self, get_attribute, entity_ids) -> list:
        return []

    def half_hourly(self, ha, day) -> list:
        return []


class NoEvents(AxleEvents):
    name = "none"

    def display_names(self) -> dict[str, str]:
        return {}

    def read_event(self, state) -> tuple[bool, datetime | None, datetime | None]:
        return False, None, None

    def grid_events(self, ha, now: datetime) -> list[GridEvent]:
        return []


# site key -> the adapter used for "none"
NULL = {"ev_charger": NoCharger, "forecast": NoForecast, "events": NoEvents}

__all__ = ["NoCharger", "NoForecast", "NoEvents", "NULL"]
