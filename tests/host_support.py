"""A Home Assistant stand-in for the host tests: a state machine behind the `Home` interface (pe_core/host_server.py),
and an engine-side `SocketHost` connected to a `HostServer` over a real local socket."""

import contextlib
import copy
import threading
import time
from datetime import datetime, timezone

from pe_core import protocol
from pe_core.host_server import HostServer
from pe_core.host_socket import SocketHost


class FakeHome:
    timezone = "Europe/London"

    def __init__(self):
        self.states_by_id: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.fired: list[tuple] = []
        self.written: list[tuple] = []
        self.server: HostServer | None = None
        self.fail_calls = False
        self.defined, self.retired, self.online = [], [], []

    def put(self, entity_id, state, attributes=None):
        """Set an entity as Home Assistant would, and tell the server (as the integration's listener does)."""
        now = datetime.now(timezone.utc).isoformat()
        new = {"entity_id": entity_id, "state": state, "attributes": attributes or {}, "last_changed": now,
               "last_updated": now}
        self.states_by_id[entity_id] = new
        if self.server is not None:
            self.server.state_changed(entity_id, copy.deepcopy(new))

    def remove(self, entity_id):
        self.states_by_id.pop(entity_id, None)
        if self.server is not None:
            self.server.state_changed(entity_id, None)

    # the Home interface
    def state(self, entity_id):
        return copy.deepcopy(self.states_by_id.get(entity_id))

    def states(self, domain=None):
        return {k: copy.deepcopy(v) for k, v in self.states_by_id.items()
                if domain is None or k.startswith(domain + ".")}

    def history(self, entity_id, start, end, minimal, no_attributes):
        return [[{"entity_id": entity_id, "state": "1", "last_changed": start}]]

    def render(self, template):
        return template.upper()

    def call(self, service, data):
        if self.fail_calls:
            raise RuntimeError("Home Assistant said no")
        self.calls.append((service, data))
        return {"ok": True}

    def fire(self, event, data):
        self.fired.append((event, data))

    def set_state(self, entity_id, state, attributes):
        self.written.append((entity_id, state, attributes))

    def define_entity(self, component, key, name, options):
        self.defined.append((component, key, name, options))

    def retire_entity(self, entity_id):
        self.retired.append(entity_id)

    def entities_online(self, online):
        self.online.append(online)


def wait_for(condition, timeout=5.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.005)
    raise AssertionError(f"timed out waiting for {what}")


@contextlib.contextmanager
def connected(home=None, secret="s3cret", engine_secret=None, proto=protocol.VERSION, retry=(0.05,), args=None):
    """(home, server, host): a started HostServer over `home` and a started SocketHost connected to it."""
    home = home or FakeHome()
    server = HostServer(home, secret, proto=proto)
    home.server = server
    address = server.start()
    host = SocketHost(address, engine_secret or secret, engine_version="9.9.9", args=args, retry=retry)
    try:
        host.start(timeout=5)
        yield home, server, host
    finally:
        host.stop()
        server.stop()


class Recorder:
    """Callbacks that remember how they were called."""

    def __init__(self):
        self.calls = []
        self.arrived = threading.Event()

    def __call__(self, *args):
        self.calls.append(args)
        self.arrived.set()

    def wait(self, n=1, timeout=5.0):
        wait_for(lambda: len(self.calls) >= n, timeout, f"{n} callbacks")
        return self.calls
