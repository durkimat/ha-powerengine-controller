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


def with_operation(raw: dict | None, mode: str) -> dict:
    """A copy of the saved config with operation.mode set (the Predbat/PowerEngine switch). Raises ConfigError."""
    if mode not in ("active", "passive"):
        raise ConfigError(f"operation must be 'active' or 'passive', not {mode!r}")
    new = copy.deepcopy(raw or {})
    op = new.get("operation")
    new["operation"] = dict(op) if isinstance(op, dict) else {}
    new["operation"]["mode"] = mode
    return new
