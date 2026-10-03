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


def test_retired_entities_are_cleared_from_mqtt_and_marked_in_direct_mode(engine, tmp_path):
    """The Costs custom-range selects (0.9.72-0.9.74) no longer exist: every start clears them."""
    from pe_core.entities import RETIRED_ENTITIES
    assert {e.entity_id for e in RETIRED_ENTITIES} == {"select.pe_ui_cost_from", "select.pe_ui_cost_to",
                                                   "select.pe_ui_history_plan"}
    e, powerengine = engine
    e.cfg = None
    e.mqtt = _FakeMqtt()
    e.get_state = lambda *a, **k: None
    e._retire_old_entities()
    for key in ("ui_cost_from", "ui_cost_to"):
        cleared = [t for t in e.mqtt.published if key in t]
        assert len(cleared) == 4 and all(e.mqtt.published[t] == "" for t in cleared)   # discovery, state, attrs, set

    from pe_core.adapters.publish import DirectPublisher
    calls = []
    direct = DirectPublisher(lambda eid, **kw: calls.append((eid, kw.get("state"))))
    e._publisher_obj = direct
    e.get_state = lambda eid=None, **k: "5 days ago" if eid == "select.pe_ui_cost_from" else None
    e._retire_old_entities()                   # only the one HA still has (no stray entity on a fresh install)
    assert calls == [("select.pe_ui_cost_from", "unavailable")]


def test_ui_defaults_ignore_keys_an_older_release_saved(engine, tmp_path):
    import json
    e, powerengine = engine
    e.cfg = None
    e.mqtt = _FakeMqtt()
    e._save_path = lambda: str(tmp_path / "config.yaml")
    (tmp_path / "ui.json").write_text(json.dumps({"right_align": True, "history": True, "cost_range": True}))
    e._ui_defaults()
    assert e.mqtt.published == {}
    assert json.loads((tmp_path / "ui.json").read_text())["cost_range"] is True     # left alone, nothing reads it
