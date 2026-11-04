"""The integration's end of the connection to the engine (protocol.py), written against a small `Home` interface so that
it can be tested here without Home Assistant and used there by the integration.

`HostServer` listens on a local address, lets one engine in at a time (a new engine replaces the old one), checks its
secret and protocol version, answers its requests through `Home` and sends it what it asked to hear about. The
integration feeds it with `state_changed` and `event_fired` from Home Assistant's own listeners.

Everything here is blocking and runs on threads of its own; `Home` is called from those threads, so an implementation
over Home Assistant's async API hands each call to its event loop and waits for the answer.
"""

from __future__ import annotations

import logging
import socket
import threading
from collections.abc import Callable
from typing import Any, Protocol

from . import protocol


class Home(Protocol):
    """What the integration offers the engine, one method per request in protocol.py."""

    timezone: str

    def state(self, entity_id: str) -> dict | None: ...

    def states(self, domain: str | None = None) -> dict[str, dict]: ...

    def history(self, entity_id: str, start: str | None, end: str | None, minimal: bool,
                no_attributes: bool) -> list: ...

    def render(self, template: str) -> Any: ...

    def call(self, service: str, data: dict) -> Any: ...

    def fire(self, event: str, data: dict) -> None: ...

    def set_state(self, entity_id: str, state: Any, attributes: dict) -> None: ...

    def define_entity(self, component: str, key: str, name: str, options: dict) -> None: ...

    def retire_entity(self, entity_id: str) -> None: ...

    def entities_online(self, online: bool) -> None: ...


def _describe(err: Exception) -> str:
    """The error as text for the engine. Some of Home Assistant's exceptions build their message from a translation,
    which can only be done on its event loop; from here that raises, and the engine must still be answered."""
    try:
        return f"{type(err).__name__}: {err}"
    except Exception:
        args = ", ".join(str(a) for a in getattr(err, "args", ()))
        return f"{type(err).__name__}{': ' + args if args else ''}"


class HostServer:
    def __init__(self, home: Home, secret: str, address: str = "tcp:127.0.0.1:0", proto: int = protocol.VERSION,
                 logger: logging.Logger | None = None, on_engine: Callable[[dict | None], None] | None = None,
                 on_watch: Callable[[str], None] | None = None, on_listen: Callable[[str], None] | None = None):
        self.home, self._secret, self._proto = home, secret, proto
        self._requested = address
        self._logger = logger or logging.getLogger(__name__)
        self._on_engine = on_engine or (lambda info: None)       # called with {"engine": version} or None on leaving
        self._on_watch = on_watch or (lambda entity_id: None)    # the engine now wants this entity's changes
        self._on_listen = on_listen or (lambda event: None)      # the engine now wants this event
        self._listener = None
        self.address = ""
        self._lock = threading.RLock()
        self._wire: protocol.Wire | None = None
        self._watched: set[str] = set()
        self._events: set[str] = set()
        self._stopped = False
        self._thread: threading.Thread | None = None
        self.engine: dict | None = None                          # the engine now connected: {"engine": "0.9.203", ...}

    def start(self) -> str:
        """Start listening; the address to give the engine (the port filled in)."""
        self._listener, self.address = protocol.listen(self._requested)
        self._thread = threading.Thread(target=self._accept, name="powerengine-server", daemon=True)
        self._thread.start()
        return self.address

    def stop(self) -> None:
        self._stopped = True
        with self._lock:
            wire = self._wire
        if wire is not None:
            wire.close()
        if self._listener is not None:
            for close in (lambda: self._listener.shutdown(socket.SHUT_RDWR), self._listener.close):   # wakes accept()
                try:
                    close()
                except OSError:
                    pass
        if self._thread is not None:
            self._thread.join(5)

    def ask_to_stop(self) -> bool:
        """Ask the connected engine to shut down (an update, or Home Assistant stopping). False: none is connected."""
        return self._send({"t": "stop"})

    # --- what Home Assistant tells us
    def state_changed(self, entity_id: str, new: dict | None) -> None:
        with self._lock:
            watched = entity_id in self._watched
        if watched:
            self._send({"t": "changed", "entity_id": entity_id, "new": new})

    def event_fired(self, event: str, data: dict, context: dict | None = None) -> None:
        """`context` is the event's origin ({"user_id", "id"}), for an engine that says who pressed a button."""
        with self._lock:
            wanted = event in self._events
        if wanted:
            self._send({"t": "event", "event": event, "data": data, **({"context": context} if context else {})})

    def wants(self) -> tuple[set[str], set[str]]:
        """(the entities and the events the engine has asked about), for the integration to subscribe to."""
        with self._lock:
            return set(self._watched), set(self._events)

    # --- the connection
    def _send(self, message: dict) -> bool:
        with self._lock:
            wire = self._wire
        if wire is None:
            return False
        try:
            wire.send(message)
        except OSError:
            return False
        return True

    def _accept(self) -> None:
        while not self._stopped:
            try:
                sock, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(sock,), name="powerengine-engine", daemon=True).start()

    def _serve(self, sock) -> None:
        wire = protocol.Wire(sock)
        try:
            info = self._handshake(wire)
            if info is None:
                return
        except (OSError, protocol.ProtocolError):
            wire.close()
            return
        with self._lock:
            old, self._wire = self._wire, wire
            self._watched, self._events = set(), set()
            self.engine = info
        if old is not None:
            old.close()
        self._on_engine(info)
        try:
            while True:
                message = wire.read()
                if message is None:
                    break
                self._handle(wire, message)
        except (OSError, protocol.ProtocolError) as err:
            self._logger.debug("engine connection ended: %s", err)
        finally:
            with self._lock:
                mine = self._wire is wire
                if mine:
                    self._wire, self.engine = None, None
                    self._watched, self._events = set(), set()
            wire.close()
            if mine:
                self._on_engine(None)

    def _handshake(self, wire: protocol.Wire) -> dict | None:
        nonce = protocol.new_secret()
        wire.send({"t": "challenge", "nonce": nonce, "proto": self._proto})
        hello = wire.read()
        if not hello or hello.get("t") != "hello":
            return None
        if not protocol.check_mac(self._secret, nonce, hello.get("mac")):
            wire.send({"t": "refuse", "reason": "secret", "proto": self._proto})
            return None
        if hello.get("proto") != self._proto:
            wire.send({"t": "refuse", "reason": "protocol", "proto": self._proto})
            return None
        wire.send({"t": "welcome", "proto": self._proto, "timezone": self.home.timezone})
        return {"engine": hello.get("engine", ""), "proto": hello.get("proto")}

    def _handle(self, wire: protocol.Wire, message: dict) -> None:
        kind, rid = message["t"], message.get("id")
        if rid is None:
            return                                               # (a push from the engine: there are none yet)
        if kind not in protocol.REQUESTS:
            wire.send({"t": "reply", "id": rid, "ok": False, "error": f"unknown request {kind!r}"})
            return
        try:
            value = self._answer(kind, message)
            wire.send({"t": "reply", "id": rid, "ok": True, "value": value})
        except OSError:
            raise
        except Exception as err:                                 # whatever Home Assistant said, the engine hears it
            wire.send({"t": "reply", "id": rid, "ok": False, "error": _describe(err)})

    def _answer(self, kind: str, m: dict) -> Any:
        home = self.home
        if kind == "watch":
            with self._lock:
                self._watched.add(m["entity_id"])
            self._on_watch(m["entity_id"])
            return home.state(m["entity_id"])
        if kind == "states":
            return home.states(m.get("domain"))
        if kind == "history":
            return home.history(m["entity_id"], m.get("start"), m.get("end"), bool(m.get("minimal")),
                                bool(m.get("no_attributes")))
        if kind == "template":
            return home.render(m["template"])
        if kind == "call":
            return home.call(m["service"], m.get("data") or {})
        if kind == "fire":
            home.fire(m["event"], m.get("data") or {})
            return None
        if kind == "set":
            home.set_state(m["entity_id"], m.get("state"), m.get("attributes") or {})
            return None
        if kind == "entity":
            home.define_entity(m["component"], m["key"], m.get("name") or m["key"], m.get("options") or {})
            return None
        if kind == "retire":
            home.retire_entity(m["entity_id"])
            return None
        if kind == "online":
            home.entities_online(bool(m.get("online")))
            return None
        if kind == "on":
            with self._lock:
                self._events.add(m["event"])
            self._on_listen(m["event"])
            return None
        if kind == "off":
            with self._lock:
                self._events.discard(m["event"])
            return None
        return None
