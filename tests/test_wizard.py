"""The setup wizard's data (pe_core/wizard.py), the detection tables, and required inputs following the site."""

import copy
import json
import re

import pytest

from pe_core import wizard
from pe_core.adapters import detect, registry
from pe_core.adapters.definition import DefinitionError, load_definition, parse_definition
from pe_core.adapters.options import site_options
from pe_core.config import SITE_KINDS, left_out_roles, parse_config, required_roles
from pe_core.roles import ROLES


def _site(**kw):
    return parse_config({"site": kw}) if kw else parse_config({})


def test_every_required_part_is_inverter_and_tariff_and_the_rest_can_be_skipped():
    info = wizard.wizard_info()
    assert [p["part"] for p in info["parts"]] == ["inverter", "tariff", "ev_charger", "forecast", "events"]
    required = [p["part"] for p in info["parts"] if p["required"]]
    assert required == ["inverter", "tariff"]
    for p in info["parts"]:
        assert ("skip" in p) == (not p["required"])
        if not p["required"]:
            assert p["skip"] == "none"


def test_every_part_lists_the_adapters_the_registry_has_and_how_to_find_them():
    info = wizard.wizard_info()
    for p in info["parts"]:
        names = [o["id"] for o in p["options"]]
        extra = {"none", "auto"}
        assert names == [n for n in registry.names(SITE_KINDS[p["part"]]) if n not in extra]
        for o in p["options"]:
            assert o.get("integration", {}).get("name"), f"{p['part']}/{o['id']} has no integration name to show"
            assert any(o.get(k) for k in detect.LISTS), f"{p['part']}/{o['id']} has nothing to search for"
            for key in ("manufacturers", "models", "entities"):
                for pattern in o.get(key, []):
                    re.compile(pattern)


def test_each_role_is_in_one_part_and_guards_are_in_none():
    info = wizard.wizard_info()
    seen = {}
    for p in info["parts"]:
        for key in p["roles"]:
            assert key not in seen, f"{key} is in both {seen[key]} and {p['part']}"
            seen[key] = p["part"]
    for role in ROLES:
        if role.group == "handover":
            assert role.key not in seen
        else:
            assert role.key in seen, f"{role.key} belongs to no part"
    assert seen["smart_target_soc"] == "tariff" and seen["grid_power_reference"] == "ev_charger"
    assert seen["solar_forecast_today"] == "forecast" and seen["axle_event_start"] == "events"


def test_the_published_wizard_is_small():
    assert len(json.dumps(wizard.wizard_info(), separators=(",", ":"))) < 5000
    # with everything else on the version sensor (names, site, site_options) it stays far from HA's 16 KB limit
    rest = {"site": _site().site.as_dict(), "site_options": site_options(), "wizard": wizard.wizard_info()}
    assert len(json.dumps(rest, separators=(",", ":"))) < 9000


def test_solis_detect_block_is_loaded_and_valid():
    detect_block = load_definition("solis")["detect"]
    assert detect_block["domains"] == ["solax_modbus"]
    assert re.search(detect_block["entities"][0], "sensor.solis_battery_soc")
    assert re.search(detect_block["manufacturers"][0], "Solis", re.I)


@pytest.mark.parametrize("bad, fragment", [
    ({"nope": 1}, "unknown key"),
    ({"domains": "solax_modbus"}, "detect.domains"),
    ({"entities": ["^sensor\\.(solis"]}, "bad pattern"),
    ({"integration": {"url": "x"}}, "detect.integration"),
    ("text", "must be a mapping"),
])
def test_a_bad_detect_block_is_refused_in_words(bad, fragment):
    data = copy.deepcopy(load_definition("solis").data)
    data["detect"] = bad
    with pytest.raises(DefinitionError, match=fragment):
        parse_definition(data)


def test_a_definition_with_no_detect_block_is_still_valid_and_has_no_search_data():
    data = copy.deepcopy(load_definition("solis").data)
    del data["detect"]
    assert parse_definition(data).get("detect") is None


# --- required inputs follow the site ---------------------------------------------------------------------------

def test_leaving_a_part_out_drops_its_roles_from_the_required_list():
    full = set(required_roles(parse_config({})))
    assert {"ev_plug_status", "smart_dispatches", "solar_forecast_today", "axle_event_active"} <= full
    no_car = set(required_roles(_site(ev_charger="none")))
    assert not {"ev_plug_status", "ev_charge_power", "smart_dispatches"} & no_car
    assert {"solar_forecast_today", "axle_event_active", "battery_soc", "import_rate_now"} <= no_car
    no_forecast = set(required_roles(_site(forecast="none")))
    assert not {"solar_forecast_today", "solar_forecast_tomorrow"} & no_forecast
    no_events = set(required_roles(_site(events="none")))
    assert "axle_event_active" not in no_events and "ev_plug_status" in no_events


def test_the_inverter_and_tariff_roles_are_always_required():
    bare = set(required_roles(_site(ev_charger="none", forecast="none", events="none")))
    assert {"battery_soc", "grid_power", "house_load_power", "import_rate_now", "import_rates_today"} <= bare
    assert left_out_roles(_site(ev_charger="none").site) == {r.key for r in ROLES if r.group in ("ev", "smart")}


def test_the_default_site_leaves_nothing_out():
    assert left_out_roles(parse_config({}).site) == set()
