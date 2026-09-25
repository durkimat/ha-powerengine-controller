import json
import re

import pytest

from pe_core.roles import GROUPS, KINDS, ROLE_BY_KEY, ROLES, catalogue, is_forbidden_control


def test_keys_unique_and_snake_case():
    keys = [r.key for r in ROLES]
    assert len(keys) == len(set(keys))
    assert all(re.match(r"^[a-z][a-z0-9_]+$", k) for k in keys)


def test_every_role_is_documented():
    group_keys = {g for g, _ in GROUPS}
    for r in ROLES:
        assert r.group in group_keys, r.key
        assert r.kind in KINDS, r.key
        assert r.label and r.description.endswith("."), r.key
        assert len(r.description) <= 110, f"{r.key}: keep descriptions to one line"
        if r.signed:
            assert "+" in r.sign_note and "-" in r.sign_note, r.key
        if r.kind == "static":
            assert r.static_ok and r.static_unit, r.key


def test_suggestion_patterns_compile():
    for r in ROLES:
        for p in r.suggest + r.suggest_not:
            re.compile(p)


def test_catalogue_fits_in_ha_attribute_limit():
    # HA won't record attributes over 16 KB; keep well under it (roles + settings, as published)
    from pe_core.config import settings_catalogue
    # HA's recorder stores attributes as compact JSON and skips any over 16 KiB
    for part in (catalogue(), settings_catalogue()):            # published on two sensors
        size = len(json.dumps(part, separators=(",", ":"), ensure_ascii=False))
        assert size < 15500, size


@pytest.mark.parametrize("eid,forbidden", [
    ("switch.edf_energy_x_intelligent_bump_charge", True),
    ("switch.zappi_boost", True),
    ("select.solis_inverter_battery_control_override", False),
])
def test_forbidden_controls(eid, forbidden):
    assert is_forbidden_control(eid) is forbidden


def test_battery_limits_default_to_agreed_values():
    assert ROLE_BY_KEY["battery_max_charge_power"].suggest_static == 4800
    assert ROLE_BY_KEY["battery_max_discharge_power"].suggest_static == 4800
