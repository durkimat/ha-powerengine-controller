"""Writing config.yaml safely: validate first, back up, write atomically."""

from __future__ import annotations

import copy
import glob
import os
import shutil
import tempfile
from datetime import datetime

import yaml

from .config import Config, ConfigError, parse_config

KEEP_BACKUPS = 10
HEADER = "# Written by PowerEngine's config page. Edit there rather than by hand.\n"


def save_config(path: str, data: dict, now: datetime | None = None) -> tuple[Config, str | None]:
    """Validate `data`, back up any existing file, then write it atomically.

    Returns (parsed config, backup path or None). Raises ConfigError if invalid,
    in which case nothing on disk changes.
    """
    cfg = parse_config(data)                       # raises before touching disk
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)

    backup = None
    if os.path.exists(path):
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        backup = f"{path}.bak-{stamp}"
        shutil.copy2(path, backup)
        for old in sorted(glob.glob(f"{path}.bak-*"))[:-KEEP_BACKUPS]:
            os.remove(old)

    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".config-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(HEADER)
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return cfg, backup


_TRUE, _FALSE = {"true", "on", "yes", "1"}, {"false", "off", "no", "0"}


def _flag(value):
    """A true/false written as text or 0/1 by something between the form and the app becomes a real boolean; anything
    else is returned as it came, so validation still refuses it."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return value


def coerce_flags(data):
    """A copy of a config from the card with its true/false settings as booleans (features, the on/off system
    settings, notification events, plant `enabled`, an input's `invert` / `use_measured`, `remove_entities`).
    Choice settings (which hold text like "garage") and numbers are left alone."""
    from .config import SYSTEM_CHOICES
    if not isinstance(data, dict):
        return data
    out = copy.deepcopy(data)
    for section in ("features",):
        if isinstance(out.get(section), dict):
            out[section] = {k: _flag(v) for k, v in out[section].items()}
    if isinstance(out.get("system"), dict):
        out["system"] = {k: (v if k in SYSTEM_CHOICES else _flag(v)) for k, v in out["system"].items()}
    events = (out.get("notifications") or {}).get("events") if isinstance(out.get("notifications"), dict) else None
    if isinstance(events, dict):
        out["notifications"]["events"] = {k: _flag(v) for k, v in events.items()}
    for plant in out.get("solar_plants") or []:
        if isinstance(plant, dict) and "enabled" in plant:
            plant["enabled"] = _flag(plant["enabled"])
    for spec in (out.get("inputs") or {}).values() if isinstance(out.get("inputs"), dict) else []:
        if isinstance(spec, dict):
            for key in ("invert", "use_measured"):
                if key in spec:
                    spec[key] = _flag(spec[key])
    if "remove_entities" in out:
        out["remove_entities"] = _flag(out["remove_entities"])
    return out


def with_operation(raw: dict | None, mode: str) -> dict:
    """A copy of the saved config with operation.mode set (the Predbat/PowerEngine switch). Raises ConfigError."""
    if mode not in ("active", "passive"):
        raise ConfigError(f"operation must be 'active' or 'passive', not {mode!r}")
    new = copy.deepcopy(raw or {})
    op = new.get("operation")
    new["operation"] = dict(op) if isinstance(op, dict) else {}
    new["operation"]["mode"] = mode
    return new
