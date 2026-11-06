"""What PowerEngine knows about other Home Assistant integrations' entities, as data (`integrations/*.yaml`).

Each file describes one integration PowerEngine can read from (a tariff, a car charger, a solar forecast, grid events):
how the wizard recognises it (`detect`) and which entities to suggest for which PowerEngine input (`roles`). The code
names no integration and no entity: add a file and the Config page and the wizard know a new one. An inverter's own
file is `devices/<name>.yaml` (definition.py).

    part: tariff                  # the site key this answers to: tariff | ev_charger | forecast | events
    name: edf
    detect:
      integration: {name: ..., url: ...}      # url only where it is known
      domains: [...]                          # Home Assistant integration domains
      manufacturers: [...]  models: [...]     # regular expressions, case-insensitive
      entities: [...]                         # entity-id regular expressions
    roles:
      <role key>: {suggest: [...], suggest_not: [...]}

Nothing here is read by the control code: it never changes what PowerEngine does, only what is suggested.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

FOLDER = Path(__file__).parent / "integrations"
PARTS = ("tariff", "ev_charger", "forecast", "events")
LISTS = ("domains", "manufacturers", "models", "entities")
KEYS = ("integration", *LISTS)


class IntegrationDataError(ValueError):
    """An integration file that cannot be used (the message names the file and the problem)."""


def _strings(value, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise IntegrationDataError(f"{where} must be a list of strings")
    return value


def _patterns(value, where: str) -> list[str]:
    for pattern in _strings(value, where):
        try:
            re.compile(pattern)
        except re.error as err:
            raise IntegrationDataError(f"{where}: '{pattern}' is not a regular expression ({err})") from err
    return value


def parse(data, source: str) -> dict:
    """Check one file's content and return it (raises IntegrationDataError naming `source`)."""
    if not isinstance(data, dict):
        raise IntegrationDataError(f"{source}: must be a mapping")
    extra = set(data) - {"part", "name", "detect", "roles"}
    if extra:
        raise IntegrationDataError(f"{source}: unknown key(s) {', '.join(sorted(extra))}")
    if data.get("part") not in PARTS:
        raise IntegrationDataError(f"{source}: part must be one of {', '.join(PARTS)}")
    if not isinstance(data.get("name"), str) or not data["name"]:
        raise IntegrationDataError(f"{source}: name is required")
    detect = data.get("detect")
    if not isinstance(detect, dict) or set(detect) != set(KEYS):
        raise IntegrationDataError(f"{source}: detect needs exactly {', '.join(KEYS)}")
    integration = detect["integration"]
    if not isinstance(integration, dict) or not isinstance(integration.get("name"), str) \
            or set(integration) - {"name", "url"}:
        raise IntegrationDataError(f"{source}: detect.integration is {{name, url?}}")
    _strings(detect["domains"], f"{source}: detect.domains")
    for key in ("manufacturers", "models", "entities"):
        _patterns(detect[key], f"{source}: detect.{key}")
    roles = data.get("roles")
    if not isinstance(roles, dict):
        raise IntegrationDataError(f"{source}: roles must be a mapping (an empty one is {{}})")
    for role, spec in roles.items():
        if not isinstance(spec, dict) or set(spec) - {"suggest", "suggest_not"} or "suggest" not in spec:
            raise IntegrationDataError(f"{source}: roles.{role} is {{suggest: [...], suggest_not: [...]?}}")
        for key in ("suggest", "suggest_not"):
            if key in spec:
                _patterns(spec[key], f"{source}: roles.{role}.{key}")
    return data


@lru_cache(maxsize=1)
def load() -> tuple[dict, ...]:
    """Every integration file, checked, in file-name order."""
    out = []
    for path in sorted(FOLDER.glob("*.yaml")):
        out.append(parse(yaml.safe_load(path.read_text(encoding="utf-8")), path.name))
    return tuple(out)


def detect_table() -> dict[str, dict[str, dict]]:
    """{part: {name: {"integration", "domains", "manufacturers", "models", "entities"}}}, for the wizard."""
    table: dict[str, dict[str, dict]] = {}
    for data in load():
        table.setdefault(data["part"], {})[data["name"]] = {k: data["detect"][k] for k in KEYS}
    return table


def role_suggestions() -> dict[str, dict[str, tuple[str, ...]]]:
    """{role: {"suggest": (...), "suggest_not": (...)}}, the files' suggestions for a role joined in file-name order."""
    out: dict[str, dict[str, tuple[str, ...]]] = {}
    for data in load():
        for role, spec in data["roles"].items():
            have = out.setdefault(role, {"suggest": (), "suggest_not": ()})
            have["suggest"] += tuple(spec.get("suggest", ()))
            have["suggest_not"] += tuple(spec.get("suggest_not", ()))
    return out
