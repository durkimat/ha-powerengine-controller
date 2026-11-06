"""The engine run inside one Python object, as the tests, the demo and the nightly comparison run it.

`LocalHost` wraps an app object (the engine with a stand-in for Home Assistant as its other base class, see
pe_core/compare/harness.py) and gives the engine the `Host` interface (host.py). Besides forwarding the calls, it owns:

  * the record of the timers and listeners the engine registered, so the demo can start, change day and exit inside
    the running app and cancel exactly what it made (`start_tracking`, `cancel_all`);
  * the demo's replacement of the Home Assistant calls (`override`, `clear_overrides`).

Each call is looked up on the app at the moment it is made, so a test that replaces a method on the app is seen. Inside
Home Assistant the engine runs on the integration's host instead (pe_core/host_socket.py).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from .host import METHODS

# calls whose handle is recorded while tracking: method -> kind (cancel_timer, cancel_listen_event and
# cancel_listen_state take the handle back)
_TRACKED = {"run_every": "timer", "run_daily": "timer", "listen_event": "event", "listen_state": "state"}
_CANCEL = {"timer": "cancel_timer", "event": "cancel_listen_event", "state": "cancel_listen_state"}


def _forward(name: str) -> Callable:
    def call(self: LocalHost, *args: Any, **kwargs: Any) -> Any:
        return self.method(name)(*args, **kwargs)
    call.__name__ = name
    call.__qualname__ = f"LocalHost.{name}"
    return call


def _recording(name: str) -> Callable:
    kind = _TRACKED[name]

    def call(self: LocalHost, callback: Callable, *args: Any, **kwargs: Any) -> Any:
        handle = self.method(name)(callback, *args, **kwargs)
        return handle if self.handles is None else self._track(kind, handle)
    call.__name__ = name
    call.__qualname__ = f"LocalHost.{name}"
    return call


def _cancelling(name: str) -> Callable:
    def call(self: LocalHost, handle: Any, *args: Any, **kwargs: Any) -> Any:
        if self.handles is not None:
            self._untrack(handle)
        return self.method(name)(handle, *args, **kwargs)
    call.__name__ = name
    call.__qualname__ = f"LocalHost.{name}"
    return call


class LocalHost:
    def __init__(self, app: Any):
        self._app = app
        self.handles: list[tuple] | None = None     # (kind, handle, due) once tracking has started
        self.overrides: dict[str, Callable] = {}

    @property
    def args(self) -> dict:
        return self._app.args

    @property
    def background_writes(self) -> bool:
        return False                                     # a call to the stand-in is instant: send in step

    @property
    def native_entities(self) -> None:
        return None                                      # states are written, there is no integration behind them

    def log(self, msg: str, *args: Any, **kwargs: Any) -> Any:
        """The stand-in's own log (the app's `log` is the one that also keeps a ring for the diagnostics export)."""
        return self._app._base_log(msg, *args, **kwargs)

    def method(self, name: str) -> Callable | None:
        """The call as it is now: the demo's replacement if there is one, else the app's, else None."""
        return self.overrides.get(name) or getattr(self._app, name, None)

    # --- the demo replaces the calls that reach Home Assistant
    def override(self, **calls: Callable) -> None:
        self.overrides.update(calls)

    def clear_overrides(self) -> None:
        self.overrides.clear()

    # --- timers and listeners: forwarded, and recorded while tracking
    def run_in(self, callback: Callable, delay: float, **kwargs: Any) -> Any:
        handle = self.method("run_in")(callback, delay, **kwargs)
        if self.handles is None:
            return handle
        return self._track("timer", handle, datetime.now(timezone.utc) + timedelta(seconds=float(delay)))

    def start_tracking(self) -> None:
        """Record the handle of every timer and listener from now on (only an app that isn't set up yet, which is the
        only one that can switch in and out of the demo, needs this)."""
        if self.handles is None:
            self.handles = []

    def _track(self, kind: str, handle: Any, due: datetime | None = None) -> Any:
        handles = self.handles
        if handles is None or handle is None:
            return handle
        if kind == "timer":                             # forget one-off timers that have already run (bounded list)
            now = datetime.now(timezone.utc)
            handles[:] = [h for h in handles if not (h[0] == "timer" and h[2] is not None and h[2] < now)]
        if not any(h[1] == handle for h in handles):    # (run_daily is built on run_every)
            handles.append((kind, handle, due))
        return handle

    def _untrack(self, handle: Any) -> None:
        if self.handles is not None and handle is not None:
            self.handles[:] = [h for h in self.handles if h[1] != handle]

    def cancel_all(self) -> None:
        """Cancel every timer and listener recorded since tracking started, once each."""
        now = datetime.now(timezone.utc)
        done = set()
        for kind, handle, due in list(self.handles or []):
            if handle in done:
                continue
            done.add(handle)
            self._untrack(handle)
            if kind == "timer" and due is not None and due <= now:
                continue                                    # already ran
            try:
                self.method(_CANCEL[kind])(handle)
            except Exception:
                pass

    # --- states
    def state_writer(self, told_once: Callable, demo: bool = False) -> Callable | None:
        """set_state for the direct publisher."""
        return self.method("set_state")


for _name in METHODS:
    if _name not in ("run_in", "run_every", "run_daily", "listen_event", "listen_state", "cancel_timer",
                     "cancel_listen_event", "cancel_listen_state"):
        setattr(LocalHost, _name, _forward(_name))
for _name in _TRACKED:
    setattr(LocalHost, _name, _recording(_name))
for _name in _CANCEL.values():
    setattr(LocalHost, _name, _cancelling(_name))
del _name
