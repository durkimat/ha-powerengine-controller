"""Checks for mapped inputs: does the entity exist, fit the role, and look alive?

Pure functions over plain dicts, so they run identically in tests and in HA.
A `state` is what HA returns for an entity: {"state", "attributes", "last_updated", ...}.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .roles import UNITS, Role, is_forbidden_control

OK, UNMAPPED, MISSING, UNAVAILABLE, WRONG_UNIT, WRONG_DOMAIN, STALE, FORBIDDEN, BAD_STATIC, NO_ATTRIBUTE = (
    "ok", "unmapped", "missing", "unavailable", "wrong_unit", "wrong_domain", "stale", "forbidden",
    "bad_static", "no_attribute",
)

# Live readings that should change regularly; everything else isn't age-checked.
STALE_AFTER = {"power": timedelta(minutes=30), "percent": timedelta(hours=6)}


def _age(state: dict[str, Any], now: datetime) -> timedelta | None:
    stamp = state.get("last_reported") or state.get("last_updated")
    if not stamp:
        return None
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return now - t


def check(role: Role, spec: dict[str, Any] | None, state: dict[str, Any] | None,
          now: datetime | None = None) -> tuple[str, str]:
    """Return (status, message) for one role."""
    now = now or datetime.now(timezone.utc)
    if not spec:
        return UNMAPPED, "Not set"
    if "value" in spec:
        if not role.static_ok:
            return BAD_STATIC, "This input must be an entity"
        try:
            float(spec["value"])
        except (TypeError, ValueError):
            return BAD_STATIC, "Static value must be a number"
        return OK, f"Fixed value {spec['value']} {role.static_unit}".strip()

    entity = str(spec.get("entity", ""))
    domain = entity.split(".", 1)[0]
    if role.kind == "control" and is_forbidden_control(entity):
        return FORBIDDEN, "PowerEngine never writes to bump/boost entities"
    if domain not in role.domains:
        return WRONG_DOMAIN, f"Expected a {' or '.join(role.domains)} entity"
    if state is None:
        return MISSING, "Entity not found"
    if str(state.get("state")) in ("unavailable", "unknown") and role.kind not in ("timestamp", "control"):
        # timestamps are legitimately 'unknown' when no event is scheduled
        return UNAVAILABLE, f"Entity is {state.get('state')}"
    attrs = state.get("attributes") or {}
    attr = spec.get("attribute") or role.attribute
    if attr and attr not in attrs:
        return NO_ATTRIBUTE, f"Entity has no '{attr}' attribute"
    allowed = UNITS.get(role.kind)
    if allowed and not attr:
        unit = attrs.get("unit_of_measurement")
        if unit not in allowed:
            return WRONG_UNIT, f"Unit is {unit or 'none'}; expected {' or '.join(allowed)}"
    limit = STALE_AFTER.get(role.kind)
    age = _age(state, now)
    if limit and age is not None and age > limit:
        return STALE, f"No update for {int(age.total_seconds() // 60)} min"
    return OK, "OK"


def summarise(results: dict[str, tuple[str, str]], required: list[str]) -> str:
    """Overall mapping state: 'ok', 'incomplete' (required inputs missing/bad) or 'warnings'."""
    if any(results.get(k, (UNMAPPED, ""))[0] != OK for k in required):
        return "incomplete"
    if any(status not in (OK, UNMAPPED) for status, _ in results.values()):
        return "warnings"
    return "ok"
