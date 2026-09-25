"""PowerEngine's Home Assistant entities, published via MQTT discovery.

Pure data: this module builds topics and payloads; the AppDaemon adapter only
publishes them. Every entity follows the naming scheme
    <component>.pe_<group>_<name>
and belongs to the single "PowerEngine" device.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

BASE_TOPIC = "powerengine"
DISCOVERY_PREFIX = "homeassistant"
AVAILABILITY_TOPIC = f"{BASE_TOPIC}/status"
ONLINE, OFFLINE = "online", "offline"
REPO_URL = "https://github.com/durkimat/ha-powerengine-controller"

GROUPS = ("cfg", "ctl", "state", "plan", "map", "diag", "cost", "event")
_KEY = re.compile(r"^(" + "|".join(GROUPS) + r")_[a-z0-9_]+$")

# HA only allows entity_category "config" on controllable entities (switch,
# number, select, button...). Read-only entities use "diagnostic" or none.
_CONFIG_CATEGORY_ALLOWED = {"switch", "number", "select", "button", "text", "datetime"}


@dataclass(frozen=True)
class EntityDef:
    component: str          # sensor, binary_sensor, switch, ...
    key: str                # e.g. "diag_version" -> sensor.pe_diag_version
    name: str               # shown after the device name: "PowerEngine <name>"
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def entity_id(self) -> str:
        return f"{self.component}.pe_{self.key}"

    @property
    def unique_id(self) -> str:
        return f"powerengine_{self.key}"

    @property
    def state_topic(self) -> str:
        return f"{BASE_TOPIC}/{self.key}/state"

    @property
    def attributes_topic(self) -> str:
        return f"{BASE_TOPIC}/{self.key}/attributes"

    @property
    def discovery_topic(self) -> str:
        return f"{DISCOVERY_PREFIX}/{self.component}/{BASE_TOPIC}/{self.key}/config"


# --- the entities this build publishes -------------------------------------------------

ENTITIES: tuple[EntityDef, ...] = (
    EntityDef("sensor", "diag_version", "Version",
              {"icon": "mdi:tag-outline", "entity_category": "diagnostic"}),
    EntityDef("sensor", "diag_heartbeat", "Heartbeat",
              {"device_class": "timestamp", "entity_category": "diagnostic", "expire_after": 300}),
    EntityDef("binary_sensor", "diag_config_ok", "Config OK",
              {"icon": "mdi:file-check-outline", "entity_category": "diagnostic"}),
    EntityDef("sensor", "cfg_operation_mode", "Configured mode",
              {"icon": "mdi:cog-outline", "entity_category": "diagnostic",
               "device_class": "enum", "options": ["unconfigured", "passive", "active"]}),
    EntityDef("sensor", "map_config", "Input mapping",
              {"icon": "mdi:link-variant", "entity_category": "diagnostic"}),
    EntityDef("sensor", "map_catalogue", "Input catalogue",
              {"icon": "mdi:format-list-bulleted", "entity_category": "diagnostic"}),
    EntityDef("sensor", "state_operation_mode", "Operation mode",
              {"icon": "mdi:power-settings",
               "device_class": "enum", "options": ["unconfigured", "passive", "active"]}),
)

POWER = {"device_class": "power", "unit_of_measurement": "W", "state_class": "measurement"}
RATE = {"unit_of_measurement": "GBP/kWh", "state_class": "measurement", "icon": "mdi:currency-gbp",
        "suggested_display_precision": 4}

STATE_ENTITIES: tuple[EntityDef, ...] = (
    EntityDef("sensor", "state_summary", "Status", {"icon": "mdi:text-box-outline"}),
    EntityDef("sensor", "state_decision", "Decision",
              {"icon": "mdi:head-cog-outline", "device_class": "enum",
               "options": ["self_use", "grid_charge", "hold", "force_discharge", "none"]}),
    EntityDef("sensor", "state_activity", "Activity", {"icon": "mdi:history"}),
    EntityDef("sensor", "state_battery_soc", "Battery",
              {"device_class": "battery", "unit_of_measurement": "%", "state_class": "measurement"}),
    EntityDef("sensor", "state_battery_power", "Battery power", {**POWER, "icon": "mdi:home-battery"}),
    EntityDef("sensor", "state_grid_power", "Grid power", {**POWER, "icon": "mdi:transmission-tower"}),
    EntityDef("sensor", "state_solar_power", "Solar power", {**POWER, "icon": "mdi:solar-power"}),
    EntityDef("sensor", "state_house_power", "House power", {**POWER, "icon": "mdi:home-lightning-bolt"}),
    EntityDef("sensor", "state_ev_power", "Car charging power", {**POWER, "icon": "mdi:car-electric"}),
    EntityDef("sensor", "state_import_rate", "Import rate", RATE),
    EntityDef("sensor", "state_export_rate", "Export rate", RATE),
    EntityDef("sensor", "state_ev", "Car", {"icon": "mdi:car-electric"}),
    EntityDef("sensor", "state_smart_charge", "Smart charge", {"icon": "mdi:ev-station"}),
    EntityDef("sensor", "state_axle", "Axle", {"icon": "mdi:transmission-tower-export"}),
    EntityDef("sensor", "state_free_power", "Free power", {"icon": "mdi:gift-outline"}),
)

ENTITIES = ENTITIES + STATE_ENTITIES


def device(version: str) -> dict[str, Any]:
    return {
        "identifiers": ["powerengine"],
        "name": "PowerEngine",
        "manufacturer": "PowerEngine",
        "model": "AppDaemon app",
        "sw_version": version,
        "configuration_url": REPO_URL,
    }


def discovery_payload(ent: EntityDef, version: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": ent.name,
        "has_entity_name": True,
        "unique_id": ent.unique_id,
        "default_entity_id": ent.entity_id,
        "state_topic": ent.state_topic,
        "json_attributes_topic": ent.attributes_topic,
        "availability_topic": AVAILABILITY_TOPIC,
        "payload_available": ONLINE,
        "payload_not_available": OFFLINE,
        "device": device(version),
        "origin": {"name": "PowerEngine", "sw_version": version, "support_url": REPO_URL},
    }
    if ent.component == "binary_sensor":
        payload.update({"payload_on": "ON", "payload_off": "OFF"})
    payload.update(ent.options)
    return payload


def removal_messages() -> list[tuple[str, str]]:
    """(topic, payload) pairs that delete every entity and its retained state."""
    msgs: list[tuple[str, str]] = []
    for ent in ENTITIES:
        msgs += [(ent.discovery_topic, ""), (ent.state_topic, ""), (ent.attributes_topic, "")]
    msgs.append((AVAILABILITY_TOPIC, ""))
    return msgs


def validate_definitions(entities: tuple[EntityDef, ...] = ENTITIES) -> None:
    """Raise ValueError if any definition breaks the naming or metadata rules."""
    seen: set[str] = set()
    for ent in entities:
        if not _KEY.match(ent.key):
            raise ValueError(f"{ent.key}: key must be <group>_<name> with group in {GROUPS}")
        if ent.unique_id in seen:
            raise ValueError(f"duplicate entity {ent.unique_id}")
        seen.add(ent.unique_id)
        if ent.options.get("entity_category") == "config" and ent.component not in _CONFIG_CATEGORY_ALLOWED:
            raise ValueError(f"{ent.entity_id}: HA does not allow entity_category 'config' on {ent.component}")
