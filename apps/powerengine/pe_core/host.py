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

`AppDaemonHost` looks each method up on the app at the moment of the call, not when it is built. That keeps the demo,
which replaces `get_state`, `call_service`, `set_state` and `fire_event` on the app, and the tests, which replace
whatever they need, working without change.
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


def _late(name: str) -> Callable:
    def call(self: AppDaemonHost, *args: Any, **kwargs: Any) -> Any:
        return getattr(self._app, name)(*args, **kwargs)
    call.__name__ = name
    call.__qualname__ = f"AppDaemonHost.{name}"
    return call


class AppDaemonHost:
    """The host when PowerEngine runs as an AppDaemon app: each call goes to the app's AppDaemon method as it is at
    that moment."""

    def __init__(self, app: Any):
        self._app = app

    @property
    def args(self) -> dict:
        return self._app.args


for _name in METHODS:
    setattr(AppDaemonHost, _name, _late(_name))
del _name
