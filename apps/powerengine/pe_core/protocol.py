"""What the engine process and the Home Assistant integration say to each other.

One connection, on the same machine, from the engine to the integration. Each message is one line of JSON. The
integration starts the conversation with a challenge; the engine proves it holds the shared secret and says which
protocol version it speaks. The engine holds no Home Assistant token: it asks, and the integration acts for it.

    integration -> engine   {"t": "challenge", "nonce": "...", "proto": 1}
    engine -> integration   {"t": "hello", "proto": 1, "mac": "<HMAC-SHA256(secret, nonce)>", "engine": "0.9.203"}
    integration -> engine   {"t": "welcome", "proto": 1, "timezone": "Europe/London"}
                            or {"t": "refuse", "reason": "secret" | "protocol", "proto": 1}

Requests (engine -> integration) carry an "id" and are answered by {"t": "reply", "id": n, "ok": true, "value": ...} or
{"t": "reply", "id": n, "ok": false, "error": "..."}:

    watch    {"entity_id"}                  -> the entity's full state (or null) and, from now on, every change to it
    states   {"domain"?}                    -> {entity_id: full state} for every entity, or one domain (not watched)
    history  {"entity_id", "start", "end", "minimal"?, "no_attributes"?}   (times as ISO strings) -> [[row, ...]]
    template {"template"}                   -> the rendered text
    call     {"service": "domain/name", "data": {...}}
    fire     {"event", "data"}
    set      {"entity_id", "state", "attributes"}   write an entity's state (the direct publisher)
    on       {"event"} / off {"event"}      start or stop being sent this event

Pushes (integration -> engine, no id): {"t": "changed", "entity_id", "old", "new"} for a watched entity, {"t": "event",
"event", "data"} for an event the engine asked for, {"t": "stop"} to ask the engine to shut down.

A full state is {"entity_id", "state", "attributes", "last_changed", "last_updated"} as Home Assistant keeps it.

`VERSION` changes only when a message changes in a way the other side cannot ignore. A new engine on an old
integration (or the reverse) is refused at the handshake with reason "protocol", and the integration says so: that is
the one case where updating needs a Home Assistant restart. Adding a field, or a request an old side answers with an
error, does not change VERSION.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
from typing import Any

VERSION = 1
MAX_LINE = 32 * 1024 * 1024        # a day's history can be large, but nothing is bigger than this

REQUESTS = ("watch", "states", "history", "template", "call", "fire", "set", "on", "off")
PUSHES = ("changed", "event", "stop")
HANDSHAKE = ("challenge", "hello", "welcome", "refuse")


class ProtocolError(Exception):
    """The other side sent something that isn't the protocol."""


class Refused(ProtocolError):
    """The integration refused the handshake; `reason` is "secret" or "protocol"."""

    def __init__(self, reason: str, proto: int | None = None):
        super().__init__(f"refused: {reason}" + (f" (the integration speaks protocol {proto})" if proto else ""))
        self.reason, self.proto = reason, proto


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def mac(secret: str, nonce: str) -> str:
    return hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()


def check_mac(secret: str, nonce: str, given: Any) -> bool:
    return isinstance(given, str) and hmac.compare_digest(mac(secret, nonce), given)


class Wire:
    """A connected socket that sends and reads one JSON message per line. Sending is safe from several threads."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._buffer = b""
        self._send_lock = threading.Lock()

    def send(self, message: dict) -> None:
        line = json.dumps(message, default=str, separators=(",", ":")).encode() + b"\n"
        with self._send_lock:
            self.sock.sendall(line)

    def read(self) -> dict | None:
        """The next message, or None when the other side has closed."""
        while b"\n" not in self._buffer:
            if len(self._buffer) > MAX_LINE:
                raise ProtocolError("message too long")
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        try:
            message = json.loads(line)
        except ValueError as err:
            raise ProtocolError(f"not JSON: {err}") from err
        if not isinstance(message, dict) or "t" not in message:
            raise ProtocolError("a message is an object with a type 't'")
        return message

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def parse_address(address: str) -> tuple[str, Any]:
    """"unix:/path/to.sock" or "tcp:127.0.0.1:port" (port 0 lets the system choose, for the listener)."""
    kind, _, rest = address.partition(":")
    if kind == "unix" and rest:
        return "unix", rest
    if kind == "tcp":
        host, _, port = rest.rpartition(":")
        if host and port.isdigit():
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise ValueError("the engine connection is local only (127.0.0.1)")
            return "tcp", (host, int(port))
    raise ValueError(f"not an address: {address!r} (use unix:/path or tcp:127.0.0.1:port)")


def listen(address: str) -> tuple[socket.socket, str]:
    """A listening socket and its address as it can be given to `connect` (the port filled in)."""
    kind, where = parse_address(address)
    if kind == "unix":
        if os.path.exists(where):
            os.unlink(where)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(where)
        os.chmod(where, 0o600)
        sock.listen(1)
        return sock, address
    sock = socket.socket(socket.AF_INET6 if ":" in where[0] else socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(where)
    sock.listen(1)
    return sock, f"tcp:{where[0]}:{sock.getsockname()[1]}"


def connect(address: str, timeout: float = 5.0) -> socket.socket:
    kind, where = parse_address(address)
    sock = socket.socket(socket.AF_UNIX if kind == "unix" else socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(where)
    except OSError:
        sock.close()
        raise
    sock.settimeout(None)
    return sock
