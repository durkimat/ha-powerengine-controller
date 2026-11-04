"""The one door between PowerEngine and the thing that runs it.

The app reads and changes Home Assistant, sets timers and listens for events. Every one of those calls goes through a
`Host`, never straight to AppDaemon, so what hosts the app can change without the decisions changing. Today the host is
AppDaemon (`AppDaemonHost`); the plan is a second one that talks to a separate engine process (docs/plans).

The methods keep AppDaemon's names, arguments and meanings, because that is what the app already speaks:

  * Callbacks are plain functions called on a worker thread; a state or event callback takes the arguments AppDaemon
    gives it, a timer callback takes (kwargs).
  * `run_in`, `run_every` and `run_daily` return a handle that `cancel_timer` takes; `listen_state` and `listen_event`
    return one for `cancel_listen_state` and `cancel_listen_event`.
  * `get_state` is synchronous and cheap (a host may answer from a local copy of Home Assistant's states).

`AppDaemonHost` (host_appdaemon.py) looks each method up on the app at the moment of the call, not when it is built,
so the tests, which replace whatever they need on the app, work without change. Beyond the calls in METHODS a host
also provides what the engine needs of the place it runs: its own log, the way entity states are written, plugin
lookups, and the demo's start, exit and replacement of the Home Assistant calls.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

# The methods a host provides. test_host.py checks that the Protocol, the AppDaemon host and the fake AppDaemon the
# tests use all have exactly these.
METHODS = (
    "get_state", "get_history", "render_template", "get_timezone",
    "call_service", "fire_event",
    "listen_state", "listen_event", "cancel_listen_state", "cancel_listen_event",
    "run_in", "run_every", "run_daily", "cancel_timer",
)
# What a host provides beyond those (see the end of the Host class).
EXTRAS = ("log", "method", "state_writer", "plugin_api", "start_tracking", "cancel_all", "override", "clear_overrides",
          "background_writes", "native_entities")


@runtime_checkable
class Host(Protocol):
    """What the app needs from whatever runs it."""

    @property
    def args(self) -> dict:
        """The app's own settings from apps.yaml (settings_file, demo, ...)."""

    # --- Home Assistant: reading
    def get_state(self, entity_id: str | None = None, attribute: str | None = None, default: Any = None,
                  **kwargs: Any) -> Any:
        """An entity's state, one attribute (`attribute="name"`), everything (`attribute="all"`), or with no entity
        every state."""

    def get_history(self, **kwargs: Any) -> Any:
        """Recorded states: entity_id, start_time, end_time."""

    def render_template(self, template: str, **kwargs: Any) -> Any:
        """A Home Assistant template, rendered."""

    def get_timezone(self) -> str:
        """The time zone name Home Assistant is set to."""

    # --- Home Assistant: acting
    def call_service(self, service: str, **kwargs: Any) -> Any:
        """Call "domain/service" with the keyword arguments as service data."""

    def fire_event(self, event: str, **kwargs: Any) -> Any:
        """Fire an event on Home Assistant's bus."""

    # --- listening
    def listen_state(self, callback: Callable, entity_id: str | None = None, **kwargs: Any) -> Any:
        """Call back when the entity changes. Returns a handle for cancel_listen_state."""

    def listen_event(self, callback: Callable, event: str | None = None, **kwargs: Any) -> Any:
        """Call back when the event is fired. Returns a handle for cancel_listen_event."""

    def cancel_listen_state(self, handle: Any, **kwargs: Any) -> Any: ...

    def cancel_listen_event(self, handle: Any, **kwargs: Any) -> Any: ...

    # --- timers
    def run_in(self, callback: Callable, delay: float, **kwargs: Any) -> Any:
        """Call back once, `delay` seconds from now. Returns a handle for cancel_timer."""

    def run_every(self, callback: Callable, start: Any, interval: float, **kwargs: Any) -> Any:
        """Call back from `start`, then every `interval` seconds."""

    def run_daily(self, callback: Callable, start: Any, **kwargs: Any) -> Any:
        """Call back every day at the time of day `start`."""

    def cancel_timer(self, handle: Any, **kwargs: Any) -> Any: ...

    # --- beyond the calls above
    @property
    def background_writes(self) -> bool:
        """Do calls to the inverter wait on a real device, so that the engine should make them off the thread that runs
        its callbacks (pe_core/background.py)? False for a host whose calls are instant (the tests' fake, the demo)."""

    @property
    def native_entities(self) -> Any:
        """Where the engine's own entities can be made as real Home Assistant entities (unique id, device, registry):
        an object with `define(component, key, name, options)`, `retire(entity_id)` and `online(bool)`, or None where
        the host can only write states (AppDaemon)."""

    def log(self, msg: str, *args: Any, **kwargs: Any) -> Any:
        """The host's own log (level="INFO" by default). The engine's `log` adds its diagnostics ring and calls this."""

    def method(self, name: str) -> Callable | None:
        """The named call as it is now (the demo's replacement if there is one), or None if the host has none."""

    def state_writer(self, told_once: Callable, demo: bool = False) -> Callable | None:
        """A `set_state(entity_id, state=..., attributes=...)` that writes an entity exactly as given, for the direct
        publisher; None if the host cannot write states. `told_once(key, message, level)` reports a problem once."""

    def plugin_api(self, name: str) -> Any:
        """A plugin's API (the MQTT plugin), or None where the host has no such plugin."""

    def start_tracking(self) -> None:
        """Start recording the timers and listeners registered, for the demo's restart."""

    def cancel_all(self) -> None:
        """Cancel everything recorded since tracking started."""

    def override(self, **calls: Callable) -> None:
        """Replace calls (get_state, call_service, ...) until clear_overrides: the demo's gate."""

    def clear_overrides(self) -> None: ...
