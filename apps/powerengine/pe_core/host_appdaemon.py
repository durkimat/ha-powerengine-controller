"""PowerEngine hosted by AppDaemon: everything that is about AppDaemon rather than about PowerEngine.

`AppDaemonHost` wraps the AppDaemon app object (the `PowerEngine` shell in powerengine.py) and gives the engine the
`Host` interface (host.py). Besides forwarding the calls, it owns:

  * the record of the timers and listeners the engine registered, so the demo can start, change day and exit inside
    the running app and cancel exactly what it made (`start_tracking`, `cancel_all`);
  * the demo's replacement of the Home Assistant calls (`override`, `clear_overrides`);
  * how entity states are written (`state_writer`): AppDaemon 4.5 and later alter the attributes `set_state` sends and
    warn when it creates an entity, and this works round both;
  * the plugin lookups (`plugin_api`): the MQTT plugin may not be configured.

Each call is looked up on the app at the moment it is made, so a test that replaces a method on the app is seen.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from .adapters.publish import ad_would_alter
from .host import METHODS

# calls whose handle is recorded while tracking: method -> kind (cancel_timer, cancel_listen_event and
# cancel_listen_state take the handle back)
_TRACKED = {"run_every": "timer", "run_daily": "timer", "listen_event": "event", "listen_state": "state"}
_CANCEL = {"timer": "cancel_timer", "event": "cancel_listen_event", "state": "cancel_listen_state"}


def _forward(name: str) -> Callable:
    def call(self: AppDaemonHost, *args: Any, **kwargs: Any) -> Any:
        return self.method(name)(*args, **kwargs)
    call.__name__ = name
    call.__qualname__ = f"AppDaemonHost.{name}"
    return call


def _recording(name: str) -> Callable:
    kind = _TRACKED[name]

    def call(self: AppDaemonHost, callback: Callable, *args: Any, **kwargs: Any) -> Any:
        handle = self.method(name)(callback, *args, **kwargs)
        return handle if self.handles is None else self._track(kind, handle)
    call.__name__ = name
    call.__qualname__ = f"AppDaemonHost.{name}"
    return call


def _cancelling(name: str) -> Callable:
    def call(self: AppDaemonHost, handle: Any, *args: Any, **kwargs: Any) -> Any:
        if self.handles is not None:
            handle = self._resolved(handle) if isinstance(handle, asyncio.Future) else handle
            self._untrack(handle)
        return self.method(name)(handle, *args, **kwargs)
    call.__name__ = name
    call.__qualname__ = f"AppDaemonHost.{name}"
    return call


class AppDaemonHost:
    def __init__(self, app: Any):
        self._app = app
        self.handles: list[tuple] | None = None     # (kind, handle, due) once tracking has started
        self.wipes = 0                              # how many times cancel_all has run
        self.overrides: dict[str, Callable] = {}

    @property
    def args(self) -> dict:
        return self._app.args

    def log(self, msg: str, *args: Any, **kwargs: Any) -> Any:
        """AppDaemon's own log (the app's `log` is the one that also keeps a ring for the diagnostics export)."""
        return self._app._hass_log(msg, *args, **kwargs)

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

    @staticmethod
    def _resolved(handle: Any) -> Any:
        """The handle string behind what AppDaemon returned. AppDaemon 4.4 returns the string; newer versions return
        an asyncio Task/Future (when the call is made from the event loop) that resolves to it."""
        if isinstance(handle, asyncio.Future):
            if handle.done() and not handle.cancelled() and handle.exception() is None:
                return handle.result()
            return None
        return handle

    def _track(self, kind: str, handle: Any, due: datetime | None = None) -> Any:
        handles = self.handles
        if handles is None or handle is None:
            return handle
        if kind == "timer":                             # forget one-off timers that have already run (bounded list)
            now = datetime.now(timezone.utc)
            handles[:] = [h for h in handles if not (h[0] == "timer" and h[2] is not None and h[2] < now)]
        if not isinstance(handle, asyncio.Future) and any(h[1] == handle for h in handles):
            return handle                                # already recorded (run_daily is built on run_every)
        if isinstance(handle, asyncio.Future):
            real = self._resolved(handle)
            if real is not None:
                if not any(h[1] == real for h in handles):
                    handles.append((kind, real, due))
            elif not handle.done():
                generation = self.wipes

                def record(fut: Any, kind: str = kind, due: datetime | None = due) -> None:
                    real = self._resolved(fut)
                    live = self.handles
                    if real is None or live is None:
                        return
                    if self.wipes != generation:         # registered before a wipe: cancel it now
                        try:
                            self.method(_CANCEL[kind])(real)
                        except Exception:
                            pass
                        return
                    if not any(h[1] == real for h in live):
                        live.append((kind, real, due))
                handle.add_done_callback(record)
            return handle
        handles.append((kind, handle, due))
        return handle

    def _untrack(self, handle: Any) -> None:
        handle = self._resolved(handle) if isinstance(handle, asyncio.Future) else handle
        if self.handles is not None and handle is not None:
            self.handles[:] = [h for h in self.handles if h[1] != handle]

    def _timer_gone(self, handle: Any) -> bool:
        """True when AppDaemon says this timer is no longer scheduled (it ran, or was cancelled): cancelling it would
        only log "Invalid callback handle". Where AppDaemon can't say (4.4 has no timer_running), False."""
        check = getattr(self._app, "timer_running", None)
        if check is None:
            return False
        try:
            return check(handle) is False
        except Exception:
            return False

    def cancel_all(self) -> None:
        """Cancel every timer and listener recorded since tracking started, once each."""
        self.wipes += 1
        now = datetime.now(timezone.utc)
        done = set()
        for kind, handle, due in list(self.handles or []):
            if handle in done:                              # once each: a second cancel is an "Invalid callback handle"
                continue
            done.add(handle)
            self._untrack(handle)
            if kind == "timer" and ((due is not None and due <= now) or self._timer_gone(handle)):
                continue                                    # already ran
            try:
                self.method(_CANCEL[kind])(handle)
            except Exception:
                pass

    # --- plugins
    def plugin_configured(self, name: str) -> bool:
        """Is this plugin in AppDaemon's configuration? get_plugin_api logs a WARNING ("Unknown Plugin Configuration")
        when it isn't, so ask the plugin list first. Where the list can't be read, say yes and let the call decide."""
        try:
            plugins = self._app.AD.plugins
            for attr in ("config", "plugins"):
                found = getattr(plugins, attr, None)
                if isinstance(found, dict):
                    return name in found
        except Exception:
            pass
        return True

    def plugin_api(self, name: str) -> Any:
        """The plugin's API, or None when AppDaemon has no such plugin. Raises what AppDaemon raises."""
        return self._app.get_plugin_api(name) if self.plugin_configured(name) else None

    # --- writing entity states
    @staticmethod
    def cleans_attributes() -> bool:
        """Does this AppDaemon clean the payload of set_state (4.5 and later)? 4.4 sends it as given."""
        try:
            from appdaemon import utils as ad_utils
            return hasattr(ad_utils, "clean_http_kwargs")
        except Exception:
            return False

    def rest_states_poster(self) -> Callable | None:
        """A function that writes an entity's state and attributes to Home Assistant exactly as given, or None. Newer
        AppDaemon versions clean the payload of `set_state` on the way (`true` becomes "true"; `false`, `null` and every
        0 are dropped, so a series loses its zeros and every later value shifts, and the config's booleans arrive as
        text). This posts to HA's states endpoint with the plugin's own session instead, the way `set_state` does,
        but with nothing removed. None where its plugin can't be reached."""
        try:
            plugin = self._app.AD.plugins.get_plugin_object(self._app.namespace)
            loop, session, base = self._app.AD.loop, plugin.session, plugin.config.ha_url
            import aiohttp
        except Exception:
            return None

        async def post(entity_id, state, attributes):
            payload = {"state": "" if state is None else str(state), "attributes": attributes or {}}
            async with session.post(base / f"api/states/{entity_id}", json=payload,
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()

        def send(entity_id, state, attributes):
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:                             # on AppDaemon's own loop: can't wait for it here
                loop.create_task(post(entity_id, state, attributes))
            else:
                asyncio.run_coroutine_threadsafe(post(entity_id, state, attributes), loop).result(15)
        return send

    def lossless_set_state(self, plain: Callable, told_once: Callable) -> Callable:
        """`plain` (AppDaemon's set_state) for anything it would not alter; the exact REST write for attributes it would
        (see rest_states_poster). Only our own `pe_` entities are ever written this way. `told_once(key, message,
        level)` says a problem once."""
        if not self.cleans_attributes():
            return plain
        post = self.rest_states_poster()

        def set_state(entity_id, **kwargs):
            attributes = kwargs.get("attributes")
            if attributes and ad_would_alter(attributes) and re.match(r"^[a-z_]+\.pe_", str(entity_id)):
                if post is not None:
                    try:
                        post(entity_id, kwargs.get("state"), attributes)
                        return None
                    except Exception as err:
                        told_once("rest_write", f"Could not write {entity_id} to Home Assistant directly "
                                                f"({err!r}); using AppDaemon's set_state instead", "INFO")
                else:
                    told_once("rest_altered", "AppDaemon's set_state changes attribute values (true becomes "
                                              "text, false, null and 0 are dropped) and this AppDaemon can't be "
                                              "bypassed; some PowerEngine attributes may be wrong", "WARNING")
            return plain(entity_id, **kwargs)
        return set_state

    def state_writer(self, told_once: Callable, demo: bool = False) -> Callable | None:
        """set_state for the direct publisher. AppDaemon 4.5 and later warn "Entity ... not found" when set_state
        creates an entity, unless told `check_existence=False`; we create our own entities, so say so. 4.4 has no such
        argument (it would become an attribute), so it is only passed where set_state names it."""
        raw = self.method("set_state")
        if raw is None:
            return None
        if not demo:                                        # (the demo gate has it underneath: see the demo's start)
            raw = self.lossless_set_state(raw, told_once)
        try:
            named = "check_existence" in inspect.signature(type(self._app).set_state).parameters
        except (TypeError, ValueError, AttributeError):
            named = False
        if not named:
            return raw

        def set_state(entity_id, **kwargs):
            kwargs.setdefault("check_existence", False)
            return raw(entity_id, **kwargs)
        return set_state


for _name in METHODS:
    if _name not in ("run_in", "run_every", "run_daily", "listen_event", "listen_state", "cancel_timer",
                     "cancel_listen_event", "cancel_listen_state"):
        setattr(AppDaemonHost, _name, _forward(_name))
for _name in _TRACKED:
    setattr(AppDaemonHost, _name, _recording(_name))
for _name in _CANCEL.values():
    setattr(AppDaemonHost, _name, _cancelling(_name))
del _name
