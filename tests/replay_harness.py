"""Replay a recorded day through the whole PowerEngine app, with Home Assistant and the clock faked.

Used by test_replay.py as a safety net for refactoring: the plans, decisions and inverter writes the app produces
from the same inputs must not change unless a release means them to. The inverter doesn't respond (the recorded
battery, grid and house readings play back as they were), so this checks what PowerEngine asks for, not the physics.
"""

from __future__ import annotations

import json
import re
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).parent
STEP = timedelta(seconds=60)
VERSION = r"\b\d+\.\d+\.\d+\b"             # masked in logged lines, so releases don't change the record
PLAN_SLOTS = 32                      # half-hours of each plan kept in the record
# INFO log lines worth recording (control changes), besides every warning
LOGGED_INFO = ("PowerEngine ", "Leaving Active", "RAM remote control", "Inputs back", "Writes held",
               "Controller switch", "Car charger inputs")


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
            self.timers.append((Clock.now + timedelta(seconds=float(delay)), cb, kw))
            return len(self.timers)

        def run_every(self, *a, **k):
            return None

        run_daily = run_at = run_minutely = run_hourly = run_once = run_every

        def cancel_timer(self, handle, **kw):
            return None

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


def load_app(monkeypatch):
    """Import powerengine against the fake AppDaemon, with the clock patched into every module that uses it."""
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = _fake_appdaemon()
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine
    for name, mod in list(sys.modules.items()):
        if (name == "powerengine" or name.startswith("pe_core")) and getattr(mod, "datetime", None) is datetime:
            monkeypatch.setattr(mod, "datetime", FrozenDatetime)
    return powerengine


class Replay:
    def __init__(self, fixture: dict, folder: Path, powerengine, overrides: dict | None = None,
                 start: str | None = None, end: str | None = None, events: list[tuple] | None = None):
        self.fx = fixture
        self.powerengine = powerengine
        self.folder = folder
        cfg = json.loads(json.dumps(fixture["config"]))
        for section, values in (overrides or {}).items():
            cfg.setdefault(section, {}).update(values)
        (folder / "costs").mkdir(parents=True, exist_ok=True)
        (folder / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
        self.cfg = cfg
        self.app = powerengine.PowerEngine()
        self.app.args = {"settings_file": str(folder / "config.yaml")}
        self.start = datetime.fromisoformat(start) if start else datetime.fromisoformat(fixture["start"])
        self.end = datetime.fromisoformat(end) if end else datetime.fromisoformat(fixture["end"])
        self.timeline = {e: [(datetime.fromisoformat(t), v) for t, v in pts] for e, pts in fixture["timeline"].items()}
        self.rates = fixture["rates"]
        self.solar = fixture["solar_forecast"]
        self.events = sorted(((datetime.fromisoformat(iso), action) for iso, action in (events or [])),
                             key=lambda x: x[0])
        self._applied_static = False
        self.record: list[dict] = []

    # --- inputs at the replay's time
    def _inputs(self):
        a, t = self.app, Clock.now
        inp = self.cfg["inputs"]
        if not self._applied_static:                # the scenario's own first step, whatever its start time
            self._applied_static = True
            for eid, s in self.fx["static"].items():
                a.set_fake(eid, s["state"], json.loads(json.dumps(s["attributes"])))
            a.set_fake("switch.pe_ctl_pause", "off", {})
        for eid in self.fx["static"]:                 # HA keeps reporting unchanged values: not stale
            if eid in a.states:
                a.states[eid]["last_reported"] = a.states[eid]["last_updated"] = t.isoformat()
        for eid, pts in self.timeline.items():
            v = next((v for when, v in reversed(pts) if when <= t), pts[0][1])
            a.set_fake(eid, v)
        if "battery_charge_power" in inp and "battery_power" in inp:   # Solis: separate in/out sensors, from power
            p = float(a.get_state(inp["battery_power"]["entity"]) or 0)
            a.set_fake(inp["battery_charge_power"]["entity"], round(max(0.0, -p), 1))
            a.set_fake(inp["battery_discharge_power"]["entity"], round(max(0.0, p), 1))
        local = t.astimezone(a.tz) if getattr(a, "tz", None) else t
        day = local.date()

        def rates_for(d):
            return [r for r in self.rates if datetime.fromisoformat(r["start"]).date() == d]
        a.set_fake(inp["import_rates_today"]["entity"], t.isoformat(), {"rates": rates_for(day)})
        a.set_fake(inp["import_rates_tomorrow"]["entity"], t.isoformat(), {"rates": rates_for(day + timedelta(days=1))})
        now_rate = next((r["value_inc_vat"] for r in self.rates
                         if datetime.fromisoformat(r["start"]) <= t < datetime.fromisoformat(r["end"])), None)
        a.set_fake(inp["import_rate_now"]["entity"], now_rate, {"unit_of_measurement": "GBP/kWh"})
        if "offpeak_now" in inp:
            a.set_fake(inp["offpeak_now"]["entity"], "on" if now_rate is not None and now_rate < 0.1 else "off")
        for role, off in (("solar_forecast_today", 0), ("solar_forecast_tomorrow", 1), ("solar_forecast_day3", 2)):
            if role in inp:
                d = day + timedelta(days=off)
                fc = [p for p in self.solar if datetime.fromisoformat(p["period_start"]).date() == d]
                total = round(sum(p.get("pv_estimate", 0) for p in fc) / 2, 3)
                a.set_fake(inp[role]["entity"], total, {"detailedForecast": fc, "unit_of_measurement": "kWh"})
        # smart-charge slots as EDF listed them at this time
        planned = []
        for dsp in self.fx["dispatches"]:
            seen, stop = datetime.fromisoformat(dsp["first_seen"]), datetime.fromisoformat(dsp["ended"] or dsp["end"])
            if seen <= t < stop and datetime.fromisoformat(dsp["end"]) > t:
                planned.append({"start": dsp["start"], "end": dsp["end"], "charge_in_kwh": -(dsp["kwh"] or 0),
                                "source": "SMART"})
        if "smart_dispatches" in inp:
            on = any(datetime.fromisoformat(p["start"]) <= t for p in planned)
            a.set_fake(inp["smart_dispatches"]["entity"], "on" if on else "off",
                       {"planned_dispatches": planned, "completed_dispatches": []})
        car_w = float(a.get_state(inp["ev_charge_power"]["entity"]) or 0) if "ev_charge_power" in inp else 0.0
        if "ev_plug_status" in inp:
            a.set_fake(inp["ev_plug_status"]["entity"], "Charging" if car_w > 100 else "Waiting for EV")
        if "ev_charger_status" in inp:
            a.set_fake(inp["ev_charger_status"]["entity"], "Charging" if car_w > 100 else "Completed")

    # --- what's recorded
    def _snap(self, last):
        a, t = self.app, Clock.now.isoformat()
        d = getattr(a, "_decision", None)
        dec = [d.action, d.rule, d.target_soc, d.power_w] if d is not None else None
        if dec != last.get("decision"):
            self.record.append({"t": t, "decision": dec})
            last["decision"] = dec
        pt = getattr(a, "_plan_time", None)
        if a.plan is not None and pt != last.get("plan_time"):
            last["plan_time"] = pt
            code = "".join({"grid_charge": "C", "export": "E", "hold": "H", "self_use": "s",
                            "force_discharge": "F"}.get(ps.action, "?") for ps in a.plan.slots[:PLAN_SLOTS])
            socs = [round(ps.soc_end) for ps in a.plan.slots[:PLAN_SLOTS:4]]
            if code != last.get("plan") or socs != last.get("socs"):
                self.record.append({"t": t, "plan": code, "soc": socs})
                last["plan"], last["socs"] = code, socs
        while len(a.calls) > last.get("calls", 0):
            c = a.calls[last.get("calls", 0)]
            self.record.append({k: v for k, v in c.items()})
            last["calls"] = last.get("calls", 0) + 1
        while len(a.warnings) > last.get("warn", 0):
            self.record.append({"t": t, "warning": a.warnings[last.get("warn", 0)][26:]})
            last["warn"] = last.get("warn", 0) + 1

    # --- scenario events: pause/resume, an AppDaemon restart, a rejected write
    def _process_events(self):
        while self.events and self.events[0][0] <= Clock.now:
            _, action = self.events.pop(0)
            kind = action[0]
            if kind == "pause":
                self.app.set_fake("switch.pe_ctl_pause", "on" if action[1] else "off")
                self.app._evaluate()
            elif kind == "restart":
                self._restart()
            elif kind == "reject":
                self.app.rejected.add(action[1])
            else:
                raise ValueError(f"unknown replay event: {action!r}")
            self.record.append({"t": Clock.now.isoformat(), "event": list(action)})

    def _restart(self):
        """Simulate an AppDaemon restart: a new PowerEngine instance over the same folder, sharing the same fake
        Home Assistant (so the inverter's actual state carries over, as it would through a real restart)."""
        old = self.app
        old.terminate()
        new = self.powerengine.PowerEngine()
        new.args = old.args
        for attr in ("states", "calls", "warnings", "fired", "timers", "rejected", "pending_pause"):
            setattr(new, attr, getattr(old, attr))
        self.app = new
        self.app.initialize()

    def run(self) -> list[dict]:
        Clock.now = self.start
        self.app.tz = None
        self._inputs()
        self._process_events()
        self.app.initialize()
        last: dict = {"calls": len(self.app.calls)}
        self._snap(last)
        t, n = self.start + STEP, 0
        while t < self.end:
            Clock.now = t
            self._inputs()
            self._process_events()
            due = [x for x in self.app.timers if x[0] <= t]
            self.app.timers = [x for x in self.app.timers if x[0] > t]
            for _, cb, kw in sorted(due, key=lambda x: x[0]):
                cb(kw)
            n += 1
            if n % 5 == 0:
                self.app._evaluate()
            self.app._cycle({})
            if self.app.pending_pause is not None:          # the app paused itself (daily write limit)
                on = self.app.pending_pause.upper() == "ON"
                self.app.pending_pause = None
                self.app.set_fake("switch.pe_ctl_pause", "on" if on else "off")
                self.record.append({"t": Clock.now.isoformat(), "event": ["self-pause", on]})
                self.app._evaluate()
            self._snap(last)
            t += STEP
        return self.record
