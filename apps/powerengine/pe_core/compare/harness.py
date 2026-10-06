"""The fake AppDaemon and the fake clock the whole app can run against without Home Assistant.

Used by tests/replay_harness.py (the replay safety net), tools/engine_compare.py and the nightly engine comparison
(`pe_core.compare.run`, which the app starts as a separate process). Nothing in here reaches the real Home Assistant:
`Hass` keeps its states, service calls and events in memory.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import re
import shutil
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest import mock

VERSION = r"\b\d+\.\d+\.\d+\b"             # masked in logged lines, so releases don't change the record
# INFO log lines worth recording (control changes), besides every warning
LOGGED_INFO = ("PowerEngine ", "Leaving Active", "RAM remote control", "Inputs back", "Writes held",
               "Controller switch", "Car charger inputs")

REAL_DATETIME = datetime
HANDLES = itertools.count(1)          # run_in handles, unique across restarts (cancel_timer removes by handle)


class Clock:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FrozenDatetime(datetime):
    """datetime whose now() is the replay's clock (patched into the app's modules)."""

    @classmethod
    def now(cls, tz=None):
        t = Clock.now
        return t.astimezone(tz) if tz is not None else t.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return Clock.now.replace(tzinfo=None)


def _fake_appdaemon():
    class Hass:
        """Just enough of AppDaemon's Hass API, over an in-memory state machine."""

        def __init__(self):
            self.states: dict[str, dict] = {}
            self.calls: list[dict] = []
            self.timers: list[tuple] = []
            self.warnings: list[str] = []
            self.fired: list[tuple] = []
            self.rejected: set[str] = set()
            self.pending_pause: str | None = None
            self.args = {}

        # --- states
        def set_fake(self, eid, state, attributes=None, when=None):
            when = (when or Clock.now).isoformat()
            old = self.states.get(eid)
            changed = old is None or str(old["state"]) != str(state)
            attrs = attributes if attributes is not None else (old or {}).get("attributes", {})
            self.states[eid] = {"entity_id": eid, "state": state, "attributes": attrs,
                                "last_changed": when if changed else old["last_changed"],
                                "last_updated": when, "last_reported": when}

        def get_state(self, entity_id=None, attribute=None, default=None, **kw):
            if entity_id is None:
                return {k: dict(v) for k, v in self.states.items()}
            s = self.states.get(entity_id)
            if s is None:
                return default
            if attribute == "all":
                return json.loads(json.dumps(s))
            if attribute:
                return s["attributes"].get(attribute, default)
            return s["state"]

        def entity_exists(self, entity_id, **kw):
            return entity_id in self.states

        # --- services and events
        def call_service(self, service, **kw):
            self.calls.append({"t": Clock.now.isoformat(), "svc": service, **{k: v for k, v in kw.items()
                                                                             if k in ("entity_id", "value", "option")}})
            eid = kw.get("entity_id")
            if eid in self.rejected:            # simulates an inverter that doesn't take the setting
                return None
            if service.startswith("number/") and eid:
                self.set_fake(eid, kw.get("value"))
            elif service.startswith("select/") and eid:
                self.set_fake(eid, kw.get("option"))
            elif service.startswith("button/") and eid:
                self.set_fake(eid, Clock.now.isoformat())
            return None

        def fire_event(self, event, **kw):
            self.fired.append((Clock.now.isoformat(), event))

        # --- scheduling: run_in callbacks are run by the replay when due; the rest the replay drives itself
        def run_in(self, cb, delay, **kw):
            handle = next(HANDLES)
            self.timers.append((Clock.now + timedelta(seconds=float(delay)), cb, kw, handle))
            return handle

        def run_every(self, *a, **k):
            return None

        run_daily = run_at = run_minutely = run_hourly = run_once = run_every

        def cancel_timer(self, handle, **kw):
            self.timers = [x for x in self.timers if x[3] != handle]

        def listen_state(self, *a, **k):
            return None

        listen_event = listen_state

        def cancel_listen_state(self, *a, **k):
            return None

        cancel_listen_event = cancel_listen_state

        def get_history(self, *a, **k):
            return []

        def get_timezone(self):
            return "Europe/London"

        def get_plugin_api(self, name):
            return types.SimpleNamespace(mqtt_publish=self._mqtt_publish)

        def _mqtt_publish(self, topic, payload=None, *a, **k):
            """PowerEngine sets its own pause switch over MQTT (the daily write limit): the replay applies it."""
            if str(topic).endswith("/ctl_pause/set"):
                self.pending_pause = str(payload)

        def log(self, msg, *a, level="INFO", **k):
            if level in ("WARNING", "ERROR"):
                self.warnings.append(f"{Clock.now.isoformat()} {msg}")
            elif any(str(msg).startswith(p) for p in LOGGED_INFO):
                self.warnings.append(f"{Clock.now.isoformat()} [info] {re.sub(VERSION, 'x.y.z', str(msg))}")

    return Hass


fake_appdaemon = _fake_appdaemon


def recording_hass_class():
    """The fake Hass, also recording what the app registers with run_every and anything that would reach Home
    Assistant (in demo mode nothing should: the gate keeps it all inside the app)."""
    Base = _fake_appdaemon()

    class Hass(Base):
        def __init__(self):
            super().__init__()
            self.every, self.real_calls, self.real_events, self.real_sets = [], [], [], []

        def run_every(self, callback, start=None, interval=None, **kw):
            self.every.append((callback, float(interval)))
            return f"every-{len(self.every)}"

        def set_state(self, entity_id, state=None, attributes=None, **kw):
            self.real_sets.append(entity_id)
            self.set_fake(entity_id, state, attributes)

        def call_service(self, service, **kw):
            self.real_calls.append(service)

        def fire_event(self, event, **kw):
            self.real_events.append(event)

        def get_plugin_api(self, name):
            raise AssertionError("demo mode must not look for the MQTT plugin")

    return Hass


@contextlib.contextmanager
def loaded_app(config_text=None, engine: str = "v1", pack=None, extra_files=None, setup=None):
    """`powerengine` imported against the recording fake AppDaemon with the clock patched in. In demo mode the app
    copies demo/config.template as its settings: `config_text(engine)` (default: the template with the engine chosen) is
    written instead, and `extra_files` ({path relative to the settings folder: source file}) are copied beside it.
    `pack` replaces the demo pack the app loads (a real day as a one-day pack). `setup(powerengine, stack)` may enter
    more patches (the world the app builds, how it learns its load profile) on the ExitStack."""
    stubs = {}
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = recording_hass_class()
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        stubs[name] = types.ModuleType(name)
    stubs["appdaemon.plugins.hass.hassapi"] = hassapi
    saved = {name: sys.modules.get(name) for name in (*stubs, "powerengine")}
    real_copy = shutil.copyfile

    def copy(src, dst, **kw):
        if str(src).endswith("config.template"):
            text = open(src, encoding="utf-8").read()
            if config_text is not None:
                text = config_text(engine)
            elif engine == "v2":
                text = text.replace("  publisher: direct\n", "  publisher: direct\n  engine: v2\n")
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(text)
            for rel, source in (extra_files or {}).items():
                target = os.path.join(os.path.dirname(dst), rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                real_copy(source, target)
            return dst
        return real_copy(src, dst, **kw)

    with contextlib.ExitStack() as stack:
        sys.modules.update(stubs)
        sys.modules.pop("powerengine", None)
        stack.callback(lambda: [sys.modules.pop(n, None) if v is None else sys.modules.__setitem__(n, v)
                                for n, v in saved.items()])
        stack.enter_context(mock.patch.object(shutil, "copyfile", copy))
        import powerengine
        if pack is not None:
            stack.enter_context(mock.patch.object(powerengine, "load_demo_pack", lambda *a, **k: pack))
        if setup is not None:
            setup(powerengine, stack)
        real = REAL_DATETIME                  # (this module's own `datetime` is patched part-way through the loop)
        for name, mod in list(sys.modules.items()):
            if (name == "powerengine" or name.startswith("pe_core")) and getattr(mod, "datetime", None) is real:
                stack.enter_context(mock.patch.object(mod, "datetime", FrozenDatetime))
        yield powerengine
