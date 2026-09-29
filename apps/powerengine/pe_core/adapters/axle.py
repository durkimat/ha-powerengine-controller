"""The Axle VPP adapter: export events run by the aggregator, read from the Axle Home Assistant integration.

`read_event` is pure: it takes `state`, a function `role -> {"state": ..., "attributes": {...}} | None`, and returns
what `pe_core.readings.read()` stores in `Readings` (active now, next start, next end). `grid_events` wraps it for the
neutral `GridEvent` shape.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ..parsing import State, _is_on, parse_time
from .base import GridEvent, HomeAssistant
from .registry import register
from .vocabulary import GRID_EVENT

StateFn = Callable[[str], State | None]


class AxleEvents:
    """Reads Axle's export events. See module docstring."""

    name = "axle"

    def __init__(self, role_entity: Callable[[str], str | None] | None = None):
        self.role_entity = role_entity

    def display_names(self) -> dict[str, str]:
        return {GRID_EVENT: "Axle event"}

    def read_event(self, state: StateFn) -> tuple[bool, datetime | None, datetime | None]:
        """(event active now, next event start, next event end)."""
        return (_is_on(state("axle_event_active")),
                parse_time((state("axle_event_start") or {}).get("state")),
                parse_time((state("axle_event_end") or {}).get("state")))

    def grid_events(self, ha: HomeAssistant, now: datetime) -> list[GridEvent]:
        """The current or next event as an export `GridEvent` (its payment isn't known here, so no value); empty
        when Axle has given no start and end, or the event is over."""
        if self.role_entity is None:
            raise RuntimeError("AxleEvents needs a role_entity function to read through Home Assistant")

        def state(role: str):
            eid = self.role_entity(role)
            return ha.get_state(eid, attribute="all") if eid else None

        _, start, end = self.read_event(state)
        if start is None or end is None or end <= now:
            return []
        return [GridEvent(start, end, "export", None)]


register("event", "axle", lambda role_entity=None: AxleEvents(role_entity))
