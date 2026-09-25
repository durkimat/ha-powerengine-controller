"""Loading and validating PowerEngine's config.yaml.

The file is owned by the app (written by the config page). This module only
reads and validates it; a missing file means "unconfigured", never an error.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from .roles import RETIRED_ROLES, ROLE_BY_KEY, is_forbidden_control

SCHEMA_VERSION = 1

# Where the AppDaemon add-on sees HA's config folder differs between add-on
# versions, so try both (first match wins).
DEFAULT_PATHS = (
    "/homeassistant/powerengine/config.yaml",
    "/config/powerengine/config.yaml",
)

KNOWN_KEYS = frozenset(
    {
        "schema_version",
        "inputs",
        "solar_plants",
        "system",
        "features",
        "safety",
        "tariff",
        "operation",
        "notifications",
        "remove_entities",
    }
)

MODES = ("passive", "active")
FORECAST_SOURCES = ("none", "solcast_site", "scaled")
FEATURES = ("fill_when_cheap", "smart_charge_optimisation", "arbitrage", "axle", "free_power_days")
FEATURE_DEFAULTS = {"fill_when_cheap": True, "smart_charge_optimisation": True, "arbitrage": False, "axle": True,
                    "free_power_days": True}
# name: (default, min, max) -- numeric safety settings, all validated
SAFETY = {
    "min_reserve_soc": (12, 0, 100),          # never plan to go below this (%)
    "cheap_threshold_p": (10.0, 0, 100),      # import at or below this is "cheap" (pence/kWh)
    "grid_charge_target_soc": (100, 10, 100), # charge to this in cheap periods (%)
    "charge_hysteresis_soc": (3, 0, 20),      # resume charging only below target minus this (%)
    "pre_axle_lookahead_h": (6.0, 0, 48),     # start protecting charge this long before an event (h)
    "axle_margin_soc": (5, 0, 50),            # extra above the event's needs (%)
    "main_fuse_a": (60, 20, 200),             # supply fuse; import is planned to stay under 90% of it (A)
    "ev_charger_kw": (7.4, 0, 22),            # car charger power, assumed during planned smart slots (kW)
    "export_limit_kw": (6.0, 0, 30),          # DNO-approved export limit (kW)
    "battery_wear_p": (2.0, 0, 20),           # wear cost per kWh cycled through the battery (p/kWh)
    "arbitrage_min_margin_p": (1.0, 0, 50),   # profit per kWh an arbitrage cycle must clear after losses + wear
}
SYSTEM_DEFAULTS = {"house_load_includes_ev": True}

# Phone notifications via the HA companion app (a notify.* service). Off until a service is chosen.
NOTIFY_EVENTS = {
    "health": (True, "Health problems", "When the Health tab finds a problem (checked after start-up and each night)."),
    "inputs": (True, "Inputs not working", "When a required input has been unavailable or stale for 15 minutes."),
    "axle": (True, "Axle events", "When an Axle event is scheduled, with its time."),
    "free_power": (True, "Free-power sessions", "When a free-electricity session is announced."),
    "daily": (False, "Daily summary", "Each morning: yesterday's cost and savings."),
}
_NOTIFY_SERVICE = re.compile(r"^notify\.[a-z0-9_]+$")

# Labels and one-line help for the config page (kept next to the defaults they describe).
SETTING_TEXT = {
    "min_reserve_soc": ("Minimum reserve", "%", "PowerEngine never plans to take the battery below this."),
    "cheap_threshold_p": ("Cheap import threshold", "p/kWh", "Import at or below this price counts as cheap."),
    "grid_charge_target_soc": ("Grid-charge target", "%", "How full to charge from the grid when import is cheap."),
    "charge_hysteresis_soc": ("Charge restart margin", "%", "Once full, restart only below target minus this."),
    "pre_axle_lookahead_h": ("Axle look-ahead", "h", "How long before an Axle event to start protecting charge."),
    "axle_margin_soc": ("Axle safety margin", "%", "Extra charge kept above what an Axle event needs."),
    "main_fuse_a": ("Main supply fuse", "A",
                    "Rating of the main fuse at the supply cutout. PowerEngine plans grid charging so house + car + "
                    "battery import stays under 90% of it (230 V), reducing battery charging first. Change it if "
                    "the fuse is upgraded."),
    "ev_charger_kw": ("Car charger power", "kW",
                      "What the car draws while charging (7.4 kW for a 32 A Zappi). Used to plan the fuse limit "
                      "during smart-charge slots."),
    "export_limit_kw": ("Export limit", "kW",
                        "The export limit your DNO approved. PowerEngine never plans to export more than this."),
    "battery_wear_p": ("Battery wear cost", "p/kWh",
                       "Cost of wear per kWh through the battery: battery price ÷ (capacity kWh × rated cycles). "
                       "E.g. £4,000 ÷ (18 × 8,000) ≈ 2.8p. Used only to decide whether arbitrage is worth it."),
    "arbitrage_min_margin_p": ("Arbitrage minimum profit", "p/kWh",
                               "Arbitrage runs only if, per kWh exported, export price − purchase price ÷ losses − "
                               "wear is at least this."),
    "house_load_includes_ev": ("House load includes the car charger", "",
                               "Tick if the car is inside the inverter's house load. PowerEngine then subtracts it and "
                               "stops the battery discharging into the car."),
}


# Config-page sections for the numeric settings (in display order).
SETTING_SECTIONS = (
    ("battery", "Battery and charging", ("min_reserve_soc", "cheap_threshold_p", "grid_charge_target_soc",
                                         "charge_hysteresis_soc")),
    ("limits", "Supply limits", ("main_fuse_a", "ev_charger_kw", "export_limit_kw")),
    ("axle", "Axle events", ("pre_axle_lookahead_h", "axle_margin_soc")),
    ("arbitrage", "Arbitrage", ("battery_wear_p", "arbitrage_min_margin_p")),
)


def settings_catalogue() -> dict:
    """Settings schema for the config page: defaults, ranges, labels, help."""
    order = [k for _, _, keys in SETTING_SECTIONS for k in keys]
    safety = [{"key": k, "default": SAFETY[k][0], "min": SAFETY[k][1], "max": SAFETY[k][2],
               "label": SETTING_TEXT[k][0], "unit": SETTING_TEXT[k][1], "help": SETTING_TEXT[k][2]} for k in order]
    system = [{"key": k, "default": d, "label": SETTING_TEXT[k][0], "help": SETTING_TEXT[k][2]}
              for k, d in SYSTEM_DEFAULTS.items()]
    return {"safety": safety, "system": system,
            "sections": [{"key": sec, "label": label, "keys": list(keys)} for sec, label, keys in SETTING_SECTIONS]}
_ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_PLANT_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


class ConfigError(ValueError):
    """The config file exists but is not valid."""


@dataclass(frozen=True)
class SolarPlant:
    id: str
    name: str
    power: dict[str, Any]
    energy_today: dict[str, Any]
    forecast: str = "none"
    enabled: bool = True


@dataclass(frozen=True)
class Config:
    mode: str = "passive"
    features: dict[str, bool] = field(default_factory=lambda: dict(FEATURE_DEFAULTS))
    safety: dict[str, float] = field(default_factory=lambda: {k: v[0] for k, v in SAFETY.items()})
    system: dict[str, bool] = field(default_factory=lambda: dict(SYSTEM_DEFAULTS))
    remove_entities: bool = False
    notifications: dict[str, Any] = field(default_factory=lambda: _parse_notifications(None))
    inputs: dict[str, Any] = field(default_factory=dict)
    solar_plants: tuple[SolarPlant, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


def _check_input_spec(label: str, spec: Any) -> None:
    if not isinstance(spec, dict) or not ({"entity", "value"} & spec.keys()):
        raise ConfigError(f"{label} must have either 'entity' or 'value'")
    if "entity" in spec and "value" in spec:
        raise ConfigError(f"{label} has both 'entity' and 'value'; use one")
    if "entity" in spec and not _ENTITY_ID.match(str(spec["entity"])):
        raise ConfigError(f"{label}: '{spec['entity']}' is not a valid entity id")
    if "invert" in spec and not isinstance(spec["invert"], bool):
        raise ConfigError(f"{label}: 'invert' must be true or false")


def _check_role(role_key: str, spec: dict) -> None:
    role = ROLE_BY_KEY.get(role_key)
    if role is None:
        raise ConfigError(f"unknown input '{role_key}'")
    _check_input_spec(f"input '{role_key}'", spec)
    if spec.get("invert") and not role.signed:
        raise ConfigError(f"input '{role_key}' can't be inverted")
    if "value" in spec:
        if not role.static_ok:
            raise ConfigError(f"input '{role_key}' must be an entity, not a fixed value")
        try:
            float(spec["value"])
        except (TypeError, ValueError):
            raise ConfigError(f"input '{role_key}': fixed value must be a number") from None
    else:
        domain = str(spec["entity"]).split(".", 1)[0]
        if domain not in role.domains:
            raise ConfigError(f"input '{role_key}' must be a {' or '.join(role.domains)} entity")
        if role.kind == "control" and is_forbidden_control(spec["entity"]):
            raise ConfigError(f"input '{role_key}': PowerEngine never writes to bump/boost entities")


def _parse_plants(data: Any) -> tuple[SolarPlant, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise ConfigError("'solar_plants' must be a list")
    plants, seen = [], set()
    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"solar plant #{i} must be a mapping")
        pid = item.get("id")
        if not isinstance(pid, str) or not _PLANT_ID.match(pid):
            raise ConfigError(f"solar plant #{i}: 'id' must be lowercase letters, digits or _ (max 24)")
        if pid in seen:
            raise ConfigError(f"solar plant id '{pid}' is used twice")
        seen.add(pid)
        for key in ("power", "energy_today"):
            _check_input_spec(f"solar plant '{pid}' {key}", item.get(key))
        forecast = item.get("forecast", "none")
        if forecast not in FORECAST_SOURCES:
            raise ConfigError(f"solar plant '{pid}': forecast must be one of {', '.join(FORECAST_SOURCES)}")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"solar plant '{pid}': 'enabled' must be true or false")
        plants.append(
            SolarPlant(
                id=pid,
                name=str(item.get("name") or pid),
                power=dict(item["power"]),
                energy_today=dict(item["energy_today"]),
                forecast=forecast,
                enabled=enabled,
            )
        )
    return tuple(plants)


def _parse_notifications(raw: Any) -> dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ConfigError("'notifications' must be a mapping")
    unknown = sorted(set(raw) - {"service", "events"})
    if unknown:
        raise ConfigError(f"unknown notification setting(s): {', '.join(unknown)}")
    service = raw.get("service") or ""
    if not isinstance(service, str) or (service and not _NOTIFY_SERVICE.match(service)):
        raise ConfigError("notifications.service must be a notify service, e.g. notify.mobile_app_my_phone")
    events = {k: v[0] for k, v in NOTIFY_EVENTS.items()}
    raw_events = raw.get("events") or {}
    if not isinstance(raw_events, dict):
        raise ConfigError("'notifications.events' must be a mapping")
    for key, value in raw_events.items():
        if key not in NOTIFY_EVENTS:
            raise ConfigError(f"unknown notification '{key}'")
        if not isinstance(value, bool):
            raise ConfigError(f"notification '{key}' must be true or false")
        events[key] = value
    return {"service": service, "events": events}


def parse_config(data: Any) -> Config:
    """Validate an already-loaded YAML document and return a Config."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("top level of config.yaml must be a mapping")

    unknown = sorted(set(data) - KNOWN_KEYS)
    if unknown:
        raise ConfigError(f"unknown top-level key(s): {', '.join(unknown)} (is this the right file?)")

    version = data.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ConfigError(f"unsupported schema_version {version!r} (expected {SCHEMA_VERSION})")

    operation = data.get("operation") or {}
    if not isinstance(operation, dict):
        raise ConfigError("'operation' must be a mapping")
    mode = operation.get("mode", "passive")
    if mode not in MODES:
        raise ConfigError("'operation.mode' must be 'passive' or 'active'")

    remove_entities = data.get("remove_entities", False)
    if not isinstance(remove_entities, bool):
        raise ConfigError("'remove_entities' must be true or false")

    inputs = data.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise ConfigError("'inputs' must be a mapping")
    inputs = {k: v for k, v in inputs.items() if k not in RETIRED_ROLES}
    for role_key, spec in inputs.items():
        _check_role(role_key, spec)

    features = dict(FEATURE_DEFAULTS)
    raw_features = data.get("features") or {}
    if not isinstance(raw_features, dict):
        raise ConfigError("'features' must be a mapping")
    for key, value in raw_features.items():
        if key not in FEATURES:
            raise ConfigError(f"unknown feature '{key}'")
        if not isinstance(value, bool):
            raise ConfigError(f"feature '{key}' must be true or false")
        features[key] = value

    safety = {k: v[0] for k, v in SAFETY.items()}
    raw_safety = data.get("safety") or {}
    if not isinstance(raw_safety, dict):
        raise ConfigError("'safety' must be a mapping")
    for key, value in raw_safety.items():
        if key not in SAFETY:
            raise ConfigError(f"unknown safety setting '{key}'")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"safety setting '{key}' must be a number")
        _, lo, hi = SAFETY[key]
        if not lo <= value <= hi:
            raise ConfigError(f"safety setting '{key}' must be between {lo} and {hi}")
        safety[key] = value
    if safety["min_reserve_soc"] >= safety["grid_charge_target_soc"]:
        raise ConfigError("minimum reserve must be below the grid-charge target")

    notifications = _parse_notifications(data.get("notifications"))

    system = dict(SYSTEM_DEFAULTS)
    raw_system = data.get("system") or {}
    if not isinstance(raw_system, dict):
        raise ConfigError("'system' must be a mapping")
    for key, value in raw_system.items():
        if key not in SYSTEM_DEFAULTS:
            raise ConfigError(f"unknown system setting '{key}'")
        if not isinstance(value, bool):
            raise ConfigError(f"system setting '{key}' must be true or false")
        system[key] = value

    return Config(
        mode=mode,
        features=features,
        safety=safety,
        system=system,
        remove_entities=remove_entities,
        notifications=notifications,
        inputs=dict(inputs),
        solar_plants=_parse_plants(data.get("solar_plants")),
        raw=data,
    )


def load_config(paths: tuple[str, ...] | list[str] = DEFAULT_PATHS) -> tuple[Config | None, str | None]:
    """Load the first config file that exists.

    Returns (None, None) when no file exists (the app then runs unconfigured).
    Raises ConfigError when a file exists but is invalid.
    """
    for path in paths:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                try:
                    data = yaml.safe_load(fh)
                except yaml.YAMLError as err:
                    raise ConfigError(f"{path} is not valid YAML: {err}") from err
            return parse_config(data), path
    return None, None


BATTERY_PAIR = ("battery_charge_power", "battery_discharge_power")


def uses_battery_pair(cfg: Config) -> bool:
    """True when separate charging/discharging sensors replace the single (unsigned) battery power sensor."""
    return all("entity" in (cfg.inputs.get(k) or {}) for k in BATTERY_PAIR)


def required_roles(cfg: Config) -> list[str]:
    """Role keys that must be mapped, given which features are switched on."""
    feature_for = {"axle": "axle", "free_power": "free_power_days"}
    pair = uses_battery_pair(cfg)
    keys = []
    for role in ROLE_BY_KEY.values():
        if role.required == "yes" or (role.required in feature_for and cfg.features.get(feature_for[role.required])):
            keys.append(role.key)
    if pair:                               # the pair replaces the single sensor, and both become required
        keys = [k for k in keys if k != "battery_power"] + [k for k in BATTERY_PAIR if k not in keys]
    return keys
