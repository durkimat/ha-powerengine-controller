"""Switches run while there is free power: the config item and the on/off rules."""
import pytest

from pe_core.config import ConfigError, parse_config
from pe_core.freeloads import FreeLoadState


def cfg(loads):
    return parse_config({"free_power_loads": loads})


def test_default_is_none():
    assert parse_config({}).free_power_loads == ()


def test_the_first_item_parses_in_both_forms():
    a = cfg([{"entity": "switch.immersion_heater", "name": "Immersion"}]).free_power_loads
    b = cfg(["switch.immersion_heater"]).free_power_loads
    assert a[0].entity == b[0].entity == "switch.immersion_heater" and a[0].name == "Immersion" and b[0].name == ""


@pytest.mark.parametrize("bad", [
    "switch.x", [{"name": "x"}], [{"entity": "immersion"}], [{"entity": "climate.hall"}],
    ["switch.a", "switch.a"], [{"entity": "switch.a", "colour": "red"}], [{"entity": "switch.a", "name": 3}], [5],
])
def test_bad_items_are_refused(bad):
    with pytest.raises(ConfigError):
        cfg(bad)


def test_turns_on_for_a_session_and_off_at_its_end():
    s = FreeLoadState()
    assert s.step(True, "off").service == "turn_on"
    assert s.step(True, "on") is None                    # once is enough
    assert s.step(False, "on").service == "turn_off"
    assert s.step(False, "off") is None


def test_a_switch_already_on_is_left_alone():
    s = FreeLoadState()
    assert s.step(True, "on") is None
    assert s.step(False, "on") is None and not s.held


def test_turned_off_by_the_owner_stays_off_for_the_session_then_the_next_one_starts_afresh():
    s = FreeLoadState()
    s.step(True, "off")
    assert s.step(True, "off") is None                   # the owner switched it off: not switched on again
    assert s.step(False, "off") is None
    assert s.step(True, "off").service == "turn_on"      # the next session


def test_an_unreadable_switch_is_not_touched():
    s = FreeLoadState()
    assert s.step(True, "unavailable") is None and not s.seen
    assert s.step(True, "off").service == "turn_on"


def test_state_survives_a_restart():
    s = FreeLoadState()
    s.step(True, "off")
    again = FreeLoadState.from_dict(s.to_dict())
    assert again.step(False, "on").service == "turn_off"
