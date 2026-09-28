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

# Live readings that should change regularly; everything else isn't age-checked. A power reading of exactly 0
# is never stale (see check()).
STALE_AFTER = {"power": timedelta(minutes=30), "percent": timedelta(hours=6)}
# Settings rather than readings: they can go months or years unchanged (and many integrations only write on
# change), so they're never age-checked; only missing or unavailable is flagged.
SLOW_ROLES = frozenset({"battery_soh", "inverter_min_soc"})


def _is_zero(value: Any) -> bool:
    try:
        return float(value) == 0
    except (TypeError, ValueError):
        return False


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
    value = str(state.get("state"))
    idle_unknown = value == "unknown" and (role.unknown_ok or role.kind in ("timestamp", "control"))
    if value in ("unavailable", "unknown") and not idle_unknown:
        # timestamps and some event sensors are legitimately 'unknown' when no event is scheduled
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
    limit = None if role.key in SLOW_ROLES else STALE_AFTER.get(role.kind)
    if limit and role.kind == "power" and _is_zero(state.get("state")):
        # An idle charger or solar at night sits at 0 W, and many integrations only write on change.
        limit = None
    age = _age(state, now)
    if limit and age is not None and age > limit:
        return STALE, f"No update for {int(age.total_seconds() // 60)} min"
    return OK, "OK"


# Required inputs whose brief outage doesn't stop control: the car charger's readings come from a cloud service
# (myenergi) that blips; control carries on with the car assumed not charging. Unmapped still counts as missing.
DEGRADABLE_GROUPS = frozenset({"ev"})
DEGRADED = (UNAVAILABLE, STALE)


def degradable(key: str) -> bool:
    from .roles import ROLE_BY_KEY
    role = ROLE_BY_KEY.get(key)
    return role is not None and role.group in DEGRADABLE_GROUPS


def blocking(results: dict[str, tuple[str, str]], required: list[str]) -> list[str]:
    """Required inputs that stop control now (a degradable one only when unmapped, missing or wrong)."""
    out = []
    for k in required:
        status = results.get(k, (UNMAPPED, ""))[0]
        if status != OK and not (status in DEGRADED and degradable(k)):
            out.append(k)
    return out


def degraded(results: dict[str, tuple[str, str]], required: list[str]) -> list[str]:
    return [k for k in required if degradable(k) and results.get(k, (UNMAPPED, ""))[0] in DEGRADED]


def summarise(results: dict[str, tuple[str, str]], required: list[str]) -> str:
    """Overall mapping state: 'ok', 'incomplete' (required inputs missing/bad) or 'warnings'."""
    if blocking(results, required):
        return "incomplete"
    if any(status not in (OK, UNMAPPED) for status, _ in results.values()):
        return "warnings"
    return "ok"
