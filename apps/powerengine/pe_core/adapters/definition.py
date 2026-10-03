"""Inverter definition files: what makes one inverter different from another, as data.

A definition is a YAML file (see `devices/solis.yml` and docs/INVERTERS.md). It says which entities an inverter's
roles are, how its timed windows and remote-control registers are laid out, what its options are called, and what it
can do. The algorithms (timed-window arithmetic, the remote-control command for a decision, clock-drift maths) are
Python; a definition picks them by name (`behaviour:`), it never contains code.

This module only loads, validates and merges (firmware variants). It imports nothing from the rest of pe_core, so
`roles.py` can use it for the entity suggestions without a cycle.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

DEVICES_DIR = Path(__file__).parent / "devices"
SUFFIX = ".yml"                # not .yaml: AppDaemon loads every .yaml under the apps folder as app config
FORMAT = 1

# The named behaviours a definition may select (each is implemented in Python by DefinedInverter and its helpers).
BEHAVIOURS = {"ram": ("override_select",), "timed_slots": ("timed_hhmm",), "clock": ("drift_button",)}
ACTIONS = ("grid_charge", "hold", "force_discharge", "export", "self_use", "none")
RC_OPTIONS = ("Off", "Force charge", "Force discharge")       # the RC controller's own words (ramcontrol.py)
RC_ROLES = ("rc_mode", "rc_charge_power", "rc_discharge_power")
RC_TESTS = ("rc_charge", "rc_discharge", "rc_hold", "rc_failsafe")
STATUSES = ("verified", "community", "draft")               # how far a definition has been proven on real hardware
TIMED_SLOT_COUNT = 3                                          # what the timed_hhmm behaviour is written for


class DefinitionError(ValueError):
    """A definition file is missing something or says something the driver can't use."""


@dataclass(frozen=True)
class Definition:
    data: dict            # the merged definition (base plus the matching firmware variant)
    source: str           # the file it came from, for messages
    firmware: str | None  # the firmware asked for (None: the definition's default)
    variant: str | None   # the `match` of the variant applied, if any

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def name(self) -> str:
        return self.data["name"]


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _matches(match: str, firmware: str) -> bool:
    if match.startswith("re:"):
        return re.search(match[3:], firmware) is not None
    return match == firmware


def apply_firmware(data: dict, firmware: str | None, source: str = "<definition>") -> tuple[dict, str | None]:
    """(the definition with the matching firmware variant merged in, that variant's `match` or None)."""
    section = data.get("firmware") or {}
    version = firmware if firmware is not None else section.get("default")
    if version is None:
        return data, None
    for variant in section.get("variants") or []:
        if _matches(str(variant["match"]), str(version)):
            over = {k: v for k, v in (variant.get("override") or {}).items() if k != "firmware"}
            return _deep_merge(data, over), str(variant["match"])
    return data, None


# --- validation ----------------------------------------------------------------------------------------------

def _need(d, path: str, kind, source: str):
    """d[path] (a dotted path) which must exist and be a `kind`."""
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise DefinitionError(f"{source}: missing '{path}'")
        cur = cur[part]
    if not isinstance(cur, kind):
        want = kind.__name__ if isinstance(kind, type) else " or ".join(k.__name__ for k in kind)
        raise DefinitionError(f"{source}: '{path}' must be a {want}, not {type(cur).__name__}")
    return cur


def _strings(d, path: str, source: str) -> list[str]:
    items = _need(d, path, list, source)
    if not all(isinstance(x, str) and x for x in items):
        raise DefinitionError(f"{source}: '{path}' must be a list of names")
    return items


def validate(data, source: str = "<definition>") -> None:
    """Raise DefinitionError, naming the key, if `data` isn't a usable inverter definition."""
    if not isinstance(data, dict):
        raise DefinitionError(f"{source}: the file must be a mapping of keys")
    if data.get("definition") != FORMAT:
        raise DefinitionError(f"{source}: 'definition' must be {FORMAT} (the file format version)")
    _need(data, "name", str, source)
    _need(data, "display_names", dict, source)
    _need(data, "card_model", str, source)
    caps = _need(data, "capabilities", dict, source)
    for flag in ("supports_ram", "supports_timed_slots"):
        _need(caps, flag, bool, f"{source}: capabilities")
    for key in ("max_charge_w", "max_discharge_w"):
        _need(data, f"capabilities.{key}", (int, float), source)
    acts = _strings(data, "capabilities.actions", source)
    bad = [a for a in acts if a not in ACTIONS]
    if bad:
        raise DefinitionError(f"{source}: capabilities.actions has unknown action(s) {bad}; known: {list(ACTIONS)}")
    _strings(data, "capabilities.never_touch", source)
    if not (caps["supports_ram"] or caps["supports_timed_slots"]):
        raise DefinitionError(f"{source}: the inverter must support RAM remote control or timed slots")
    if caps["supports_ram"]:
        _validate_ram(data, source)
    if caps["supports_timed_slots"]:
        _validate_timed(data, source)
    if "clock" in data:
        _validate_clock(data, source)
    _validate_roles(data, source)
    _validate_firmware(data, source)
    _validate_status(data, source)
    _validate_detect(data, source)


def _behaviour(data, section: str, source: str) -> None:
    b = _need(data, f"{section}.behaviour", str, source)
    if b not in BEHAVIOURS[section]:
        raise DefinitionError(f"{source}: {section}.behaviour '{b}' is not one this app has; "
                              f"known: {list(BEHAVIOURS[section])}")


def _validate_ram(data, source: str) -> None:
    _behaviour(data, "ram", source)
    for key in ("failsafe_min", "max_power_w", "lookup_minutes"):
        _need(data, f"ram.{key}", (int, float), source)
    _need(data, "ram.prefer", str, source)
    for role in RC_ROLES:
        _need(data, f"ram.entities.{role}.domain", str, source)
        _need(data, f"ram.entities.{role}.tail", str, source)
    for direction in ("charge", "discharge"):
        role = _need(data, f"ram.power_roles.{direction}", str, source)
        if role not in RC_ROLES[1:]:
            raise DefinitionError(f"{source}: ram.power_roles.{direction} must be one of {list(RC_ROLES[1:])}")
    for opt in RC_OPTIONS:
        _need(data, f"ram.options.{opt}", str, source)
    tests = _need(data, "ram.tests", dict, source)
    for name, option in tests.items():
        if name not in RC_TESTS:
            raise DefinitionError(f"{source}: ram.tests has unknown test '{name}'; known: {list(RC_TESTS)}")
        if option not in RC_OPTIONS:
            raise DefinitionError(f"{source}: ram.tests.{name} must be one of {list(RC_OPTIONS)}")
    missing = [t for t in RC_TESTS if t not in tests]
    if missing:
        raise DefinitionError(f"{source}: ram.tests is missing {missing}")


def _validate_timed(data, source: str) -> None:
    _behaviour(data, "timed_slots", source)
    n = _need(data, "timed_slots.count", int, source)
    if n != TIMED_SLOT_COUNT:
        raise DefinitionError(f"{source}: timed_slots.count is {n}, but timed_hhmm handles exactly "
                              f"{TIMED_SLOT_COUNT} slots")
    suffix = _need(data, "timed_slots.suffix", str, source)
    if "{n}" not in suffix:
        raise DefinitionError(f"{source}: timed_slots.suffix must contain {{n}} (the slot number)")
    first = _strings(data, "timed_slots.first_slot_roles", source)
    _strings(data, "timed_slots.currents", source)
    _strings(data, "timed_slots.test_roles", source)
    _strings(data, "timed_slots.staged_parts", source)
    for key in ("self_use_option", "write_only_match"):
        _need(data, f"timed_slots.{key}", str, source)
    _need(data, "timed_slots.recheck_seconds", (int, float), source)
    if not first:
        raise DefinitionError(f"{source}: timed_slots.first_slot_roles is empty")


def _validate_clock(data, source: str) -> None:
    _behaviour(data, "clock", source)
    _need(data, "clock.clock_role", str, source)
    _need(data, "clock.sync_role", str, source)


def _validate_roles(data, source: str) -> None:
    roles = data.get("roles", {})
    if not isinstance(roles, dict):
        raise DefinitionError(f"{source}: 'roles' must be a mapping of role to suggestions")
    for role, spec in roles.items():
        if not isinstance(spec, dict):
            raise DefinitionError(f"{source}: roles.{role} must be a mapping")
        for key in ("suggest", "suggest_not"):
            for pattern in spec.get(key, []):
                try:
                    re.compile(pattern)
                except (re.error, TypeError) as err:
                    raise DefinitionError(f"{source}: roles.{role}.{key} has a bad pattern {pattern!r}: {err}") \
                        from None
        unknown = set(spec) - {"suggest", "suggest_not"}
        if unknown:
            raise DefinitionError(f"{source}: roles.{role} has unknown key(s) {sorted(unknown)}")


def _validate_status(data, source: str) -> None:
    """`status` (optional, default draft) and `verified_firmware`, the versions a person has proven it on; and
    `firmware_entity`, where the inverter reports its own firmware (optional: {domain, tail}, found by name)."""
    status = data.get("status", "draft")
    if status not in STATUSES:
        raise DefinitionError(f"{source}: 'status' must be one of {', '.join(STATUSES)} (not {status!r})")
    if "verified_firmware" in data:
        _strings(data, "verified_firmware", source)
    ent = data.get("firmware_entity")
    if ent is not None and not (isinstance(ent, dict) and isinstance(ent.get("domain"), str)
                                and isinstance(ent.get("tail"), str)):
        raise DefinitionError(f"{source}: 'firmware_entity' needs a 'domain' and a 'tail' (text)")


def _validate_detect(data, source: str) -> None:
    """`detect` (optional): how the setup wizard recognises this inverter in Home Assistant. `integration` is
    {name, url?}; `domains`, `manufacturers`, `models` and `entities` are lists of text (the last three are regular
    expressions, checked here so a typo is a clear error and not a silent miss in the card)."""
    detect = data.get("detect")
    if detect is None:
        return
    if not isinstance(detect, dict):
        raise DefinitionError(f"{source}: 'detect' must be a mapping")
    unknown = sorted(set(detect) - {"integration", "domains", "manufacturers", "models", "entities"})
    if unknown:
        raise DefinitionError(f"{source}: 'detect' has unknown key(s) {unknown}")
    integ = detect.get("integration")
    if integ is not None and not (isinstance(integ, dict) and isinstance(integ.get("name"), str)
                                  and isinstance(integ.get("url", ""), str)):
        raise DefinitionError(f"{source}: 'detect.integration' needs a 'name' (and may have a 'url'), as text")
    for key in ("domains", "manufacturers", "models", "entities"):
        items = detect.get(key, [])
        if not isinstance(items, list) or not all(isinstance(x, str) and x for x in items):
            raise DefinitionError(f"{source}: 'detect.{key}' must be a list of text")
        if key != "domains":
            for pattern in items:
                try:
                    re.compile(pattern)
                except re.error as err:
                    raise DefinitionError(f"{source}: 'detect.{key}' has a bad pattern {pattern!r}: {err}") from None


def _validate_firmware(data, source: str) -> None:
    fw = data.get("firmware")
    if fw is None:
        return
    if not isinstance(fw, dict):
        raise DefinitionError(f"{source}: 'firmware' must be a mapping")
    for i, variant in enumerate(fw.get("variants") or []):
        if not isinstance(variant, dict) or not isinstance(variant.get("match"), str):
            raise DefinitionError(f"{source}: firmware.variants[{i}] needs a 'match' (a version or 're:pattern')")
        if not isinstance(variant.get("override", {}), dict):
            raise DefinitionError(f"{source}: firmware.variants[{i}].override must be a mapping")
        if variant["match"].startswith("re:"):
            try:
                re.compile(variant["match"][3:])
            except re.error as err:
                raise DefinitionError(f"{source}: firmware.variants[{i}].match: {err}") from None


# --- loading -------------------------------------------------------------------------------------------------

def parse_definition(data: dict, firmware: str | None = None, source: str = "<definition>") -> Definition:
    """A validated Definition from already-parsed data, with the firmware variant applied (and checked again)."""
    validate(data, source)
    merged, variant = apply_firmware(data, firmware, source)
    if variant is not None:
        validate(merged, f"{source} (firmware {firmware if firmware is not None else data['firmware']['default']}, "
                         f"variant {variant})")
    return Definition(copy.deepcopy(merged), source, firmware, variant)


@cache
def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as err:
        raise DefinitionError(f"{path}: can't read it: {err}") from None
    except yaml.YAMLError as err:
        raise DefinitionError(f"{path}: not valid YAML: {err}") from None
    return data


def definition_path(name: str) -> Path:
    return DEVICES_DIR / f"{name}{SUFFIX}"


def load_definition(name_or_path: str | Path, firmware: str | None = None) -> Definition:
    """The definition called `name` (a file in devices/), or at `path`, for `firmware` (None: its default)."""
    path = Path(name_or_path)
    if not path.suffix:
        path = definition_path(str(name_or_path))
    return parse_definition(copy.deepcopy(_read(str(path))), firmware, path.name)


def available() -> list[str]:
    """The inverter definitions shipped in devices/."""
    return sorted(p.name[: -len(SUFFIX)] for p in DEVICES_DIR.glob(f"*{SUFFIX}"))


def detect_info(name: str) -> dict | None:
    """The `detect:` block of the inverter's definition (what the setup wizard looks for), or None if it has none."""
    return load_definition(name).get("detect")


def role_suggestions(name: str, firmware: str | None = None) -> dict[str, dict[str, tuple[str, ...]]]:
    """{role: {"suggest": (...), "suggest_not": (...)}} from the inverter's definition."""
    out = {}
    for role, spec in load_definition(name, firmware).get("roles", {}).items():
        out[role] = {"suggest": tuple(spec.get("suggest", ())), "suggest_not": tuple(spec.get("suggest_not", ()))}
    return out


__all__ = ["Definition", "DefinitionError", "load_definition", "parse_definition", "apply_firmware", "validate",
           "available", "role_suggestions", "detect_info", "definition_path", "DEVICES_DIR"]
