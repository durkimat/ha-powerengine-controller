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
    remove_entities: bool = False
    inputs: dict[str, Any] = field(default_factory=dict)
    solar_plants: tuple[SolarPlant, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


def _check_input_spec(label: str, spec: Any) -> None:
    if not isinstance(spec, dict) or not ({"entity", "value"} & spec.keys()):
        raise ConfigError(f"{label} must have either 'entity' or 'value'")
    if "entity" in spec and "value" in spec:
        raise ConfigError(f"{label} has both 'entity' and 'value'; use one")


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
    for role, spec in inputs.items():
        _check_input_spec(f"input '{role}'", spec)

    return Config(
        mode=mode,
        remove_entities=remove_entities,
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
