"""The Kraken tariff adapter: parsing (the same as read() always did), the protocol wrappers, supplier detection,
registry lookups and the smart-charge request translation. States are dict-backed fakes in the Octopus Energy
integration's real attribute shapes."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fixtures import BST, CONFIG, NOW, STATES, S, get_state

from pe_core.adapters import DISPATCH, GRID_EVENT, TARIFF, Dispatch, HomeAssistant, TariffAdapter, display
from pe_core.adapters import get as get_adapter
from pe_core.adapters import names as adapter_names
from pe_core.adapters.kraken import DEFAULT_READY_BY_OPTIONS, KrakenTariff, supplier_of
from pe_core.readings import read

ROLE_ENTITY = {
    "import_rate_now": "sensor.rate_now", "export_rate": "sensor.export_rate", "standing_charge": "sensor.standing",
    "import_rates_today": "event.rates_today", "import_rates_tomorrow": "event.rates_tomorrow",
    "smart_dispatches": "binary_sensor.dispatching", "offpeak_now": "binary_sensor.offpeak",
    "free_power_active": "binary_sensor.free_now", "free_power_next_start": "sensor.free_start",
    "free_power_next_end": "sensor.free_end",
}

def roles(states=None):
    """A `role -> state` accessor over fake states."""
    states = STATES if states is None else states
    return lambda role: states.get(ROLE_ENTITY.get(role, ""))

class FakeHA:
    def __init__(self, states=None):
        self.states = STATES if states is None else states
        self.calls = []

    def get_state(self, entity_id=None, attribute=None):
        return self.states.get(entity_id)

    def call_service(self, service, **data):
        self.calls.append((service, data))

def test_rates_cover_today_and_tomorrow():
    rates = KrakenTariff().read_rates(roles())
    assert len(rates) == 96
    assert rates[0].start.astimezone(BST).day == 22 and rates[-1].start.astimezone(BST).day == 23
    assert rates[0].value == 0.06993 and rates[-1].value == 0.302831

def test_rates_missing_or_malformed_are_empty():
    assert KrakenTariff().read_rates(roles({})) == []
    bad = {"event.rates_today": S("x", rates="junk"), "event.rates_tomorrow": S("x", rates=[{"start": "bad"}, 3])}
    assert KrakenTariff().read_rates(roles(bad)) == []

def test_import_and_export_rate_units():
    t = KrakenTariff()
    assert t.read_import_rate(roles()) == 0.302831 and t.read_export_rate(roles()) == 0.15
    pence = {"sensor.rate_now": S("30.28", "p/kWh"), "sensor.export_rate": S("15", "p/kWh")}
    assert t.read_import_rate(roles(pence)) == pytest.approx(0.3028)
    assert t.read_export_rate(roles(pence)) == pytest.approx(0.15)
    assert t.read_import_rate(roles({})) is None and t.read_export_rate(roles({})) is None
    assert t.read_import_rate(roles({"sensor.rate_now": S("unavailable")})) is None

def test_standing_charge_in_pounds_and_in_pence():
    t = KrakenTariff()
    assert t.read_standing_charge(roles({"sensor.standing": S("0.5709", "GBP/day")})) == 0.5709
    assert t.read_standing_charge(roles({"sensor.standing": S("57.09", "p")})) == pytest.approx(0.5709)
    assert t.read_standing_charge(roles({"sensor.standing": S("57.09", "p/day")})) == pytest.approx(0.5709)
    assert t.read_standing_charge(roles({"sensor.standing": S("unknown")})) is None
    assert t.read_standing_charge(roles({})) is None

def test_dispatches_carry_charge_in_kwh_and_completed_are_separate():
    states = {"binary_sensor.dispatching": S("off", planned_dispatches=[
        {"start": "2026-09-22T21:00:00+01:00", "end": "2026-09-22T21:30:00+01:00", "charge_in_kwh": -0.7},
        {"start": "2026-09-22T21:30:00+01:00", "end": "2026-09-23T09:30:00+01:00", "charge_in_kwh": -84.0},
    ], completed_dispatches=[
        {"start": "2026-09-22T01:00:00+01:00", "end": "2026-09-22T01:30:00+01:00", "charge_in_kwh": -2.5}])}
    planned, completed = KrakenTariff().read_dispatches(roles(states))
    assert [w.value for w in planned] == [-0.7, -84.0]
    assert planned[0].start.astimezone(BST).hour == 21
    assert [w.value for w in completed] == [-2.5]
    assert KrakenTariff().read_dispatches(roles({})) == ([], [])

@pytest.mark.parametrize("state,expected", [("on", True), ("off", False)])
def test_offpeak_on_off(state, expected):
    assert KrakenTariff().read_offpeak(roles({"binary_sensor.offpeak": S(state)})) is expected

def test_offpeak_missing_is_none():
    assert KrakenTariff().read_offpeak(roles({})) is None

def test_free_sessions():
    t = KrakenTariff()
    assert t.read_free_sessions(roles()) == (False, None, None)
    states = {"binary_sensor.free_now": S("on"), "sensor.free_start": S("2026-09-22T18:00:00+01:00"),
              "sensor.free_end": S("2026-09-22T19:00:00+01:00")}
    active, start, end = t.read_free_sessions(roles(states))
    assert active is True and start.astimezone(BST).hour == 18 and end - start == timedelta(hours=1)
    assert t.read_free_sessions(roles({})) == (False, None, None)

def test_read_puts_the_same_values_in_readings():
    """read() through the adapter gives what it always did (also covered by the replay)."""
    r = read(CONFIG, get_state(), NOW)
    t = KrakenTariff()
    st = roles()
    assert r.rates == t.read_rates(st) and r.dispatches == t.read_dispatches(st)[0]
    assert r.import_rate == 0.302831 and r.export_rate == 0.15 and r.offpeak_now is None
    explicit = read(CONFIG, get_state(), NOW, tariff=KrakenTariff("octopus"))
    assert explicit.rates == r.rates and explicit.dispatches == r.dispatches

def test_supplier_detection():
    assert supplier_of("sensor.edf_energy_electricity_21l_1012_current_rate") == "edf"
    assert supplier_of("sensor.octopus_energy_electricity_abc_current_rate") == "octopus"
    assert supplier_of("sensor.rate_now") == "octopus" and supplier_of(None) == "octopus"

def test_unknown_supplier_is_rejected():
    with pytest.raises(ValueError):
        KrakenTariff("bulb")

def test_display_names_per_supplier():
    edf, octo = KrakenTariff("edf").display_names(), KrakenTariff("octopus").display_names()
    assert display(DISPATCH, edf) == "EDF smart slot" and display(TARIFF, edf) == "EDF"
    assert display(DISPATCH, octo) == "Octopus intelligent dispatch"
    assert GRID_EVENT in edf and GRID_EVENT in octo

def test_protocol_conformance_and_wrappers():
    ha = FakeHA()
    t = KrakenTariff("edf", ROLE_ENTITY.get)
    assert isinstance(t, TariffAdapter) and isinstance(ha, HomeAssistant)
    assert len(t.import_rates(ha, NOW)) == 96
    assert t.export_rate(ha, NOW) == 0.15
    d = t.dispatches(ha, NOW)
    assert d[0] == Dispatch(datetime(2026, 9, 22, 21, 0, tzinfo=BST), datetime(2026, 9, 22, 21, 30, tzinfo=BST), 0.7)
    assert d[1].kwh == 84.0 and d[1].for_car is True          # kWh reported as positive in the neutral shape
    assert t.grid_events(ha, NOW) == []

def test_wrappers_need_a_role_map_and_tolerate_unmapped_roles():
    with pytest.raises(RuntimeError):
        KrakenTariff("edf").import_rates(FakeHA(), NOW)
    assert KrakenTariff("edf", lambda role: None).import_rates(FakeHA(), NOW) == []

def test_registry_lookups():
    assert {"edf", "octopus"} <= set(adapter_names("tariff"))
    edf, octo = get_adapter("tariff", "edf")(ROLE_ENTITY.get), get_adapter("tariff", "octopus")()
    assert isinstance(edf, KrakenTariff) and edf.name == "edf" and edf.role_entity == ROLE_ENTITY.get
    assert octo.name == "octopus"

def test_smart_request_translation():
    t = KrakenTariff("edf")
    assert t.ready_by_call("select.edf_x_intelligent_target_time", "07:30") == (
        "select/select_option", {"entity_id": "select.edf_x_intelligent_target_time", "option": "07:30"})
    assert t.ready_by_call("time.ready_by", "07:30") == (
        "time/set_value", {"entity_id": "time.ready_by", "time": "07:30:00"})
    assert t.charge_target_call("number.target", "80") == ("number/set_value", {"entity_id": "number.target",
                                                                                "value": 100})
    assert t.charge_target_call("number.target", None) is not None
    assert t.charge_target_call("number.target", "100") is None and t.charge_target_call("number.target", 100.0) is None
    assert t.charge_target_call(None, "80") is None

def test_ready_by_options():
    listed = {"state": "06:00", "attributes": {"options": ["06:00", "07:00"]}}
    assert KrakenTariff.ready_by_options(listed) == ["06:00", "07:00"]
    assert KrakenTariff.ready_by_options({"state": "06:00", "attributes": {}}) == DEFAULT_READY_BY_OPTIONS
    assert KrakenTariff.ready_by_options(None) == DEFAULT_READY_BY_OPTIONS
    assert DEFAULT_READY_BY_OPTIONS[0] == "04:00" and len(DEFAULT_READY_BY_OPTIONS) == 15

def test_smart_request_writes_through_the_ha_door():
    """The translation feeds call_service exactly as the app does."""
    ha, t = FakeHA(), KrakenTariff("edf")
    service, data = t.ready_by_call("select.rb", "06:30")
    ha.call_service(service, **data)
    assert ha.calls == [("select/select_option", {"entity_id": "select.rb", "option": "06:30"})]

