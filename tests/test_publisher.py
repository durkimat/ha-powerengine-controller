"""The publisher adapter (demo plan, step B1): MQTT exactly as before, or direct through AppDaemon."""
import json
import sys
import types
from unittest import mock

import pytest

from pe_core.adapters.publish import DirectPublisher, MqttPublisher, StatePublisher, select_publisher
from pe_core.config import ConfigError, parse_config, settings_catalogue
from pe_core.entities import (
    AVAILABILITY_TOPIC,
    ENTITIES,
    discovery_payload,
    entity_removal_messages,
    removal_messages,
    solar_plant_entity,
)

VERSION = "0.9.99"


class FakeMqtt:
    def __init__(self):
        self.sent = []

    def mqtt_publish(self, topic, payload, qos=None, retain=None):
        self.sent.append((topic, payload, qos, retain))


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

def test_direct_entity_id_equals_the_one_mqtt_discovery_asks_for():
    dynamic = [solar_plant_entity("main", "Main"), solar_plant_entity("shed_2", "Shed")]
    for e in list(ENTITIES) + dynamic:
        ha = FakeHA()
        DirectPublisher(ha.set_state).discover(e, VERSION)
        assert ha.calls[0][0] == discovery_payload(e, VERSION)["default_entity_id"] == e.entity_id
        assert e.entity_id == f"{e.component}.pe_{e.key}"


def test_publishers_satisfy_the_protocol():
    assert isinstance(MqttPublisher(FakeMqtt()), StatePublisher)
    assert isinstance(DirectPublisher(FakeHA().set_state), StatePublisher)


# --- direct -----------------------------------------------------------------------------------

def test_direct_sensor_gets_discovery_attributes_and_json_attributes():
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
    d.publish("diag_battery_temperature", 13)              # attributes stay until replaced, as on MQTT
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
    d.discover(ent("ui_history_day"), VERSION)
    d.preset("ui_history_day", "Yesterday")
    eid, state, attrs = ha.last("select.pe_ui_history_day")
    assert state == "Yesterday" and attrs["options"] == ent("ui_history_day").options["options"]
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


# --- mqtt: exactly what the app used to send -----------------------------------------------------

def test_mqtt_discovery_state_attributes_and_retirement_match_the_old_functions():
    api = FakeMqtt()
    m = MqttPublisher(api)
    e = ent("diag_version")
    m.discover(e, VERSION)
    m.publish("diag_version", VERSION)
    m.publish("diag_version", 5, {"names": {"a": "£"}})
    m.publish("diag_version", 6, '{"x": 1}')
    m.retire(ent("ctl_pause"))
    m.retire_all()
    m.available(True)
    m.available(False)
    old = [(e.discovery_topic, json.dumps(discovery_payload(e, VERSION))),
           ("powerengine/diag_version/state", VERSION),
           ("powerengine/diag_version/state", "5"),
           ("powerengine/diag_version/attributes", json.dumps({"names": {"a": "£"}}, default=str)),
           ("powerengine/diag_version/state", "6"),
           ("powerengine/diag_version/attributes", '{"x": 1}')]
    old += entity_removal_messages(ent("ctl_pause")) + removal_messages()
    old += [(AVAILABILITY_TOPIC, "online"), (AVAILABILITY_TOPIC, "offline")]
    assert [(t, p) for t, p, *_ in api.sent] == old
    assert all(q == 1 and r is True for *_, q, r in api.sent)


def test_mqtt_presets_go_to_the_retained_command_topics():
    api = FakeMqtt()
    m = MqttPublisher(api)
    m.preset("ctl_pause", "ON")
    m.preset("ui_right_align", "ON")
    m.preset("ui_history_day", "Yesterday")
    m.preset("ui_history_plan", "Start of day")
    assert [(t, p) for t, p, *_ in api.sent] == [
        ("powerengine/ctl_pause/set", "ON"), ("powerengine/ui_right_align/set", "ON"),
        ("powerengine/ui_history_day/set", "Yesterday"), ("powerengine/ui_history_plan/set", "Start of day")]


# --- choosing one --------------------------------------------------------------------------------

def test_auto_picks_mqtt_when_the_plugin_is_there_else_direct():
    ha = FakeHA()
    assert isinstance(select_publisher("auto", FakeMqtt(), ha.set_state), MqttPublisher)
    assert isinstance(select_publisher("auto", None, ha.set_state), DirectPublisher)
    assert isinstance(select_publisher("direct", FakeMqtt(), ha.set_state), DirectPublisher)
    assert isinstance(select_publisher("mqtt", FakeMqtt(), ha.set_state), MqttPublisher)
    assert select_publisher("mqtt", None, ha.set_state) is None      # asked for MQTT, none there: nothing to publish to


def test_publisher_setting_parses_defaults_to_auto_and_rejects_others():
    assert parse_config({}).system["publisher"] == "auto"
    assert parse_config({"system": {"publisher": "direct"}}).system["publisher"] == "direct"
    with pytest.raises(ConfigError):
        parse_config({"system": {"publisher": "carrier-pigeon"}})
    entry = next(s for s in settings_catalogue()["system"] if s["key"] == "publisher")
    assert entry["label"] == "Entity publishing" and entry["default"] == "auto"
    assert [o[0] for o in entry["options"]] == ["auto", "mqtt", "direct"]


def _engine():
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")

    class Hass:
        def __getattr__(self, name):
            raise AttributeError(name)

        def log(self, *a, **k):
            pass
    hassapi.Hass = Hass
    mods = {n: types.ModuleType(n) for n in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass")}
    mods["appdaemon.plugins.hass.hassapi"] = hassapi
    return mods


def test_the_app_builds_the_publisher_from_the_setting_and_the_plugin():
    with mock.patch.dict(sys.modules, _engine()):
        sys.modules.pop("powerengine", None)
        import powerengine
        e = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
        e.cfg = None
        e.mqtt = FakeMqtt()
        assert isinstance(e._get_publisher(), MqttPublisher)
        e = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
        e.cfg, e.mqtt, e.set_state = None, None, FakeHA().set_state
        assert isinstance(e._get_publisher(), DirectPublisher)
        e = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
        e.cfg, e.mqtt = types.SimpleNamespace(system={"publisher": "direct"}), FakeMqtt()
        e.set_state = FakeHA().set_state
        assert isinstance(e._get_publisher(), DirectPublisher)
        sys.modules.pop("powerengine", None)
