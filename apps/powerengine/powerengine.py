"""PowerEngine AppDaemon entry point.

A thin adapter between AppDaemon/Home Assistant and pe_core. Decision logic
belongs in pe_core so it can be tested offline.

0.0.x builds are Passive-only: they publish PowerEngine's own entities over
MQTT and never write to the inverter or any other device.
"""

import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import appdaemon.plugins.hass.hassapi as hass

from pe_core import __version__
from pe_core.activity import ActivityLog
from pe_core.checks import OK, check, summarise
from pe_core.config import DEFAULT_PATHS, ConfigError, load_config, required_roles, settings_catalogue
from pe_core.dashboard import sync_dashboard
from pe_core.decide import decide
from pe_core.entities import (
    AVAILABILITY_TOPIC,
    ENTITIES,
    OFFLINE,
    ONLINE,
    discovery_payload,
    removal_messages,
    validate_definitions,
)
from pe_core.modes import effective_mode
from pe_core.readings import read
from pe_core.roles import ROLE_BY_KEY, ROLES, catalogue
from pe_core.status import entity_states
from pe_core.store import save_config

HEARTBEAT_SECONDS = 60
CYCLE_SECONDS = 30
RECHECK_SECONDS = 300
SAVE_EVENT = "pe_config_save"
RESULT_EVENT = "pe_config_result"


class PowerEngine(hass.Hass):
    def initialize(self):
        self.log(f"PowerEngine {__version__} starting (Passive-only build: nothing is controlled)")
        validate_definitions()

        # Optional override. Not "config_path": AppDaemon sets that arg itself.
        custom = self.args.get("settings_file")
        self.paths = [custom] if custom else list(DEFAULT_PATHS)
        self.cfg, self.cfg_path, self.cfg_error = None, None, None
        try:
            self.cfg, self.cfg_path = load_config(self.paths)
        except ConfigError as err:
            self.cfg_error = str(err)
            self.log(f"Config problem: {err}", level="WARNING")
        if self.cfg is None and self.cfg_error is None:
            looked = ", ".join(self.paths)
            self.log(f"No config.yaml found (looked in {looked}); running unconfigured.", level="WARNING")
        elif self.cfg is not None:
            self.log(f"Loaded config from {self.cfg_path}: {len(self.cfg.inputs)} inputs, "
                     f"{len(self.cfg.solar_plants)} solar plant(s)")

        self.mqtt = self._mqtt_api()
        if self.mqtt is None:
            return
        if self.cfg is not None and self.cfg.remove_entities:
            self._remove_entities()
            return

        for ent in ENTITIES:
            self._publish(ent.discovery_topic, discovery_payload(ent, __version__))
        self._publish(AVAILABILITY_TOPIC, ONLINE)
        self._publish_state("diag_version", __version__)
        self._publish_state("map_catalogue", str(len(ROLES)), {**catalogue(), "settings": settings_catalogue()})
        self._last_checks = None
        self._published = {}
        self._decision, self._since = None, None
        try:   # keep the activity log across restarts (it lives in the entity's attributes)
            saved = self.get_state("sensor.pe_state_activity", attribute="entries")
        except Exception:
            saved = None
        self.activity = ActivityLog(saved if isinstance(saved, list) else None)
        try:
            self.tz = ZoneInfo(str(self.get_timezone()))
        except Exception:
            self.tz = None
        self._evaluate()                                   # also publishes mode + config status
        self._cycle({})
        self._sync_dashboard()

        self.listen_event(self._on_save, SAVE_EVENT)
        self._beat({})
        self.run_every(self._beat, "now+60", HEARTBEAT_SECONDS)
        self.run_every(lambda kwargs: self._evaluate(), f"now+{RECHECK_SECONDS}", RECHECK_SECONDS)
        self.run_every(self._cycle, f"now+{CYCLE_SECONDS}", CYCLE_SECONDS)
        self.log(f"Published {len(ENTITIES)} entities under the PowerEngine device")

    def terminate(self):
        if getattr(self, "mqtt", None) is not None:
            self._publish(AVAILABILITY_TOPIC, OFFLINE)

    # --- mapping checks and mode -----------------------------------------------------

    def _evaluate(self):
        checks, required = {}, []
        if self.cfg is not None:
            required = required_roles(self.cfg)
            for role in ROLES:
                spec = self.cfg.inputs.get(role.key)
                state = None
                if spec and "entity" in spec:
                    state = self.get_state(spec["entity"], attribute="all")
                checks[role.key] = check(role, spec, state)
        missing = [k for k in required if checks.get(k, ("unmapped", ""))[0] != OK]
        mode = effective_mode(self.cfg, self.cfg_error, missing_required=missing)
        self.mode = mode

        if self.cfg_error:
            overall = "error"
        elif self.cfg is None:
            overall = "unconfigured"
        else:
            overall = summarise(checks, required)

        signature = (overall, mode, tuple(sorted(checks.items())))
        if signature == self._last_checks:
            return
        self._last_checks = signature
        self._publish_state("diag_config_ok", "ON" if overall in ("ok", "warnings") else "OFF",
                            {"reason": self.cfg_error or overall, "file": self.cfg_path})
        self._publish_state("cfg_operation_mode", mode.configured)
        self._publish_state("state_operation_mode", mode.effective, {"reason": mode.reason})
        self._publish_state("map_config", overall, {
            "config": self.cfg.raw if self.cfg else {},
            "checks": {k: {"status": s, "message": m} for k, (s, m) in checks.items()},
            "required": required,
            "file": self.cfg_path,
            "save_path": self._save_path(),
            "error": self.cfg_error,
        })
        self.log(f"Inputs: {overall}; mode {mode.effective} ({mode.reason})")

    # --- the monitoring cycle ------------------------------------------------------

    def _cycle(self, kwargs):
        """Read inputs, decide (Passive: would-do only), then publish entities that changed."""
        readings, decision = None, None
        if self.cfg is not None and self.mode.effective != "unconfigured":
            try:
                readings = read(self.cfg, lambda eid: self.get_state(eid, attribute="all"))
                decision = decide(readings, self.cfg, self._decision, self.tz)
            except Exception as err:          # never let one bad reading stop the app
                self.log(f"Reading or deciding failed: {err!r}", level="WARNING")
        if decision is not None:
            passive = self.mode.effective != "active"
            entry = self.activity.record(decision, readings.now, passive, self.tz)
            if entry:
                self._since = entry["hhmm"]
                self.log(f"Decision: {entry['text']}")
                self.call_service("logbook/log", name="PowerEngine", message=entry["text"],
                                  entity_id="sensor.pe_state_decision")
                self._publish_state("state_activity", entry["time"], {"entries": self.activity.entries})
            self._decision = decision
        for key, (state, attrs) in entity_states(readings, self.mode, self.tz, decision, self._since).items():
            if self._published.get(key) != (state, attrs):
                self._published[key] = (state, attrs)
                self._publish_state(key, state, attrs)

    def _sync_dashboard(self):
        target = os.path.join(os.path.dirname(self._save_path()), "dashboard.yaml")
        try:
            if sync_dashboard(target):
                self.log(f"Dashboard updated: {target} (refresh the PowerEngine dashboard to see it)")
        except OSError as err:
            self.log(f"Could not write the dashboard file {target}: {err}", level="WARNING")

    # --- saving from the config card -------------------------------------------------

    def _save_path(self):
        if self.cfg_path:
            return self.cfg_path
        for path in self.paths:
            root = os.path.dirname(os.path.dirname(path))   # e.g. /homeassistant
            if os.path.isdir(root):
                return path
        return self.paths[0]

    def _on_save(self, event_name, data, kwargs):
        new = data.get("config")
        user = self._user_name(data)
        try:
            if not isinstance(new, dict):
                raise ConfigError("no configuration received")
            _, backup = save_config(self._save_path(), new)
        except (ConfigError, OSError) as err:
            self.log(f"Config save by {user} rejected: {err}", level="WARNING")
            self.fire_event(RESULT_EVENT, ok=False, message=str(err))
            return
        changed = self._changes(self.cfg.raw if self.cfg else {}, new)
        self.log(f"Config saved by {user} ({changed}); backup: {backup or 'none (first save)'}")
        self.call_service("logbook/log", name="PowerEngine",
                          message=f"configuration saved by {user}: {changed}",
                          entity_id="sensor.pe_map_config")
        self._reload()
        self.fire_event(RESULT_EVENT, ok=True, message=f"Saved. {changed}.")

    def _reload(self):
        """Re-read config.yaml in place and republish status (no app restart)."""
        self.cfg_error = None
        try:
            self.cfg, self.cfg_path = load_config(self.paths)
        except ConfigError as err:
            self.cfg, self.cfg_error = None, str(err)
        self._last_checks = None
        self._evaluate()
        self._cycle({})

    def _user_name(self, data):
        ctx = (data.get("metadata") or {}).get("context") or data.get("context") or {}
        uid = ctx.get("user_id")
        if uid:
            for person in (self.get_state("person") or {}).values():
                if (person.get("attributes") or {}).get("user_id") == uid:
                    return person["attributes"].get("friendly_name", uid)
        return uid or "unknown user"

    @staticmethod
    def _changes(old, new):
        o, n = old.get("inputs") or {}, new.get("inputs") or {}
        changed = sorted(k for k in set(o) | set(n) if o.get(k) != n.get(k))
        other = [k for k in set(old) | set(new) if k != "inputs" and old.get(k) != new.get(k)]
        parts = []
        if changed:
            labels = [ROLE_BY_KEY[k].label if k in ROLE_BY_KEY else k for k in changed]
            more = "…" if len(labels) > 6 else ""
            parts.append(f"{len(changed)} input(s) changed: " + ", ".join(labels[:6]) + more)
        if other:
            parts.append("also " + ", ".join(sorted(other)))
        return "; ".join(parts) or "no changes"

    # --- helpers -------------------------------------------------------------------

    def _mqtt_api(self):
        try:
            api = self.get_plugin_api("MQTT")
        except Exception as err:  # plugin not configured
            api = None
            self.log(f"MQTT plugin error: {err}", level="WARNING")
        if api is None:
            self.log("MQTT plugin not configured in appdaemon.yaml; entities can't be published. "
                     "See docs/INSTALL.md, Step 3.", level="WARNING")
        return api

    def _publish(self, topic, payload):
        if not isinstance(payload, str):
            payload = json.dumps(payload, default=str)
        self.mqtt.mqtt_publish(topic, payload, qos=1, retain=True)

    def _publish_state(self, key, state, attributes=None):
        self._publish(f"powerengine/{key}/state", str(state))
        if attributes is not None:
            self._publish(f"powerengine/{key}/attributes", attributes)

    def _beat(self, kwargs):
        self._publish_state("diag_heartbeat", datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def _remove_entities(self):
        for topic, payload in removal_messages():
            self._publish(topic, payload)
        self.log("remove_entities is set: removed all PowerEngine entities. The app is now idle; "
                 "remove it in HACS or set remove_entities: false to bring them back.", level="WARNING")
