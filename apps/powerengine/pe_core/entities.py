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

GROUPS = ("cfg", "ctl", "state", "plan", "map", "diag", "cost", "event", "ui")
_KEY = re.compile(r"^(" + "|".join(GROUPS) + r")(_[a-z0-9_]+)?$")   # e.g. plan, plan_next_mode

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
    EntityDef("sensor", "diag_health", "Health",
              {"icon": "mdi:stethoscope", "device_class": "enum",
               "options": ["ok", "warnings", "problems", "Not set up yet"]}),
    EntityDef("sensor", "diag_started", "Started",
              {"device_class": "timestamp", "entity_category": "diagnostic", "icon": "mdi:restart"}),
    EntityDef("sensor", "diag_control", "Inverter control preview",
              {"icon": "mdi:tune-vertical", "entity_category": "diagnostic"}),
    EntityDef("sensor", "diag_inverter_clock", "Inverter clock drift",
              {"icon": "mdi:clock-alert-outline", "entity_category": "diagnostic", "unit_of_measurement": "s"}),
    EntityDef("sensor", "diag_test_write", "Supervised test",
              {"icon": "mdi:test-tube", "entity_category": "diagnostic"}),
    EntityDef("sensor", "diag_grid_check", "Grid meter cross-check",
              {"icon": "mdi:meter-electric-outline", "unit_of_measurement": "W", "entity_category": "diagnostic"}),
    EntityDef("sensor", "diag_log", "PowerEngine log", {"icon": "mdi:text-box-search-outline",
                                                        "entity_category": "diagnostic"}),
    EntityDef("sensor", "diag_update", "Update", {"icon": "mdi:update"}),
    EntityDef("sensor", "diag_writes_today", "Inverter writes today",
              {"icon": "mdi:memory", "unit_of_measurement": "writes"}),
    EntityDef("sensor", "diag_inverter_writes", "Inverter writes per day",
              {"icon": "mdi:memory", "entity_category": "diagnostic", "unit_of_measurement": "writes/day"}),
    EntityDef("sensor", "diag_battery_capacity", "Battery usable capacity (measured)",
              {"unit_of_measurement": "kWh", "icon": "mdi:battery-high", "entity_category": "diagnostic",
               "suggested_display_precision": 1}),
    EntityDef("sensor", "diag_battery_efficiency", "Battery round-trip efficiency",
              {"unit_of_measurement": "%", "state_class": "measurement", "icon": "mdi:battery-sync-outline",
               "entity_category": "diagnostic", "suggested_display_precision": 1}),
    EntityDef("sensor", "diag_learned", "Learned from use",
              {"icon": "mdi:school-outline", "entity_category": "diagnostic"}),
    EntityDef("sensor", "diag_lowwrite", "Low-write study",
              {"icon": "mdi:content-save-cog-outline", "entity_category": "diagnostic"}),
    EntityDef("sensor", "diag_battery_temperature", "Battery temperature (estimated)",
              {"unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement",
               "entity_category": "diagnostic", "suggested_display_precision": 1}),
    EntityDef("sensor", "diag_system_losses", "System losses yesterday",
              {"device_class": "energy", "unit_of_measurement": "kWh", "icon": "mdi:fire",
               "entity_category": "diagnostic", "suggested_display_precision": 1}),
    EntityDef("sensor", "map_config", "Input mapping",
              {"icon": "mdi:link-variant", "entity_category": "diagnostic"}),
    EntityDef("sensor", "map_settings", "Settings catalogue",
              {"icon": "mdi:tune-variant", "entity_category": "diagnostic"}),
    EntityDef("sensor", "map_catalogue", "Input catalogue",
              {"icon": "mdi:format-list-bulleted", "entity_category": "diagnostic"}),
    EntityDef("sensor", "state_operation_mode", "Operation mode",
              {"icon": "mdi:power-settings",
               "device_class": "enum", "options": ["unconfigured", "passive", "active", "paused"]}),
)

POWER = {"device_class": "power", "unit_of_measurement": "W", "state_class": "measurement"}
RATE = {"unit_of_measurement": "GBP/kWh", "state_class": "measurement", "icon": "mdi:currency-gbp",
        "suggested_display_precision": 4}

STATE_ENTITIES: tuple[EntityDef, ...] = (
    EntityDef("sensor", "state_summary", "Status", {"icon": "mdi:text-box-outline"}),
    EntityDef("sensor", "state_status", "Mode (at a glance)", {"icon": "mdi:power-settings"}),
    EntityDef("sensor", "state_decision", "Decision",
              {"icon": "mdi:head-cog-outline", "device_class": "enum",
               "options": ["self_use", "grid_charge", "hold", "force_discharge", "export", "none"]}),
    EntityDef("sensor", "state_activity", "Activity", {"icon": "mdi:history"}),
    EntityDef("sensor", "state_control_method", "Inverter control", {"icon": "mdi:tune-variant"}),
    EntityDef("sensor", "state_battery_soc", "Battery",
              {"device_class": "battery", "unit_of_measurement": "%", "state_class": "measurement"}),
    EntityDef("sensor", "state_battery_power", "Battery power", {**POWER, "icon": "mdi:home-battery"}),
    EntityDef("sensor", "state_grid_power", "Grid power", {**POWER, "icon": "mdi:transmission-tower"}),
    EntityDef("sensor", "state_solar_power", "Solar power", {**POWER, "icon": "mdi:solar-power"}),
    EntityDef("sensor", "state_house_power", "House power", {**POWER, "icon": "mdi:home-lightning-bolt"}),
    EntityDef("sensor", "state_load_power", "Total load (house + car)", {**POWER, "icon": "mdi:home-import-outline"}),
    EntityDef("sensor", "state_ev_power", "Car charging power", {**POWER, "icon": "mdi:car-electric"}),
    EntityDef("sensor", "state_import_rate", "Import rate", RATE),
    EntityDef("sensor", "state_export_rate", "Export rate", RATE),
    EntityDef("sensor", "state_ev", "Car", {"icon": "mdi:car-electric"}),
    EntityDef("sensor", "state_smart_charge", "Smart charge", {"icon": "mdi:ev-station"}),
    EntityDef("sensor", "state_axle", "Axle", {"icon": "mdi:transmission-tower-export"}),
    EntityDef("sensor", "state_free_power", "Free power", {"icon": "mdi:gift-outline"}),
)

PLAN_ENTITIES: tuple[EntityDef, ...] = (
    EntityDef("sensor", "plan", "Plan", {"device_class": "timestamp", "icon": "mdi:calendar-clock"}),
    EntityDef("sensor", "plan_headline", "Plan headline", {"icon": "mdi:text-box-outline"}),
    EntityDef("sensor", "plan_next", "Next in the plan", {"icon": "mdi:arrow-right-circle-outline"}),
    EntityDef("sensor", "plan_next_mode", "Next planned mode",
              {"icon": "mdi:skip-next-outline", "device_class": "enum",
               "options": ["self_use", "grid_charge", "hold", "force_discharge", "export", "none"]}),
    EntityDef("sensor", "plan_next_start", "Next planned change", {"device_class": "timestamp"}),
    EntityDef("sensor", "plan_next_target_soc", "Next planned target",
              {"unit_of_measurement": "%", "icon": "mdi:battery-arrow-up"}),
    EntityDef("sensor", "state_sim_soc", "Simulated battery",
              {"device_class": "battery", "unit_of_measurement": "%", "state_class": "measurement",
               "icon": "mdi:battery-sync"}),
)

MONEY = {"unit_of_measurement": "GBP", "icon": "mdi:cash", "suggested_display_precision": 2}

COST_ENTITIES: tuple[EntityDef, ...] = (
    EntityDef("sensor", "cost_today", "Cost today", MONEY),
    EntityDef("sensor", "cost_saved_today", "Saved today", MONEY),
    EntityDef("sensor", "cost_days", "Daily costs", MONEY),
    EntityDef("sensor", "cost_waterfall", "Savings waterfall", {**MONEY, "icon": "mdi:chart-waterfall"}),
    EntityDef("sensor", "cost_simulator", "Tariff simulator", {"icon": "mdi:scale-balance"}),
    EntityDef("sensor", "cost_simulator_year", "Tariff simulator (year, heat pump)", {"icon": "mdi:scale-balance"}),
    EntityDef("sensor", "event_last", "Last special event", {**MONEY, "icon": "mdi:star-outline"}),
    EntityDef("sensor", "event_months", "Special events this month", {**MONEY, "icon": "mdi:calendar-star"}),
)

# Dashboard display preferences. Handled by HA and the MQTT broker: the command topic is also the state topic and is
# retained, so HA's own command comes straight back as the state. Not optimistic, so HA shows a normal toggle
# (an optimistic switch is drawn as two lightning-bolt buttons).
_UI_TOPIC = f"{BASE_TOPIC}/ui_right_align/set"
UI_ENTITIES: tuple[EntityDef, ...] = (
    EntityDef("switch", "ui_right_align", "Right-align numbers",
              {"icon": "mdi:format-align-right", "entity_category": "config", "command_topic": _UI_TOPIC,
               "state_topic": _UI_TOPIC, "optimistic": False, "retain": True}),
)

# Pause: stops all inverter writes in Active mode (after handing the inverter back to Self-Use once). Same
# retained command/state topic pattern as the UI switch, so the choice survives restarts of HA and the app.
PAUSE_TOPIC = f"{BASE_TOPIC}/ctl_pause/set"
CONTROL_SWITCHES: tuple[EntityDef, ...] = (
    EntityDef("switch", "ctl_pause", "Pause control",
              {"icon": "mdi:pause-octagon", "command_topic": PAUSE_TOPIC, "state_topic": PAUSE_TOPIC,
               "optimistic": False, "retain": True}),
)

# Plan history tab: the day is picked with the card's date picker (event pe_history_day), so only the sensor is left.
HISTORY_ENTITIES: tuple[EntityDef, ...] = (
    EntityDef("sensor", "plan_history", "Plan history", {"icon": "mdi:history"}),
)

# Entities an earlier release published that no longer exist. The app retires them at every start (the retained
# discovery, state and command topics are cleared; direct mode removes or marks the HA state), so an upgrade leaves
# nothing behind. Only the fields retirement reads are kept. 0.9.72-0.9.74: the Costs tab's custom date range.
# 0.9.84: the Plan history tab's plan and day choices.
RETIRED_ENTITIES: tuple[EntityDef, ...] = (
    EntityDef("select", "ui_cost_from", "Costs range from", {"command_topic": f"{BASE_TOPIC}/ui_cost_from/set"}),
    EntityDef("select", "ui_cost_to", "Costs range to", {"command_topic": f"{BASE_TOPIC}/ui_cost_to/set"}),
    EntityDef("select", "ui_history_plan", "History plan", {"command_topic": f"{BASE_TOPIC}/ui_history_plan/set"}),
    EntityDef("select", "ui_history_day", "History day", {"command_topic": f"{BASE_TOPIC}/ui_history_day/set"}),
)

ENTITIES = ENTITIES + STATE_ENTITIES + PLAN_ENTITIES + COST_ENTITIES + UI_ENTITIES + CONTROL_SWITCHES + HISTORY_ENTITIES


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


def entity_removal_messages(ent: EntityDef) -> list[tuple[str, str]]:
    """(topic, payload) pairs that delete one entity and its retained state."""
    msgs = [(ent.discovery_topic, ""), (ent.state_topic, ""), (ent.attributes_topic, "")]
    if "command_topic" in ent.options:                            # retained UI preference
        msgs.append((ent.options["command_topic"], ""))
    return msgs


def removal_messages() -> list[tuple[str, str]]:
    """(topic, payload) pairs that delete every entity and its retained state."""
    msgs: list[tuple[str, str]] = []
    for ent in ENTITIES:
        msgs += entity_removal_messages(ent)
    msgs.append((AVAILABILITY_TOPIC, ""))
    return msgs


def solar_plant_entity(plant_id: str, name: str) -> EntityDef:
    """The per-plant solar power sensor for one enabled solar plant (not in ENTITIES: published/retired
    dynamically as `cfg.solar_plants` changes; see PowerEngine._sync_solar_entities)."""
    return EntityDef("sensor", f"state_solar_{plant_id}_power", f"Solar power ({name})",
                     {**POWER, "icon": "mdi:solar-power"})


DEVICE_PREFIX = "sensor.pe_state_dev_"
DEVICE_FIELDS = {          # reading field -> (entity key suffix, label, options)
    "soc": ("soc", "battery", {"device_class": "battery", "unit_of_measurement": "%", "state_class": "measurement"}),
    "battery_power": ("battery_power", "battery power", {**POWER, "icon": "mdi:home-battery"}),
    "solar_power": ("solar_power", "solar power", {**POWER, "icon": "mdi:solar-power"}),
}


def device_entity(device_id: str, name: str, field_name: str) -> EntityDef:
    """One sensor of a read-only device (M1: not in ENTITIES; published and retired dynamically as `cfg.devices`
    changes, see PowerEngine._sync_device_entities). field_name is a key of DEVICE_FIELDS."""
    suffix, label, options = DEVICE_FIELDS[field_name]
    return EntityDef("sensor", f"state_dev_{device_id}_{suffix}", f"{name} {label}", options)


def device_ref_from_entity(entity_id: str) -> tuple[str, str] | None:
    """(device id, reading field) of a device sensor's entity id, or None if it isn't one."""
    if not entity_id.startswith(DEVICE_PREFIX):
        return None
    rest = entity_id[len(DEVICE_PREFIX):]
    for field_name, (suffix, _label, _opts) in DEVICE_FIELDS.items():
        if rest.endswith("_" + suffix) and len(rest) > len(suffix) + 1:
            return rest[:-len(suffix) - 1], field_name
    return None


SOLAR_PLANT_PREFIX, SOLAR_PLANT_SUFFIX = "sensor.pe_state_solar_", "_power"


def solar_plant_id_from_entity(entity_id: str) -> str | None:
    """The plant id encoded in a per-plant solar power sensor's entity id, or None if it isn't one (including
    the total, sensor.pe_state_solar_power, which has no id in between)."""
    if not (entity_id.startswith(SOLAR_PLANT_PREFIX) and entity_id.endswith(SOLAR_PLANT_SUFFIX)):
        return None
    pid = entity_id[len(SOLAR_PLANT_PREFIX):-len(SOLAR_PLANT_SUFFIX)]
    return pid or None


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
