"""Home Assistant as the engine sees it: the engine's own client (pe_core/host_socket.py) connected to the integration's
server, asking for states, calling services, hearing changes and events."""

import asyncio
import threading
import time

import pytest
from homeassistant.core import Context
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.powerengine import const
from custom_components.powerengine.const import DOMAIN
from custom_components.powerengine.supervisor import EngineSupervisor
from pe_core.host_socket import HostError, SocketHost  # noqa: E402


class Calls:
    def __init__(self):
        self.calls, self.arrived = [], threading.Event()

    def __call__(self, *args):
        self.calls.append(args)
        self.arrived.set()


@pytest.fixture
async def engine_client(hass, tmp_path, monkeypatch):
    """The integration set up with no engine of its own (the supervisor does not start one), and a client that stands
    in for the engine."""
    monkeypatch.setattr(EngineSupervisor, "start", lambda self: None)
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={const.CONF_SETTINGS_DIR: str(tmp_path)})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    server = entry.runtime_data.server
    client = SocketHost(server.address, server._secret, engine_version="9.9.9", retry=(0.05,))
    await hass.async_add_executor_job(client.start, 10)

    async def ask(fn, *args, **kwargs):
        return await hass.async_add_executor_job(lambda: fn(*args, **kwargs))
    ask.client = client
    yield ask
    await hass.async_add_executor_job(client.stop)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_the_engine_reads_states_attributes_and_whole_domains(hass, engine_client):
    hass.states.async_set("sensor.a", "12.5", {"unit_of_measurement": "kW", "zero": 0, "off": False})
    hass.states.async_set("person.me", "home", {"user_id": "u1"})
    ask, client = engine_client, engine_client.client
    assert await ask(client.get_state, "sensor.a") == "12.5"
    assert await ask(client.get_state, "sensor.a", attribute="zero") == 0
    assert await ask(client.get_state, "sensor.a", attribute="off") is False
    assert await ask(client.get_state, "sensor.nope", default="d") == "d"
    full = await ask(client.get_state, "sensor.a", attribute="all")
    assert full["entity_id"] == "sensor.a" and full["attributes"]["unit_of_measurement"] == "kW"
    assert "last_changed" in full
    people = await ask(client.get_state, "person")
    assert list(people) == ["person.me"] and people["person.me"]["attributes"]["user_id"] == "u1"
    assert {"sensor.a", "person.me"} <= set(await ask(client.get_state))


async def test_a_change_in_home_assistant_reaches_the_engines_copy_and_its_listener(hass, engine_client):
    ask, client = engine_client, engine_client.client
    hass.states.async_set("switch.pause", "off")
    assert await ask(client.get_state, "switch.pause") == "off"
    heard = Calls()
    await ask(client.listen_state, heard, "switch.pause")
    await hass.async_block_till_done()
    hass.states.async_set("switch.pause", "on")
    assert await ask(heard.arrived.wait, 10)
    assert heard.calls == [("switch.pause", "state", "off", "on", {})]
    assert await ask(client.get_state, "switch.pause") == "on"
    hass.states.async_remove("switch.pause")
    await hass.async_block_till_done()
    await ask(time.sleep, 0.2)
    assert await ask(client.get_state, "switch.pause") is None


async def test_an_event_reaches_the_engine_with_who_caused_it(hass, engine_client):
    ask, client = engine_client, engine_client.client
    heard = Calls()
    await ask(client.listen_event, heard, "pe_config_save")
    await hass.async_block_till_done()
    hass.bus.async_fire("pe_other", {"x": 1})
    hass.bus.async_fire("pe_config_save", {"settings": {"a": [1, 2]}}, context=Context(user_id="user-1"))
    assert await ask(heard.arrived.wait, 10)
    event, data, _ = heard.calls[0]
    assert event == "pe_config_save" and data["settings"] == {"a": [1, 2]}
    assert data["metadata"]["context"]["user_id"] == "user-1"
    assert len(heard.calls) == 1


async def test_the_engine_calls_services_and_waits_for_them_and_hears_when_they_fail(hass, engine_client):
    ask, client = engine_client, engine_client.client
    seen = []

    async def set_value(call):
        await asyncio.sleep(0.05)
        seen.append((call.data["entity_id"], call.data["value"]))

    async def broken(call):
        raise RuntimeError("the inverter did not answer")
    hass.services.async_register("number", "set_value", set_value)
    hass.services.async_register("select", "select_option", broken)
    await ask(client.call_service, "number/set_value", entity_id="number.charge", value=5000)
    assert seen == [("number.charge", 5000)]                       # it had finished when the call returned
    with pytest.raises(HostError, match="did not answer"):
        await ask(client.call_service, "select/select_option", entity_id="select.mode", option="Force charge")
    with pytest.raises(HostError):
        await ask(client.call_service, "no_such_domain/nothing", x=1)


async def test_the_engine_fires_events_renders_templates_and_writes_states(hass, engine_client):
    ask, client = engine_client, engine_client.client
    events = async_capture_events(hass, "pe_config_result")
    await ask(client.fire_event, "pe_config_result", ok=True, message="Saved.")
    await hass.async_block_till_done()
    assert [(e.data["ok"], e.data["message"]) for e in events] == [(True, "Saved.")]
    hass.states.async_set("sensor.t", "21.5")
    assert await ask(client.render_template, "{{ states('sensor.t') | float + 1 }}") == "22.5"
    await ask(client.state_writer(lambda *a: None), "sensor.pe_x", state="on", attributes={"zero": 0, "off": False})
    state = hass.states.get("sensor.pe_x")
    assert state.state == "on" and state.attributes["zero"] == 0 and state.attributes["off"] is False


async def test_the_engine_reads_a_days_history_from_the_recorder(recorder_mock, hass, engine_client):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done
    ask, client = engine_client, engine_client.client
    start = dt_util.utcnow() - timedelta(hours=1)
    hass.states.async_set("sensor.h", "1")
    hass.states.async_set("sensor.h", "2")
    await async_wait_recording_done(hass)
    rows = await ask(client.get_history, entity_id="sensor.h", start_time=start, end_time=dt_util.utcnow(),
                     minimal_response=True, no_attributes=True)
    assert [r["state"] for r in rows[0]] == ["1", "2"] and "last_changed" in rows[0][0]
