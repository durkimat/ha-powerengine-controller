"""Commands from Home Assistant in direct publishing mode (demo plan B2), as pure functions.

With MQTT, HA's switches and selects publish to retained command topics and the app reacts to the entity's state
changing. Direct mode has no integration behind the entities, so the app hears HA's `call_service` event instead (and
the card's own `pe_command` event) and sets the entity's state itself. Either way the app's existing state
listeners then run. Everything here only ever names PowerEngine's own entities; anything else is ignored.

`call_service` event data (HA's bus): {"domain": "switch", "service": "turn_on",
"service_data": {"entity_id": "switch.pe_ctl_pause" | [..] | "a,b", ...}}.
`pe_command` event data: {"entity_id": "switch.pe_ctl_pause", "value": "ON"} (a select's option, a number's value,
"ON" / "OFF" / "toggle" for a switch).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .entities import ENTITIES, EntityDef

BY_ENTITY_ID = {e.entity_id: e for e in ENTITIES}
SWITCH_SERVICES = {"turn_on": "ON", "turn_off": "OFF", "toggle": "toggle"}
SELECT_STEPS = {"select_next": 1, "select_previous": -1}


def entity_ids(value: Any) -> list[str]:
    """A service call's entity_id in any shape HA sends it: a string, a comma-separated string, or a list."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, Iterable) or isinstance(value, dict):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def ours(value: Any, known: dict[str, EntityDef] = BY_ENTITY_ID) -> list[EntityDef]:
    return [known[e] for e in entity_ids(value) if e in known]


def resolve(ent: EntityDef, wanted: Any, current: str | None) -> str | None:
    """The value to set on `ent`, or None if `wanted` isn't acceptable (a select's option must be one of its options,
    a number is clamped to its min/max, a switch takes ON/OFF/toggle)."""
    if ent.component == "switch":
        text = str(wanted).strip()
        if text.lower() == "toggle":
            return "OFF" if str(current).lower() == "on" else "ON"
        return {"on": "ON", "true": "ON", "off": "OFF", "false": "OFF"}.get(text.lower())
    if ent.component == "select":
        options = ent.options.get("options") or []
        return str(wanted) if str(wanted) in options else None
    if ent.component == "number":
        try:
            num = float(wanted)
        except (TypeError, ValueError):
            return None
        lo, hi = ent.options.get("min"), ent.options.get("max")
        num = min(max(num, lo if lo is not None else num), hi if hi is not None else num)
        return f"{num:g}"
    return None


def from_service(domain: str, service: str, data: dict, get_state: Callable[[str], Any],
                 known: dict[str, EntityDef] = BY_ENTITY_ID) -> list[tuple[EntityDef, str]]:
    """(entity, value) pairs for a `call_service` event, for PowerEngine's entities only."""
    data = data or {}
    out: list[tuple[EntityDef, str]] = []
    for ent in ours(data.get("entity_id"), known):
        if ent.component != domain:
            continue
        current = get_state(ent.entity_id)
        if domain == "switch" and service in SWITCH_SERVICES:
            wanted: Any = SWITCH_SERVICES[service]
        elif domain == "select" and service == "select_option":
            wanted = data.get("option")
        elif domain == "select" and service in SELECT_STEPS:
            options = ent.options.get("options") or []
            if not options:
                continue
            i = options.index(current) if current in options else -1
            j = i + SELECT_STEPS[service]
            if data.get("cycle", True) is False and not 0 <= j < len(options):
                continue
            wanted = options[j % len(options)]
        elif domain == "number" and service == "set_value":
            wanted = data.get("value")
        else:
            continue
        value = resolve(ent, wanted, current)
        if value is not None:
            out.append((ent, value))
    return out


def from_pe_command(data: dict, get_state: Callable[[str], Any],
                    known: dict[str, EntityDef] = BY_ENTITY_ID) -> list[tuple[EntityDef, str]]:
    data = data or {}
    out = []
    for ent in ours(data.get("entity_id"), known):
        value = resolve(ent, data.get("value"), get_state(ent.entity_id))
        if value is not None:
            out.append((ent, value))
    return out
