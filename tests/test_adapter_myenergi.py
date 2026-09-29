"""The Zappi adapter: plug-status classification (the logic Readings.ev_state() always had), completion, reading the
raw fields, the EVAdapter protocol and the registry."""

from __future__ import annotations

import pytest
from fixtures import CONFIG, NOW, STATES, S, get_state

from pe_core.adapters import EV_CHARGER, EVAdapter, EVState, display
from pe_core.adapters import get as get_adapter
from pe_core.adapters import names as adapter_names
from pe_core.adapters.myenergi import ZappiCharger
from pe_core.readings import Readings, read

ROLE_ENTITY = {"ev_charge_power": "sensor.car_power", "ev_plug_status": "sensor.plug",
               "ev_charger_status": "sensor.charger", "ev_charge_mode": "select.mode",
               "ev_session_energy": "sensor.session"}


def roles(states=None):
    states = STATES if states is None else states
    return lambda role: states.get(ROLE_ENTITY.get(role, ""))


class FakeHA:
    def __init__(self, states=None):
        self.states = STATES if states is None else states

    def get_state(self, entity_id=None, attribute=None):
        return self.states.get(entity_id)

    def call_service(self, service, **data):
        pass


@pytest.mark.parametrize("plug,power,expected", [
    ("Charging", 0, "charging"), ("charging", None, "charging"), (" Charging ", 7000, "charging"),
    ("EV Disconnected", 0, "unplugged"), ("EV Disconnected", 7000, "unplugged"),
    ("EV Connected", 0, "plugged_in"), ("Waiting for EV", 0, "plugged_in"), ("Paused", 5000, "plugged_in"),
    ("unknown", 0, "unplugged"), ("unavailable", None, "unplugged"), ("", 50, "unplugged"), (None, 100, "unplugged"),
    ("unknown", 101, "charging"), ("unavailable", 7000, "charging"), (None, 7000, "charging"),
])
def test_classify(plug, power, expected):
    assert ZappiCharger.classify(plug, power) == expected
    assert Readings(now=NOW, ev_plug=plug, ev_power=power).ev_state() == expected     # Readings delegates


@pytest.mark.parametrize("plug_state,status,expected", [
    ("plugged_in", "Completed", True), ("plugged_in", "Charge complete", True), ("plugged_in", "COMPLETED", True),
    ("plugged_in", "Paused", False), ("plugged_in", None, False), ("plugged_in", "", False),
    ("unplugged", "Completed", False), ("charging", "Completed", False),
])
def test_complete(plug_state, status, expected):
    assert ZappiCharger.complete(plug_state, status) is expected


def test_readings_ev_complete_uses_plug_and_status():
    assert Readings(now=NOW, ev_plug="EV Connected", ev_status="Completed").ev_complete() is True
    assert Readings(now=NOW, ev_plug="EV Disconnected", ev_status="Completed").ev_complete() is False
    assert Readings(now=NOW, ev_plug="Waiting for EV", ev_status="Paused").ev_complete() is False


def test_readings_stay_plain_and_accept_a_custom_adapter():
    class Fake(ZappiCharger):
        @staticmethod
        def classify(plug, power_w):
            return "charging"
    assert Readings(now=NOW, ev_plug="EV Disconnected", ev=Fake()).ev_state() == "charging"
    assert Readings(now=NOW) == Readings(now=NOW, ev=Fake())        # the adapter isn't part of equality
    assert ", ev=" not in repr(Readings(now=NOW))


def test_read_raw_fields():
    states = {**STATES, "sensor.car_power": S("7.2", "kW"), "sensor.plug": S("Charging"),
              "sensor.charger": S("Charging"), "select.mode": S("Eco+"), "sensor.session": S("1500", "Wh")}
    raw = ZappiCharger().read(roles(states))
    assert raw == {"power_w": 7200, "plug": "Charging", "status": "Charging", "mode": "Eco+", "session_kwh": 1.5}


def test_read_fixture_and_missing():
    raw = ZappiCharger().read(roles())
    assert raw["plug"] == "Waiting for EV" and raw["status"] == "Completed" and raw["power_w"] == 0
    assert raw["mode"] is None and raw["session_kwh"] is None
    assert ZappiCharger().read(roles({})) == {"power_w": None, "plug": None, "status": None, "mode": None,
                                              "session_kwh": None}


def test_read_puts_the_same_values_in_readings():
    r = read(CONFIG, get_state(), NOW)
    assert (r.ev_plug, r.ev_status, r.ev_power) == ("Waiting for EV", "Completed", 0)
    assert r.ev_state() == "plugged_in" and r.ev_complete() is True
    assert read(CONFIG, get_state(), NOW, ev=ZappiCharger()).ev_state() == "plugged_in"


def test_evstate_mapping():
    def ev(plug, status, power):
        states = {**STATES, "sensor.plug": S(plug), "sensor.charger": S(status), "sensor.car_power": S(str(power), "W")}
        return ZappiCharger(ROLE_ENTITY.get).state(FakeHA(states))
    assert ev("Charging", "Charging", 7000) == EVState(plugged=True, charging=True, complete=False, power_w=7000)
    assert ev("EV Connected", "Completed", 0) == EVState(plugged=True, charging=False, complete=True, power_w=0)
    assert ev("Waiting for EV", "Paused", 0) == EVState(plugged=True, charging=False, complete=False, power_w=0)
    assert ev("EV Disconnected", "Completed", 0) == EVState(plugged=False, charging=False, complete=False, power_w=0)


def test_state_needs_a_role_map():
    with pytest.raises(RuntimeError):
        ZappiCharger().state(FakeHA())


def test_protocol_registry_and_names():
    z = get_adapter("ev", "zappi")(ROLE_ENTITY.get)
    assert isinstance(z, ZappiCharger) and isinstance(z, EVAdapter) and z.name == "zappi"
    assert "zappi" in adapter_names("ev")
    assert display(EV_CHARGER, z.display_names()) == "Zappi"
