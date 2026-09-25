"""Loading and validating PowerEngine's config.yaml.

The file is owned by the app (written by the config page). This module only
reads and validates it; a missing file means "unconfigured", never an error.
"""

from __future__ import annotations

import os
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


class ConfigError(ValueError):
    """The config file exists but is not valid."""


@dataclass(frozen=True)
class Config:
    dry_run: bool = True
    remove_entities: bool = False
    inputs: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def parse_config(data: Any) -> Config:
    """Validate an already-loaded YAML document and return a Config."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("top level of config.yaml must be a mapping")

    version = data.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ConfigError(f"unsupported schema_version {version!r} (expected {SCHEMA_VERSION})")

    operation = data.get("operation") or {}
    if not isinstance(operation, dict):
        raise ConfigError("'operation' must be a mapping")
    dry_run = operation.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise ConfigError("'operation.dry_run' must be true or false")

    remove_entities = data.get("remove_entities", False)
    if not isinstance(remove_entities, bool):
        raise ConfigError("'remove_entities' must be true or false")

    inputs = data.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise ConfigError("'inputs' must be a mapping")
    for role, spec in inputs.items():
        if not isinstance(spec, dict) or not ({"entity", "value"} & spec.keys()):
            raise ConfigError(f"input '{role}' must have either 'entity' or 'value'")
        if "entity" in spec and "value" in spec:
            raise ConfigError(f"input '{role}' has both 'entity' and 'value'; use one")

    return Config(dry_run=dry_run, remove_entities=remove_entities, inputs=dict(inputs), raw=data)


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
