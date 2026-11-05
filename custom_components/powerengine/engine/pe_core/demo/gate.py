"""The demo gate: everything the app does to Home Assistant in demo mode goes through here (demo plan, step C2).

In demo mode the app replaces its own `get_state`, `call_service`, `fire_event`, `set_state` and `get_history` with the
gate's, so no code path can reach the real system:

  - `get_state`, `get_history`: the world's for demo entities; PowerEngine's own `pe_` entities from the real HA
    (that is where the direct publisher puts them); everything else reads as missing (no `zone.home`, so no weather
    fetch);
  - `call_service`: always the world's. What the world refuses (notifications, logbook entries, reloads, any real
    entity) is dropped and logged once per service;
  - `set_state`: only `pe_` entities (the publisher's), anything else dropped;
  - `fire_event`: only the answers to the card's own requests (save, test, simulator, diagnostics); anything else
    (such as the update restart request) is dropped.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

PE_ENTITY = re.compile(r"^[a-z_]+\.pe_")


class DemoGate:
    def __init__(self, world_fn: Callable[[], Any], real_get_state: Callable | None, real_set_state: Callable | None,
                 real_fire_event: Callable | None, log: Callable, allowed_events: Iterable[str] = ()):
        self._world = world_fn
        self._real_get, self._real_set, self._real_fire = real_get_state, real_set_state, real_fire_event
        self._log = log
        self.allowed_events = frozenset(allowed_events)
        self.dropped: list[tuple[str, str]] = []            # (what, detail) of everything refused
        self._told: set[str] = set()

    def _drop(self, what: str, detail: str) -> None:
        self.dropped.append((what, detail))
        if what + detail.split(" ")[0] not in self._told:
            self._told.add(what + detail.split(" ")[0])
            self._log(f"Demo: dropped {what} {detail} (the demo changes nothing outside itself)")

    # --- reads ---------------------------------------------------------------------------------------

    def get_state(self, entity_id=None, attribute=None, default=None, **kw):
        world = self._world()
        if entity_id is None:                               # every entity: the demo's, plus PowerEngine's own
            out = world.get_state()
            if self._real_get is not None:
                for eid, st in (self._real_get() or {}).items():
                    if PE_ENTITY.match(eid):
                        out[eid] = st
            return out
        if world.is_demo(entity_id):
            return world.get_state(entity_id, attribute, default)
        if PE_ENTITY.match(str(entity_id)) and self._real_get is not None:
            return self._real_get(entity_id, attribute=attribute, default=default, **kw)
        return default

    def get_history(self, entity_id=None, start_time=None, end_time=None, **kw):
        world = self._world()
        return world.get_history(entity_id, start_time, end_time) if world.is_demo(entity_id) else [[]]

    # --- writes --------------------------------------------------------------------------------------

    def call_service(self, service, **data):
        """The world takes what it can; the rest is dropped. Never anything for Home Assistant."""
        if not self._world().call_service(service, **data):
            self._drop("service call", f"{service} {data.get('entity_id') or ''}".strip())
        return None

    def set_state(self, entity_id, **kw):
        if PE_ENTITY.match(str(entity_id)) and self._real_set is not None:
            return self._real_set(entity_id, **kw)
        self._drop("state write", str(entity_id))
        return None

    def fire_event(self, event, **kw):
        if event in self.allowed_events and self._real_fire is not None:
            return self._real_fire(event, **kw)
        self._drop("event", str(event))
        return None
