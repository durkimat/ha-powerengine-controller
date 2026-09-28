"""Discovery and retirement of the per-plant solar power sensors (issue #160)."""
import sys
import types

import pytest


@pytest.fixture
def engine(monkeypatch):
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")

    class Hass:
        def __getattr__(self, name):
            raise AttributeError(name)

        def log(self, *a, **k):
            pass

    hassapi.Hass = Hass
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "appdaemon.plugins.hass.hassapi", hassapi)
    sys.modules.pop("powerengine", None)
    import powerengine
    e = powerengine.PowerEngine.__new__(powerengine.PowerEngine)
    return e, powerengine


class _FakeMqtt:
    def __init__(self):
        self.published = {}

    def mqtt_publish(self, topic, payload, qos=1, retain=True):
        self.published[topic] = payload


def _cfg(plants):
    from pe_core.config import SolarPlant
    return types.SimpleNamespace(solar_plants=tuple(
        SolarPlant(id=pid, name=name, power={}, energy_today={}, enabled=enabled)
        for pid, name, enabled in plants
    ))


def test_sync_solar_entities_discovers_enabled_plants(engine):
    e, powerengine = engine
    e.cfg = _cfg([("main", "Main", True), ("shed", "Shed", True), ("off", "Off", False)])
    e.mqtt = _FakeMqtt()
    e.get_state = lambda *a, **k: {}
    e._sync_solar_entities()
    topics = e.mqtt.published
    assert any("state_solar_main_power" in t for t in topics)
    assert any("state_solar_shed_power" in t for t in topics)
    assert not any("state_solar_off_power" in t for t in topics)


def test_sync_solar_entities_retires_removed_or_disabled_plants(engine):
    e, powerengine = engine
    e.mqtt = _FakeMqtt()
    # HA already knows about a "roof" plant sensor from a previous run, and the total (never retired).
    e.get_state = lambda *a, **k: {
        "sensor.pe_state_solar_roof_power": "123",
        "sensor.pe_state_solar_power": "456",
        "sensor.pe_state_battery_power": "1",
    }
    e.cfg = _cfg([("main", "Main", True)])
    e._sync_solar_entities()
    topics = e.mqtt.published
    assert any("state_solar_main_power" in t for t in topics)
    retired = [t for t in topics if "state_solar_roof_power" in t]
    assert retired and all(topics[t] == "" for t in retired)
    assert not any("state_solar_power/config" in t and topics[t] == "" for t in topics)  # total never touched


def test_sync_solar_entities_no_mqtt_is_a_noop(engine):
    e, powerengine = engine
    e.cfg = _cfg([("main", "Main", True)])
    e.mqtt = None
    e._sync_solar_entities()   # must not raise
