"""The engine's view of Home Assistant: `HassHome` answers the engine's requests (host_server.Home) from Home Assistant,
and sends it the changes and events it asked to hear about.

The server (host_server.py) calls `HassHome` from threads of its own, so each call hands its work to Home Assistant's
event loop and waits. Going the other way, what Home Assistant tells the engine goes through a queue and a thread of its
own (`_Outbox`), so a slow engine can never hold up the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util
from homeassistant.util.async_ import run_callback_threadsafe

_LOGGER = logging.getLogger(__name__)
CALL_TIMEOUT_S = 120.0          # a service call waits for the device (a slow inverter answers in 2 to 4 s)


def dump(state: State | None, as_id: str | None = None) -> dict | None:
    """A state as the engine reads it: what the engine's `get_state(attribute="all")` gives."""
    if state is None:
        return None
    return {"entity_id": as_id or state.entity_id, "state": state.state, "attributes": dict(state.attributes),
            "last_changed": state.last_changed.isoformat(), "last_updated": state.last_updated.isoformat()}


class _Outbox:
    """What Home Assistant tells the engine, in order, on one thread."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="powerengine-outbox", daemon=True)
        self._thread.start()

    def put(self, fn: Callable[..., Any], *args: Any) -> None:
        self._queue.put((fn, args))

    def stop(self) -> None:
        self._queue.put(None)
        self._thread.join(5)

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                item[0](*item[1])
            except Exception:                              # (the engine went away: it asks again when it is back)
                _LOGGER.debug("Could not tell the engine", exc_info=True)


class HassHome:
    """host_server.Home over Home Assistant. `server` is set once the server exists."""

    def __init__(self, hass: HomeAssistant, native: Any = None, watchdog: Any = None) -> None:
        self.hass = hass
        self.watchdog = watchdog                                   # watchdog.Watchdog, or None
        self.native = native                                       # native.NativeEntities, or None (plain states only)
        self.server: Any = None
        self._outbox = _Outbox()
        self._watching: dict[str, Callable[[], None]] = {}
        self._listening: dict[str, Callable[[], None]] = {}

    @property
    def timezone(self) -> str:
        return str(self.hass.config.time_zone)

    def close(self) -> None:
        """Stop listening for the engine (runs on the event loop)."""
        for unsub in [*self._watching.values(), *self._listening.values()]:
            unsub()
        self._watching.clear()
        self._listening.clear()
        self._outbox.stop()

    # --- running things on the event loop, from the server's threads
    def _on_loop(self, fn: Callable[[], Any]) -> Any:
        return run_callback_threadsafe(self.hass.loop, fn).result(CALL_TIMEOUT_S)

    def _await(self, coro: Coroutine[Any, Any, Any]) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.hass.loop).result(CALL_TIMEOUT_S)

    # --- Home: what the engine asks
    def state(self, entity_id: str) -> dict | None:
        return self._on_loop(lambda: dump(self.hass.states.get(entity_id), entity_id))

    def states(self, domain: str | None = None) -> dict[str, dict]:
        return self._on_loop(lambda: {s.entity_id: dump(s) for s in self.hass.states.async_all(domain)})

    def history(self, entity_id: str, start: str | None, end: str | None, minimal: bool, no_attributes: bool) -> list:
        return self._await(self._history(entity_id, start, end, no_attributes))

    async def _history(self, entity_id: str, start: str | None, end: str | None, no_attributes: bool) -> list:
        from homeassistant.components.recorder import get_instance, history
        begin = dt_util.parse_datetime(start) if start else dt_util.utcnow()
        finish = dt_util.parse_datetime(end) if end else None
        found = await get_instance(self.hass).async_add_executor_job(
            lambda: history.get_significant_states(
                self.hass, begin, finish, [entity_id], None, True, False, False, no_attributes))
        rows = []
        for item in found.get(entity_id, []):
            if isinstance(item, State):
                row = {"entity_id": entity_id, "state": item.state, "last_changed": item.last_changed.isoformat(),
                       "last_updated": item.last_updated.isoformat()}
                if not no_attributes:
                    row["attributes"] = dict(item.attributes)
            else:                                                   # a compressed row
                row = dict(item)
            rows.append(row)
        return [rows]

    def render(self, template: str) -> Any:
        async def run() -> Any:
            return Template(template, self.hass).async_render(parse_result=False)
        return self._await(run())

    def call(self, service: str, data: dict) -> Any:
        domain, _, name = service.partition("/")
        if not name:
            raise ValueError(f"not a service: {service!r} (use domain/service)")
        self._await(self.hass.services.async_call(domain, name, data, blocking=True))
        return None

    def fire(self, event: str, data: dict) -> None:
        self._on_loop(lambda: self.hass.bus.async_fire(event, data))

    def set_state(self, entity_id: str, state: Any, attributes: dict) -> None:
        def write() -> None:
            if self.native is not None and self.native.knows(entity_id):
                self.native.update(entity_id, state, attributes)
            else:
                self.hass.states.async_set(entity_id, "" if state is None else str(state), attributes)
        self._on_loop(write)

    def define_entity(self, component: str, key: str, name: str, options: dict) -> None:
        if self.native is None:
            raise ValueError("this integration makes no native entities")
        self._on_loop(lambda: self.native.define(component, key, name, options))

    def retire_entity(self, entity_id: str) -> None:
        if self.native is not None:
            self._on_loop(lambda: self.native.retire(entity_id))

    def entities_online(self, online: bool) -> None:
        if self.native is not None:
            self._on_loop(lambda: self.native.set_online(online))

    def set_failsafe(self, calls: list, armed: bool) -> None:
        if self.watchdog is None:
            raise ValueError("no watchdog to hold a failsafe")
        self._on_loop(lambda: self.watchdog.set(calls, armed))

    # --- what the engine asked to hear about (called by the server on its threads)
    def watch(self, entity_id: str) -> None:
        self.hass.loop.call_soon_threadsafe(self._watch, entity_id)

    @callback
    def _watch(self, entity_id: str) -> None:
        if entity_id in self._watching:
            return
        @callback
        def changed(event: Event[EventStateChangedData]) -> None:
            self._outbox.put(self.server.state_changed, entity_id, dump(event.data["new_state"], entity_id))
        self._watching[entity_id] = async_track_state_change_event(self.hass, [entity_id], changed)

    def listen(self, event_type: str) -> None:
        self.hass.loop.call_soon_threadsafe(self._listen, event_type)

    @callback
    def _listen(self, event_type: str) -> None:
        if event_type in self._listening:
            return

        @callback
        def fired(event: Event) -> None:
            context = {"user_id": event.context.user_id, "id": event.context.id}
            data = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dict(event.data).items()}
            self._outbox.put(self.server.event_fired, event_type, data, context)
        self._listening[event_type] = self.hass.bus.async_listen(event_type, fired)
