"""PowerEngine hosted by its own process: the engine side of the connection to the Home Assistant integration.

`SocketHost` gives the engine the `Host` interface (host.py) without AppDaemon. It talks the protocol in protocol.py to
the integration (the other end is host_server.py), keeps a local copy of every entity it has read so that `get_state`
stays synchronous and instant, and runs timers and callbacks itself (host_scheduler.py).

  * The first `get_state` of an entity asks the integration, which then sends every change to it: after that reads come
    from the copy and a state listener is called when the copy changes. Reading every entity, or a whole domain, is
    one request and is not watched.
  * If the connection drops the engine carries on with its copy, calls that need Home Assistant raise `HostUnavailable`,
    and the host reconnects in the background, re-reads what it was watching and calls the listeners for what changed
    meanwhile. If the integration refuses it (a different protocol version, a wrong secret) it stops trying and
    `refused` says why.
  * Callbacks and timers all run one at a time on one thread, as they do in AppDaemon.
"""

from __future__ import annotations

import copy
import itertools
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from . import protocol
from .host_scheduler import Dispatcher, Scheduler

LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR,
          "CRITICAL": logging.CRITICAL}
RETRY_S = (1, 2, 5, 10, 30)         # waits between attempts to reconnect (the last repeats)
REQUEST_TIMEOUT_S = 30.0


class HostUnavailable(ConnectionError):
    """The connection to the integration is down; the engine may carry on with what it has."""


class HostError(Exception):
    """The integration could not do what was asked (the message is its reason)."""


class SocketHost:
    def __init__(self, address: str, secret: str, engine_version: str = "", args: dict | None = None,
                 logger: logging.Logger | None = None, retry: tuple = RETRY_S):
        self.address, self._secret, self._engine_version = address, secret, engine_version
        self._args = dict(args or {})
        self._logger = logger or logging.getLogger("powerengine")
        self._retry = retry
        self.timezone = "UTC"
        self.refused: protocol.Refused | None = None
        self.stop_requested = threading.Event()         # the integration asked the engine to shut down
        self.connected = threading.Event()
        self.overrides: dict[str, Callable] = {}
        self.handles: list[tuple[str, Any]] | None = None
        self._dispatcher = Dispatcher(log=self.log)
        self._scheduler = Scheduler(self._dispatcher, "UTC", autostart=False, log=self.log)
        self._lock = threading.RLock()
        self._states: dict[str, dict | None] = {}       # the local copy: every entity read so far (None: not there)
        self._state_listeners: dict[str, tuple[str, Callable, dict]] = {}     # handle -> (entity_id, callback, kwargs)
        self._event_listeners: dict[str, tuple[str, Callable, dict]] = {}
        self._pending: dict[int, dict] = {}
        self._ids = itertools.count(1)
        self._handles = itertools.count(1)
        self._wire: protocol.Wire | None = None
        self._reader: threading.Thread | None = None
        self._stopped = False

    # --- the connection
    def start(self, timeout: float = 30.0) -> None:
        """Connect, and keep connected. Waits up to `timeout` for the first connection (the integration may still be
        starting); raises `Refused` if it is refused, `HostUnavailable` if it never answers."""
        deadline = time.monotonic() + timeout
        delays = iter(self._retry)
        while True:
            try:
                wire = self._handshake()
                break
            except protocol.Refused:
                raise
            except (OSError, protocol.ProtocolError) as err:
                if time.monotonic() >= deadline:
                    raise HostUnavailable(f"no answer from the integration at {self.address}: {err}") from err
                time.sleep(min(next(delays, self._retry[-1]), max(0.0, deadline - time.monotonic())))
        self._dispatcher.start()
        self._scheduler.start()
        self._reader = threading.Thread(target=self._run, args=(wire,), name="powerengine-connection", daemon=True)
        self._reader.start()

    def stop(self) -> None:
        self._stopped = True
        wire = self._wire
        if wire is not None:
            wire.close()
        self._scheduler.stop()
        self._dispatcher.idle(2)
        self._dispatcher.stop()
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(5)

    def _handshake(self) -> protocol.Wire:
        wire = protocol.Wire(protocol.connect(self.address))
        try:
            challenge = wire.read()
            if not challenge or challenge.get("t") != "challenge":
                raise protocol.ProtocolError("the integration did not start with a challenge")
            wire.send({"t": "hello", "proto": protocol.VERSION, "mac": protocol.mac(self._secret, challenge["nonce"]),
                       "engine": self._engine_version})
            reply = wire.read()
            if reply is None:
                raise protocol.ProtocolError("the integration closed the connection during the handshake")
            if reply.get("t") == "refuse":
                self.refused = protocol.Refused(str(reply.get("reason")), reply.get("proto"))
                raise self.refused
            if reply.get("t") != "welcome":
                raise protocol.ProtocolError(f"unexpected {reply.get('t')!r} in the handshake")
        except BaseException:
            wire.close()
            raise
        self.timezone = reply.get("timezone") or "UTC"
        self._scheduler.tz = _zone(self.timezone)
        self._wire = wire
        self.connected.set()
        return wire

    def _run(self, wire: protocol.Wire) -> None:
        """The reader: serve the connection until it closes, then reconnect (unless stopped or refused)."""
        attempt = 0
        while not self._stopped:
            try:
                self._serve(wire)
            except (OSError, protocol.ProtocolError) as err:
                if not self._stopped:
                    self.log(f"Lost the connection to the integration: {err}", level="WARNING")
            self._dropped()
            wire = None
            while wire is None and not self._stopped:
                time.sleep(self._retry[min(attempt, len(self._retry) - 1)])
                attempt += 1
                try:
                    wire = self._handshake()
                except protocol.Refused as err:
                    self.log(f"The integration refused this engine: {err}", level="ERROR")
                    self.stop_requested.set()
                    return
                except (OSError, protocol.ProtocolError):
                    continue
            if wire is not None:
                attempt = 0
                self.log("Reconnected to the integration")
                self._dispatcher.submit(self._resync, "resync")

    def _serve(self, wire: protocol.Wire) -> None:
        while True:
            message = wire.read()
            if message is None:
                return
            kind = message["t"]
            if kind == "reply":
                self._answered(message)
            elif kind == "changed":
                self._changed(message["entity_id"], message.get("new"))
            elif kind == "event":
                self._event(message["event"], message.get("data") or {})
            elif kind == "stop":
                self.stop_requested.set()

    def _dropped(self) -> None:
        self.connected.clear()
        wire, self._wire = self._wire, None
        if wire is not None:
            wire.close()
        with self._lock:
            pending, self._pending = list(self._pending.values()), {}
        for slot in pending:
            slot["ok"], slot["error"] = False, "the connection to the integration was lost"
            slot["done"].set()

    def _resync(self) -> None:
        """After a reconnection: read again what is watched (calling the listeners of what changed while away) and ask
        for the events again."""
        with self._lock:
            entities = sorted(self._states)
            events = sorted({e for e, _, _ in self._event_listeners.values()})
        try:
            for entity_id in entities:
                self._changed(entity_id, self.request("watch", entity_id=entity_id))
            for event in events:
                self.request("on", event=event)
        except (HostUnavailable, HostError) as err:
            self.log(f"Could not re-read what the engine watches after reconnecting: {err}", level="WARNING")

    # --- requests
    def request(self, kind: str, **fields: Any) -> Any:
        """Ask the integration and wait for the answer. Not for use from the connection's own thread."""
        wire = self._wire
        if wire is None or not self.connected.is_set():
            raise HostUnavailable("not connected to the integration")
        slot = {"done": threading.Event(), "ok": False, "value": None, "error": None}
        rid = next(self._ids)
        with self._lock:
            self._pending[rid] = slot
        try:
            wire.send({"t": kind, "id": rid, **fields})
        except OSError as err:
            with self._lock:
                self._pending.pop(rid, None)
            raise HostUnavailable(f"not connected to the integration: {err}") from err
        if not slot["done"].wait(REQUEST_TIMEOUT_S):
            with self._lock:
                self._pending.pop(rid, None)
            raise HostUnavailable(f"the integration did not answer {kind} in {REQUEST_TIMEOUT_S:.0f} s")
        if not slot["ok"]:
            if slot["error"] == "the connection to the integration was lost":
                raise HostUnavailable(slot["error"])
            raise HostError(slot["error"] or "failed")
        return slot["value"]

    def _answered(self, message: dict) -> None:
        with self._lock:
            slot = self._pending.pop(message.get("id"), None)
        if slot is not None:
            slot["ok"], slot["value"] = bool(message.get("ok")), message.get("value")
            slot["error"] = message.get("error")
            slot["done"].set()

    # --- Home Assistant: reading
    def get_state(self, entity_id: str | None = None, attribute: str | None = None, default: Any = None,
                  **kwargs: Any) -> Any:
        if entity_id is None or "." not in entity_id:                  # every state, or one domain
            try:
                found = self.request("states", **({"domain": entity_id} if entity_id else {}))
            except HostUnavailable:
                return default
            return found
        state = self._copy_of(entity_id)
        if state is None:
            return default
        if attribute == "all":
            return copy.deepcopy(state)
        if attribute:
            return (state.get("attributes") or {}).get(attribute, default)
        return state.get("state")

    def _copy_of(self, entity_id: str) -> dict | None:
        with self._lock:
            if entity_id in self._states:
                return self._states[entity_id]
        try:
            state = self.request("watch", entity_id=entity_id)
        except HostUnavailable as err:
            self._said_once("read:" + entity_id, f"Cannot read {entity_id} yet: {err}")
            return None
        with self._lock:
            self._states.setdefault(entity_id, state)
            return self._states[entity_id]

    def get_history(self, **kwargs: Any) -> Any:
        fields = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in kwargs.items()}
        return self.request("history", entity_id=fields.pop("entity_id", None),
                            start=fields.pop("start_time", None), end=fields.pop("end_time", None),
                            minimal=bool(fields.pop("minimal_response", False)),
                            no_attributes=bool(fields.pop("no_attributes", False)))

    def render_template(self, template: str, **kwargs: Any) -> Any:
        return self.request("template", template=template)

    def get_timezone(self) -> str:
        return self.timezone

    # --- Home Assistant: acting
    def call_service(self, service: str, **kwargs: Any) -> Any:
        kwargs.pop("namespace", None)
        return self.request("call", service=service, data=kwargs)

    def fire_event(self, event: str, **kwargs: Any) -> Any:
        kwargs.pop("namespace", None)
        return self.request("fire", event=event, data=kwargs)

    def set_state(self, entity_id: str, state: Any = None, attributes: dict | None = None, **kwargs: Any) -> Any:
        return self.request("set", entity_id=entity_id, state=state, attributes=attributes or {})

    # --- listening
    def listen_state(self, callback: Callable, entity_id: str | None = None, **kwargs: Any) -> Any:
        if not entity_id or "." not in entity_id:
            raise ValueError("listen_state needs one entity")
        handle = f"state-{next(self._handles)}"
        with self._lock:
            self._state_listeners[handle] = (entity_id, callback, kwargs)
        self._copy_of(entity_id)                                       # watch it (and fill the copy)
        return self._tracked("state", handle)

    def listen_event(self, callback: Callable, event: str | None = None, **kwargs: Any) -> Any:
        if not event:
            raise ValueError("listen_event needs an event name")
        handle = f"event-{next(self._handles)}"
        with self._lock:
            first = all(e != event for e, _, _ in self._event_listeners.values())
            self._event_listeners[handle] = (event, callback, kwargs)
        if first:
            try:
                self.request("on", event=event)
            except HostUnavailable:
                pass                                                   # asked again when the connection is back
        return self._tracked("event", handle)

    def cancel_listen_state(self, handle: Any, **kwargs: Any) -> Any:
        self._untrack(handle)
        with self._lock:
            return self._state_listeners.pop(handle, None) is not None

    def cancel_listen_event(self, handle: Any, **kwargs: Any) -> Any:
        self._untrack(handle)
        with self._lock:
            gone = self._event_listeners.pop(handle, None)
            last = gone is not None and all(e != gone[0] for e, _, _ in self._event_listeners.values())
        if last:
            try:
                self.request("off", event=gone[0])
            except (HostUnavailable, HostError):
                pass
        return gone is not None

    def _changed(self, entity_id: str, new: dict | None) -> None:
        with self._lock:
            old = self._states.get(entity_id)
            self._states[entity_id] = new
            listeners = [(c, kw) for e, c, kw in self._state_listeners.values() if e == entity_id]
        for callback, kwargs in listeners:
            attribute = kwargs.get("attribute")
            extra = {k: v for k, v in kwargs.items() if k != "attribute"}
            if attribute == "all":
                before, after = copy.deepcopy(old), copy.deepcopy(new)
                if before == after:
                    continue
            elif attribute:
                before = ((old or {}).get("attributes") or {}).get(attribute)
                after = ((new or {}).get("attributes") or {}).get(attribute)
                if before == after:
                    continue
            else:
                before, after = (old or {}).get("state"), (new or {}).get("state")
                if before == after:
                    continue
            self._dispatcher.submit(lambda c=callback, a=attribute or "state", b=before, n=after, k=extra:
                                    c(entity_id, a, b, n, k), f"state listener {entity_id}")

    def _event(self, event: str, data: dict) -> None:
        with self._lock:
            listeners = [(c, kw) for e, c, kw in self._event_listeners.values() if e == event]
        for callback, kwargs in listeners:
            payload, extra = copy.deepcopy(data), dict(kwargs)
            self._dispatcher.submit(lambda c=callback, d=payload, k=extra: c(event, d, k), f"event listener {event}")

    # --- timers (the engine's own: nothing to ask the integration)
    def run_in(self, callback: Callable, delay: float, **kwargs: Any) -> Any:
        return self._tracked("timer", self._scheduler.run_in(callback, delay, **kwargs))

    def run_every(self, callback: Callable, start: Any, interval: float, **kwargs: Any) -> Any:
        return self._tracked("timer", self._scheduler.run_every(callback, start, interval, **kwargs))

    def run_daily(self, callback: Callable, start: Any = None, **kwargs: Any) -> Any:
        return self._tracked("timer", self._scheduler.run_daily(callback, start, **kwargs))

    def cancel_timer(self, handle: Any, **kwargs: Any) -> Any:
        self._untrack(handle)
        return self._scheduler.cancel_timer(handle)

    # --- beyond the calls above
    def log(self, msg: str, *args: Any, **kwargs: Any) -> None:
        level = LEVELS.get(str(kwargs.get("level", "INFO")).upper(), logging.INFO)
        try:
            text = str(msg) % args if args else str(msg)
        except (TypeError, ValueError):
            text = " ".join([str(msg), *map(str, args)])
        self._logger.log(level, text)

    @property
    def args(self) -> dict:
        return self._args

    def method(self, name: str) -> Callable | None:
        return self.overrides.get(name) or getattr(self, name, None)

    def state_writer(self, told_once: Callable, demo: bool = False) -> Callable | None:
        return self.method("set_state")

    def plugin_api(self, name: str) -> Any:
        return None                                                   # no AppDaemon plugins here

    def override(self, **calls: Callable) -> None:
        self.overrides.update(calls)

    def clear_overrides(self) -> None:
        self.overrides.clear()

    def start_tracking(self) -> None:
        if self.handles is None:
            self.handles = []

    def cancel_all(self) -> None:
        for kind, handle in list(self.handles or []):
            {"timer": self.cancel_timer, "state": self.cancel_listen_state,
             "event": self.cancel_listen_event}[kind](handle)
        if self.handles is not None:
            self.handles.clear()

    def _tracked(self, kind: str, handle: Any) -> Any:
        if self.handles is not None:
            self.handles.append((kind, handle))
        return handle

    def _untrack(self, handle: Any) -> None:
        if self.handles is not None:
            self.handles[:] = [h for h in self.handles if h[1] != handle]

    def _said_once(self, key: str, message: str) -> None:
        with self._lock:
            told = self.__dict__.setdefault("_told", set())
            if key in told:
                return
            told.add(key)
        self.log(message, level="WARNING")


def _zone(name: str):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")
