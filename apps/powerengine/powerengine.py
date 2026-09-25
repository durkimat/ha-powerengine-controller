"""PowerEngine AppDaemon entry point.

A thin adapter between AppDaemon/Home Assistant and pe_core. Decision logic
belongs in pe_core so it can be tested offline.

0.0.x builds are Passive-only: they publish PowerEngine's own entities over
MQTT and never write to the inverter or any other device.
"""

import json
from datetime import datetime, timezone

import appdaemon.plugins.hass.hassapi as hass

from pe_core import __version__
from pe_core.config import DEFAULT_PATHS, ConfigError, load_config
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

HEARTBEAT_SECONDS = 60


class PowerEngine(hass.Hass):
    def initialize(self):
        self.log(f"PowerEngine {__version__} starting (Passive-only build: nothing is controlled)")
        validate_definitions()

        # Optional override. Not "config_path": AppDaemon sets that arg itself.
        custom = self.args.get("settings_file")
        paths = [custom] if custom else list(DEFAULT_PATHS)
        cfg, used, error = None, None, None
        try:
            cfg, used = load_config(paths)
        except ConfigError as err:
            error = str(err)
            self.log(f"Config problem: {err}", level="WARNING")
        if cfg is None and error is None:
            self.log(f"No config.yaml found (looked in {', '.join(paths)}); running unconfigured.", level="WARNING")
        elif cfg is not None:
            self.log(f"Loaded config from {used}: {len(cfg.inputs)} inputs, {len(cfg.solar_plants)} solar plant(s)")

        self.mode = effective_mode(cfg, error)
        self.log(f"Operation mode: {self.mode.effective} ({self.mode.reason})")

        self.mqtt = self._mqtt_api()
        if self.mqtt is None:
            return

        if cfg is not None and cfg.remove_entities:
            self._remove_entities()
            return

        for ent in ENTITIES:
            self._publish(ent.discovery_topic, discovery_payload(ent, __version__))
        self._publish(AVAILABILITY_TOPIC, ONLINE)
        self._publish_state("diag_version", __version__)
        self._publish_state("diag_config_ok", "ON" if error is None and cfg is not None else "OFF",
                            {"reason": error or ("OK" if cfg else "No config.yaml yet"), "file": used})
        self._publish_state("cfg_operation_mode", self.mode.configured)
        self._publish_state("state_operation_mode", self.mode.effective, {"reason": self.mode.reason})
        self._beat({})
        self.run_every(self._beat, "now+60", HEARTBEAT_SECONDS)
        self.log(f"Published {len(ENTITIES)} entities under the PowerEngine device")

    def terminate(self):
        if getattr(self, "mqtt", None) is not None:
            self._publish(AVAILABILITY_TOPIC, OFFLINE)

    # --- helpers -------------------------------------------------------------------

    def _mqtt_api(self):
        try:
            api = self.get_plugin_api("MQTT")
        except Exception as err:  # plugin not configured
            api = None
            self.log(f"MQTT plugin error: {err}", level="WARNING")
        if api is None:
            self.log("MQTT plugin not configured in appdaemon.yaml; entities can't be published. "
                     "See the README (MQTT setup).", level="WARNING")
        return api

    def _publish(self, topic, payload):
        if not isinstance(payload, str):
            payload = json.dumps(payload)
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
