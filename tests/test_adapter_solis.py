"""Tests for the Solis inverter adapter (Phase 0, steps 2a-2b): timed windows and RAM remote control, moved
out of the app."""

from __future__ import annotations

from datetime import datetime, timedelta

from pe_core.adapters import InverterAdapter, get
from pe_core.adapters.solis import SolisInverter
from pe_core.control import SELF_USE_MODE
from pe_core.decide import EXPORT, GRID_CHARGE, HOLD, SELF_USE, Decision

NOW = datetime(2026, 9, 26, 23, 10)

FIRST_ROLES = ("timed_charge_start_hour", "timed_charge_start_minute", "timed_charge_end_hour",
               "timed_charge_end_minute", "timed_discharge_start_hour", "timed_discharge_start_minute",
               "timed_discharge_end_hour", "timed_discharge_end_minute", "timed_update_button")

ENTITY = {role: f"number.pe_{role}" for role in FIRST_ROLES}
ENTITY["timed_update_button"] = "button.pe_timed_update_button"
FOR_SLOTS_2_3 = {n: {role: f"{ENTITY[role]}_{n}" for role in FIRST_ROLES} for n in (2, 3)}


class FakeHA:
    """A dict-backed fake HA: states keyed by entity id."""

    def __init__(self, states=None):
        self.states = dict(states or {})
        self.calls = []

    def get_state(self, entity_id=None, attribute=None):
        if entity_id is None:
            return dict(self.states)
        return self.states.get(entity_id)

    def call_service(self, service, **data):
        self.calls.append((service, data))


def role_entity_all_slots(role):
    """A role_entity function where slots 1-3 all exist (SolaX '_2'/'_3' suffixes)."""
    return ENTITY.get(role)


def role_entity_no_slots():
    """A role_entity function where the '_2'/'_3' entities don't exist."""
    def f(role):
        return ENTITY.get(role) if role != "timed_update_button" else None
    return f


def make_ha_with_slots(extra=None):
    states = {eid: "0" for eid in ENTITY.values()}
    for m in FOR_SLOTS_2_3.values():
        for eid in m.values():
            states[eid] = "0"
    states.update(extra or {})
    return FakeHA(states)


# ---------------------------------------------------------------------------
# slot_map
# ---------------------------------------------------------------------------


def test_slot_map_found_when_all_three_slots_exist():
    ha = make_ha_with_slots()
    inv = SolisInverter(ha, role_entity_all_slots)
    m, warn = inv.slot_map(NOW)
    assert warn is False
    assert set(m) == {1, 2, 3}
    assert m[1]["timed_charge_start_hour"] == ENTITY["timed_charge_start_hour"]
    assert m[2]["timed_charge_start_hour"] == FOR_SLOTS_2_3[2]["timed_charge_start_hour"]


def test_slot_map_missing_role_entity_returns_none_no_warning():
    inv = SolisInverter(FakeHA(), lambda role: None)
    m, warn = inv.slot_map(NOW)
    assert m is None and warn is False


def test_slot_map_not_found_warns_once_then_caches_until_recheck():
    # slot 1 entities exist, but the "_2"/"_3" ones don't (entity_exists is False for them)
    ha = FakeHA({eid: "0" for eid in ENTITY.values()})
    inv = SolisInverter(ha, role_entity_all_slots)
    m1, warn1 = inv.slot_map(NOW)
    assert m1 is None and warn1 is True                     # first miss: warn
    m2, warn2 = inv.slot_map(NOW + timedelta(seconds=1))
    assert m2 is None and warn2 is False                     # cached: no recheck, no repeat warning
    m3, warn3 = inv.slot_map(NOW + timedelta(seconds=599))
    assert m3 is None and warn3 is False                     # still within the 600 s cache window
    m4, warn4 = inv.slot_map(NOW + timedelta(seconds=601))
    assert m4 is None and warn4 is False                     # rechecked, still missing: no repeat warning


def test_slot_map_found_after_not_found_is_kept():
    ha = FakeHA({eid: "0" for eid in ENTITY.values()})
    inv = SolisInverter(ha, role_entity_all_slots)
    inv.slot_map(NOW)                                        # not found, cached
    for m in FOR_SLOTS_2_3.values():                          # the "_2"/"_3" entities show up
        ha.states.update({eid: "0" for eid in m.values()})
    m, warn = inv.slot_map(NOW + timedelta(seconds=601))
    assert set(m) == {1, 2, 3} and warn is False


# ---------------------------------------------------------------------------
# slot_keys / read
# ---------------------------------------------------------------------------


def test_slot_keys_flattens_slots_and_adds_the_currents_and_mode():
    ha = make_ha_with_slots()
    inv = SolisInverter(ha, role_entity_all_slots)
    m, _ = inv.slot_map(NOW)
    keys = inv.slot_keys(m)
    assert keys["timed_charge_start_hour#1"] == ENTITY["timed_charge_start_hour"]
    assert keys["timed_charge_start_hour#2"] == FOR_SLOTS_2_3[2]["timed_charge_start_hour"]
    assert keys["timed_charge_current"] == role_entity_all_slots("timed_charge_current")
    assert keys["storage_mode"] == role_entity_all_slots("storage_mode")


def test_read_skips_update_button_and_missing_entities():
    ha = FakeHA({"number.a": "5", "number.b": "7"})
    inv = SolisInverter(ha, lambda role: None)
    out = inv.read({"a": "number.a", "b": "number.b", "timed_update_button#1": "button.x", "missing": None})
    assert out == {"a": "5", "b": "7"}


# ---------------------------------------------------------------------------
# release_slots / release_rolling
# ---------------------------------------------------------------------------


def test_release_slots_closes_all_windows_and_selects_self_use():
    entities = {"timed_charge_start_hour#1": "e1", "timed_charge_current": "e2", "storage_mode": "e3",
                "timed_update_button#1": "e4"}
    have = {"timed_charge_start_hour#1": 23, "timed_charge_current": 90, "storage_mode": "Self-Use - No Export"}
    writes = inv_for_release().release_slots(entities, have)
    roles = {w.role: w for w in writes}
    assert roles["timed_charge_start_hour#1"].value == 0
    assert roles["storage_mode"].value == SELF_USE_MODE
    assert "timed_update_button#1" in roles                  # a window value changed: press the button
    assert "timed_charge_current" not in roles               # currents aren't part of release_slots' `want`


def inv_for_release():
    return SolisInverter(FakeHA(), lambda role: None)


def test_release_rolling_matches_control_release():
    from pe_core.control import release as control_release
    assert SolisInverter(FakeHA(), lambda role: None).release_rolling() == control_release()


# ---------------------------------------------------------------------------
# rolling / slot_writes
# ---------------------------------------------------------------------------


def test_rolling_opens_a_window_for_grid_charge():
    inv = SolisInverter(FakeHA(), lambda role: None)
    kind, end, want = inv.rolling(Decision(GRID_CHARGE, "plan", "x"), NOW, None, None, "rolling", 4800, 4800)
    assert kind == "charge"
    assert end == datetime(2026, 9, 26, 23, 45)
    assert want["timed_charge_start_hour"] == 23 and want["timed_charge_current"] == 90


def test_rolling_self_use_has_no_kind_or_end():
    inv = SolisInverter(FakeHA(), lambda role: None)
    kind, end, want = inv.rolling(Decision(SELF_USE, "x", "x"), NOW, None, None, "rolling", 4800, 4800)
    assert kind is None and end is None
    assert want["storage_mode"] == SELF_USE_MODE


def test_slot_writes_matches_schedule_desired_state_and_writes_for():
    from pe_core.schedule import desired_state, writes_for
    have = {}
    pers = []
    inv = SolisInverter(FakeHA(), lambda role: None, volts=52.0)
    want, writes = inv.slot_writes(pers, have, NOW, SELF_USE, None, 4800, 4800)
    exp_want = desired_state(pers, have, NOW, SELF_USE, None, 52.0, 4800, 4800)
    assert want == exp_want
    assert [w.as_dict() for w in writes] == [w.as_dict() for w in writes_for(exp_want, have)]


# ---------------------------------------------------------------------------
# confirmed / verify
# ---------------------------------------------------------------------------


def test_confirmed_accepts_string_or_int_and_the_dot_zero_float_reading():
    ha = FakeHA({"e": "5"})
    inv = SolisInverter(ha, lambda role: None)
    assert inv.confirmed("e", 5) is True                     # state "5" == str(5)
    assert inv.confirmed("e", "5") is True                   # state "5" == str("5")
    assert inv.confirmed("e", 6) is False
    ha2 = FakeHA({"e": "5.0"})                                # HA reports a number as "N.0"
    assert SolisInverter(ha2, lambda role: None).confirmed("e", 5) is True


def test_verify_reports_mismatches_and_skips_buttons():
    from pe_core.control import Write
    ha = FakeHA({"e1": "5", "e2": "9"})
    inv = SolisInverter(ha, lambda role: {"a": "e1", "b": "e2"}.get(role))
    writes = [Write("a", 5, "number"), Write("b", 7, "number"), Write("timed_update_button", None, "button")]
    msgs = inv.verify(writes, ha)
    assert len(msgs) == 1 and "b" in msgs[0]


# ---------------------------------------------------------------------------
# write_storage
# ---------------------------------------------------------------------------


def test_write_storage_staged_for_window_times_eeprom_otherwise():
    from pe_core.control import Write
    inv = SolisInverter(FakeHA(), lambda role: None)
    assert inv.write_storage(Write("timed_charge_start_hour", 23, "number")) == "staged"
    assert inv.write_storage(Write("timed_charge_current", 90, "number")) == "eeprom"
    assert inv.write_storage(Write("storage_mode", "Self-Use", "select")) == "eeprom"
    assert inv.write_storage(Write("timed_update_button", None, "button")) == "eeprom"


# ---------------------------------------------------------------------------
# capabilities / protocol conformance / registry
# ---------------------------------------------------------------------------


def test_capabilities_lists_methods_actions_and_never_touch():
    inv = SolisInverter(FakeHA(), lambda role: None, max_charge_w=6000, max_discharge_w=6000)
    caps = inv.capabilities()
    assert [m.name for m in caps.methods] == ["ram_remote", "timed_windows"]
    ram = caps.method("ram_remote")
    assert ram.storage == "ram" and ram.failsafe_min == 5 and ram.max_power_w == 5000
    timed = caps.method("timed_windows")
    assert timed.storage == "eeprom" and timed.failsafe_min is None and timed.max_power_w is None
    assert {GRID_CHARGE, HOLD, EXPORT, SELF_USE} <= caps.actions
    assert caps.max_charge_w == 6000 and caps.max_discharge_w == 6000
    assert caps.never_touch == ("Backup", "Off-Grid")


def test_capabilities_ram_power_cap_comes_from_the_constructor():
    inv = SolisInverter(FakeHA(), lambda role: None, ram_max_w=3500)
    assert inv.capabilities().method("ram_remote").max_power_w == 3500


def test_solis_inverter_satisfies_the_inverter_adapter_protocol():
    inv = SolisInverter(FakeHA(), lambda role: None)
    assert isinstance(inv, InverterAdapter)
    assert inv.name == "solis"


def test_registered_under_inverter_solis():
    factory = get("inverter", "solis")
    assert factory is SolisInverter


# ---------------------------------------------------------------------------
# RAM remote control (step 2b)
# ---------------------------------------------------------------------------


RC_ENTITIES = {
    "rc_mode": "select.solis_battery_control_override",
    "rc_charge_power": "number.solis_battery_control_override_charge_power",
    "rc_discharge_power": "number.solis_battery_control_override_discharge_power",
}


def test_rc_entities_found_and_cached_when_all_three_are_present():
    ha = FakeHA({eid: "0" for eid in RC_ENTITIES.values()})
    inv = SolisInverter(ha, lambda role: None)
    found = inv.rc_entities(NOW)
    assert found == RC_ENTITIES
    # even if entities later vanish from HA's state, the cache is kept for 10 minutes
    ha.states.clear()
    still = inv.rc_entities(NOW + timedelta(minutes=9))
    assert still == RC_ENTITIES


def test_rc_entities_rechecks_when_fewer_than_three_are_found():
    partial = dict(list(RC_ENTITIES.items())[:2])
    ha = FakeHA({eid: "0" for eid in partial.values()})
    inv = SolisInverter(ha, lambda role: None)
    first = inv.rc_entities(NOW)
    assert first == partial
    ha.states.update({RC_ENTITIES["rc_discharge_power"]: "0"})   # the third one appears
    second = inv.rc_entities(NOW + timedelta(seconds=1))          # not cached: found only 2 last time
    assert second == RC_ENTITIES


def test_rc_entities_rechecks_after_ten_minutes_even_if_complete():
    ha = FakeHA({eid: "0" for eid in RC_ENTITIES.values()})
    inv = SolisInverter(ha, lambda role: None)
    inv.rc_entities(NOW)
    ha.states.clear()
    ha.states[RC_ENTITIES["rc_mode"]] = "0"                       # only one left, past the 10-minute cache
    found = inv.rc_entities(NOW + timedelta(minutes=10, seconds=1))
    assert found == {"rc_mode": RC_ENTITIES["rc_mode"]}


def test_rc_missing_lists_the_absent_roles():
    inv = SolisInverter(FakeHA(), lambda role: None)
    assert inv.rc_missing({}) == ["rc_mode", "rc_charge_power", "rc_discharge_power"]
    assert inv.rc_missing({"rc_mode": "x"}) == ["rc_charge_power", "rc_discharge_power"]
    assert inv.rc_missing(RC_ENTITIES) == []


def test_ram_command_matches_ramcontrol_command_for():
    from pe_core import ramcontrol
    inv = SolisInverter(FakeHA(), lambda role: None)
    got = inv.ram_command(GRID_CHARGE, 3000, 4800, 4800, 5000)
    expected = ramcontrol.command_for(GRID_CHARGE, 3000, 4800, 4800, 5000)
    assert got == expected


def test_ram_off_command_is_off():
    from pe_core.rctest import OPTION_OFF
    inv = SolisInverter(FakeHA(), lambda role: None)
    cmd = inv.ram_off_command()
    assert cmd.option == OPTION_OFF and cmd.watts == 0


def test_service_for_select_and_number():
    from pe_core.control import Write
    inv = SolisInverter(FakeHA(), lambda role: None)
    assert inv.service_for(Write("storage_mode", "Self-Use", "select")) == \
        ("select/select_option", {"option": "Self-Use"})
    assert inv.service_for(Write("timed_charge_current", 90, "number")) == \
        ("number/set_value", {"value": 90})


def test_verify_uses_entities_for_slot_keys_when_given():
    from pe_core.control import Write
    ha = FakeHA({"number.slot2": "23"})
    inv = SolisInverter(ha, lambda role: None)          # role_entity would find nothing for a slot-keyed role
    writes = [Write("timed_charge_start_hour#2", 23, "number")]
    entities = {"timed_charge_start_hour#2": "number.slot2"}
    assert inv.verify(writes, ha, entities) == []
    bad = [Write("timed_charge_start_hour#2", 7, "number")]
    msgs = inv.verify(bad, ha, entities)
    assert len(msgs) == 1 and "timed_charge_start_hour#2" in msgs[0]


def test_verify_falls_back_to_role_entity_without_entities():
    from pe_core.control import Write
    ha = FakeHA({"number.a": "5"})
    inv = SolisInverter(ha, lambda role: {"a": "number.a"}.get(role))
    assert inv.verify([Write("a", 5, "number")], ha) == []
