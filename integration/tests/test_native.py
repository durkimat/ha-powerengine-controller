"""The engine's own entities as real Home Assistant entities (native.py): in the registry with the unique ids the old
AppDaemon app's MQTT entities had, on one device, with values Home Assistant accepts, and a pause switch that
holds what it is given."""

import logging

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from test_bridge import engine_client  # noqa: F401  (a client standing in for the engine)
from test_setup import running, start

from custom_components.powerengine.const import DOMAIN


async def test_the_real_engines_entities_are_in_the_registry_with_the_ids_they_always_had_on_one_device(hass, tmp_path,
                                                                                                    caplog):
    caplog.set_level(logging.WARNING)
    entry = await start(hass, tmp_path)
    await running(hass)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    mine = [e for e in registry.entities.values()
            if e.platform == DOMAIN and e.entity_id.split(".")[1].startswith("pe_")]
    assert len(mine) == 83
    version = registry.async_get("sensor.pe_diag_version")
    assert version.unique_id == "powerengine_diag_version" and version.entity_category.value == "diagnostic"
    assert registry.async_get("switch.pe_ctl_pause").unique_id == "powerengine_ctl_pause"
    assert registry.async_get("binary_sensor.pe_diag_config_ok").unique_id == "powerengine_diag_config_ok"
    devices = {e.device_id for e in mine}
    assert len(devices) == 1
    device = dr.async_get(hass).async_get(devices.pop())
    assert device.name == "PowerEngine" and (DOMAIN, entry.entry_id) in device.identifiers
    assert hass.states.get("sensor.pe_diag_version").attributes["friendly_name"] == "PowerEngine Version"
    # nothing the engine wrote was rejected: no value Home Assistant would not take, no clash of ids
    bad = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING and "pe_" in r.getMessage()
           and "loader" not in r.name]
    assert bad == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_the_pause_switch_holds_what_it_is_given_and_the_engine_hears_it(hass, engine_client):  # noqa: F811
    ask, client = engine_client, engine_client.client
    await ask(client.native_entities.define, "switch", "ctl_pause", "Pause control", {"icon": "mdi:pause-octagon"})
    await hass.async_block_till_done()
    assert hass.states.get("switch.pe_ctl_pause").state == "unknown"
    await ask(client.set_state, "switch.pe_ctl_pause", state="unknown", attributes={})      # "new": it stays unknown
    heard = []
    await ask(client.listen_state, lambda *a: heard.append(a), "switch.pe_ctl_pause")
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.pe_ctl_pause"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("switch.pe_ctl_pause").state == "on"
    await ask(lambda: [__import__("time").sleep(0.2)])
    assert heard and heard[-1][3] == "on"                                       # the engine was told of the change
    await ask(client.set_state, "switch.pe_ctl_pause", state="off", attributes={})           # the engine's own preset
    await hass.async_block_till_done()
    assert hass.states.get("switch.pe_ctl_pause").state == "off"


async def test_values_home_assistant_would_reject_become_unknown_not_errors(hass, engine_client, caplog):  # noqa: F811
    caplog.set_level(logging.WARNING)
    ask, client = engine_client, engine_client.client
    native = client.native_entities
    await ask(native.define, "sensor", "t", "Time", {"device_class": "timestamp"})
    await ask(native.define, "sensor", "e", "Mode", {"device_class": "enum", "options": ["a", "b"]})
    await ask(native.define, "sensor", "n", "Power", {"device_class": "power", "unit_of_measurement": "W",
                                                       "state_class": "measurement"})
    await hass.async_block_till_done()
    for key, value in (("t", "not a time"), ("e", "zzz"), ("n", "lots")):
        await ask(client.set_state, f"sensor.pe_{key}", state=value, attributes={})
    await hass.async_block_till_done()
    assert [hass.states.get(f"sensor.pe_{k}").state for k in "ten"] == ["unknown"] * 3
    await ask(client.set_state, "sensor.pe_t", state="2026-11-04T12:00:00+00:00", attributes={"zero": 0, "off": False})
    await ask(client.set_state, "sensor.pe_e", state="b", attributes={})
    await ask(client.set_state, "sensor.pe_n", state="1234.5", attributes={})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pe_t").state == "2026-11-04T12:00:00+00:00"
    attributes = hass.states.get("sensor.pe_t").attributes
    assert attributes["zero"] == 0 and attributes["off"] is False
    assert hass.states.get("sensor.pe_e").state == "b" and hass.states.get("sensor.pe_n").state == "1234.5"
    assert sum("not a" in r.getMessage() or "not one of" in r.getMessage() for r in caplog.records) == 3


async def test_the_entities_go_unavailable_when_the_engine_says_it_is_down_and_come_back(hass, engine_client):  # noqa: F811
    ask, client = engine_client, engine_client.client
    await ask(client.native_entities.define, "sensor", "x", "X", {})
    await ask(client.set_state, "sensor.pe_x", state="5", attributes={})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pe_x").state == "5"
    await ask(client.native_entities.online, False)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pe_x").state == "unavailable"
    await ask(client.native_entities.online, True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pe_x").state == "5"


async def test_an_entity_the_engine_retires_is_removed_from_the_registry_and_one_it_changes_is_updated(hass, engine_client):  # noqa: F811, E501
    ask, client = engine_client, engine_client.client
    native = client.native_entities
    await ask(native.define, "sensor", "y", "Old name", {"icon": "mdi:a"})
    await ask(client.set_state, "sensor.pe_y", state="1", attributes={})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pe_y").attributes["icon"] == "mdi:a"
    await ask(native.define, "sensor", "y", "New name", {"icon": "mdi:b", "unit_of_measurement": "kW"})
    await hass.async_block_till_done()
    state = hass.states.get("sensor.pe_y")
    assert state.attributes["icon"] == "mdi:b" and state.attributes["unit_of_measurement"] == "kW"
    registry = er.async_get(hass)
    assert registry.async_get("sensor.pe_y") is not None
    await ask(native.retire, "sensor.pe_y")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.pe_y") is None and registry.async_get("sensor.pe_y") is None


async def test_an_entity_this_integration_cannot_make_gets_an_error_so_the_engine_can_write_a_plain_state(hass, engine_client):  # noqa: F811, E501
    import pytest

    from pe_core.host_socket import HostError
    ask, client = engine_client, engine_client.client
    with pytest.raises(HostError, match="no native camera entities"):
        await ask(client.native_entities.define, "camera", "z", "Z", {})
    await ask(client.set_state, "camera.pe_z", state="idle", attributes={"a": 1})            # written as a plain state
    assert hass.states.get("camera.pe_z").state == "idle"
