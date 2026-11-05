"""The publisher adapter: native entities under the integration, or plain states."""
import json
import sys
from unittest import mock

import pytest

from pe_core.adapters.publish import DirectPublisher, StatePublisher, select_publisher
from pe_core.config import ConfigError, parse_config, settings_catalogue
from pe_core.entities import ENTITIES, EntityDef, solar_plant_entity

VERSION = "0.9.99"


class FakeHA:
    def __init__(self):
        self.calls = []

    def set_state(self, entity_id, state=None, attributes=None):
        self.calls.append((entity_id, state, attributes))

    def last(self, entity_id):
        return next(c for c in reversed(self.calls) if c[0] == entity_id)


def ent(key):
    return next(e for e in ENTITIES if e.key == key)


# --- entity ids -----------------------------------------------------------------------------

def test_direct_entity_id_is_the_entitys_own():
    dynamic = [solar_plant_entity("main", "Main"), solar_plant_entity("shed_2", "Shed")]
    for e in list(ENTITIES) + dynamic:
        ha = FakeHA()
        DirectPublisher(ha.set_state).discover(e, VERSION)
        assert ha.calls[0][0] == e.entity_id == f"{e.component}.pe_{e.key}"


def test_publishers_satisfy_the_protocol():
    from pe_core.adapters.publish import NativePublisher
    assert isinstance(DirectPublisher(FakeHA().set_state), StatePublisher)
    assert isinstance(NativePublisher(FakeNative(), FakeHA().set_state), StatePublisher)


# --- direct -----------------------------------------------------------------------------------

def test_direct_sensor_gets_its_definitions_attributes_and_json_attributes():
    ha = FakeHA()
    d = DirectPublisher(ha.set_state)
    e = ent("diag_battery_temperature")
    d.discover(e, VERSION)
    eid, state, attrs = ha.last(e.entity_id)
    assert state == "unknown"
    assert attrs == {"friendly_name": "PowerEngine Battery temperature (estimated)", "unit_of_measurement": "°C",
                     "device_class": "temperature", "state_class": "measurement"}
    d.publish("diag_battery_temperature", 12.5, json.dumps({"measured": True}))
    assert ha.last(e.entity_id)[1:] == ("12.5", {**attrs, "measured": True})
    d.publish("diag_battery_temperature", 13)              # attributes stay until replaced
    assert ha.last(e.entity_id)[2]["measured"] is True and ha.last(e.entity_id)[1] == "13"


def test_direct_enum_sensor_carries_its_options():
    ha = FakeHA()
    DirectPublisher(ha.set_state).discover(ent("state_operation_mode"), VERSION)
    attrs = ha.last("sensor.pe_state_operation_mode")[2]
    assert attrs["options"] == ["unconfigured", "passive", "active", "paused"] and attrs["device_class"] == "enum"


def test_direct_switch_and_select():
    ha = FakeHA()
    d = DirectPublisher(ha.set_state)
    d.discover(ent("ctl_pause"), VERSION)
    d.preset("ctl_pause", "ON")
    assert ha.last("switch.pe_ctl_pause")[1] == "on"
    assert ha.last("switch.pe_ctl_pause")[2] == {"friendly_name": "PowerEngine Pause control",
                                                 "icon": "mdi:pause-octagon"}
    d.discover(ent("diag_config_ok"), VERSION)
    d.publish("diag_config_ok", "OFF")
    assert ha.last("binary_sensor.pe_diag_config_ok")[1] == "off"
    choice = EntityDef("select", "ui_test_choice", "Test choice",
                       {"icon": "mdi:calendar-search", "options": ["Today", "Yesterday"]})
    d.discover(choice, VERSION)
    d.preset("ui_test_choice", "Yesterday")
    eid, state, attrs = ha.last("select.pe_ui_test_choice")
    assert state == "Yesterday" and attrs["options"] == ["Today", "Yesterday"]
    assert attrs["icon"] == "mdi:calendar-search"


def test_direct_rediscovery_keeps_the_value():
    ha = FakeHA()
    d = DirectPublisher(ha.set_state)
    e = solar_plant_entity("main", "Main")
    d.discover(e, VERSION)
    d.publish(e.key, 1500)
    d.discover(e, VERSION)                                 # a config save re-syncs the plants
    assert ha.last(e.entity_id)[1] == "1500"


def test_direct_unavailable_and_retire():
    ha = FakeHA()
    d = DirectPublisher(ha.set_state)
    d.discover(ent("diag_version"), VERSION)
    d.publish("diag_version", "0.9.99", {"names": {"supplier": "EDF"}})
    d.available(False)
    eid, state, attrs = ha.last("sensor.pe_diag_version")
    assert state == "unavailable" and attrs["names"] == {"supplier": "EDF"}
    d.retire(ent("diag_version"))
    assert ha.last("sensor.pe_diag_version")[1] == "unavailable"
    assert ha.last("sensor.pe_diag_version")[2]["note"] == "retired by PowerEngine"
    removed = []
    DirectPublisher(ha.set_state, removed.append).retire(ent("diag_version"))
    assert removed == ["sensor.pe_diag_version"]


def test_direct_retire_all_covers_every_entity():
    ha = FakeHA()
    DirectPublisher(ha.set_state).retire_all()
    assert {c[0] for c in ha.calls} == {e.entity_id for e in ENTITIES}


# --- choosing one --------------------------------------------------------------------------------

def test_the_publisher_is_native_where_the_host_makes_entities_else_plain_states_else_none():
    from pe_core.adapters.publish import NativePublisher
    ha = FakeHA()
    assert isinstance(select_publisher(ha.set_state, native=FakeNative()), NativePublisher)
    assert isinstance(select_publisher(ha.set_state), DirectPublisher)
    assert select_publisher(None) is None and select_publisher(None, native=FakeNative()) is None


def test_the_retired_publisher_setting_is_accepted_in_a_saved_config_and_ignored():
    for value in ("auto", "mqtt", "direct", "carrier-pigeon"):
        assert "publisher" not in parse_config({"system": {"publisher": value}}).system
    assert "publisher" not in parse_config({}).system
    assert not [s for s in settings_catalogue()["system"] if s["key"] == "publisher"]
    with pytest.raises(ConfigError):
        parse_config({"system": {"no_such_setting": 1}})


def test_the_app_publishes_natively_under_the_integration_and_as_plain_states_otherwise():
    from pe_core.adapters.publish import NativePublisher
    with mock.patch.dict(sys.modules):
        sys.modules.pop("powerengine", None)
        import powerengine
        e = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
        e.cfg, e.set_state = None, FakeHA().set_state
        assert isinstance(e._get_publisher(), DirectPublisher)
        e = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
        e.cfg = None
        e.__dict__["_host"] = type("H", (), {"native_entities": FakeNative(),
                                             "state_writer": lambda self, told, demo=False: FakeHA().set_state})()
        assert isinstance(e._get_publisher(), NativePublisher)
        sys.modules.pop("powerengine", None)


# --- native entities (the engine running under the Home Assistant integration)
class FakeNative:
    def __init__(self):
        self.defined, self.retired, self.online_calls, self.fail = [], [], [], False

    def define(self, component, key, name, options):
        if self.fail:
            raise RuntimeError("not connected")
        self.defined.append((component, key, name, options))

    def retire(self, entity_id):
        self.retired.append(entity_id)

    def online(self, online):
        self.online_calls.append(online)


def test_the_native_publisher_defines_each_entity_then_writes_only_its_extra_attributes():
    from pe_core.adapters.publish import NativePublisher, select_publisher
    from pe_core.entities import EntityDef
    native, writes = FakeNative(), []
    pub = select_publisher(lambda eid, **kw: writes.append((eid, kw)), native=native)
    assert isinstance(pub, NativePublisher) and pub.name == "native"
    ent = EntityDef("sensor", "state_battery_soc", "Battery", {"device_class": "battery", "unit_of_measurement": "%",
                                                                "entity_category": "diagnostic"})
    pub.discover(ent, "1.0")
    assert native.defined == [("sensor", "state_battery_soc", "Battery", {
        "device_class": "battery", "unit_of_measurement": "%", "entity_category": "diagnostic"})]
    pub.publish("state_battery_soc", 84.0, {"zeros": [0, 1], "off": False})
    assert writes[-1] == ("sensor.pe_state_battery_soc",
                          {"state": "84.0", "attributes": {"zeros": [0, 1], "off": False}})
    pub.available(False)
    pub.retire(ent)
    assert native.online_calls == [False] and native.retired == ["sensor.pe_state_battery_soc"]


def test_the_native_publisher_writes_plain_states_for_an_entity_the_integration_would_not_make():
    from pe_core.adapters.publish import select_publisher
    from pe_core.entities import EntityDef
    native, writes, told = FakeNative(), [], []
    native.fail = True
    pub = select_publisher(lambda eid, **kw: writes.append((eid, kw)), native=native,
                           told_once=lambda key, msg, level="INFO": told.append((key, level)))
    pub.discover(EntityDef("sensor", "x", "X", {"icon": "mdi:a"}), "1.0")
    assert told == [("native_define", "WARNING")]
    assert writes[-1][1]["attributes"]["friendly_name"] == "PowerEngine X"        # the whole plain entity this time
