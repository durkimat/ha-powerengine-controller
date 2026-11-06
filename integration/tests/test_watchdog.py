"""The integration's watchdog and its update service (watchdog.py, updater.py), which replace the YAML package."""

from datetime import timedelta

import pytest
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.powerengine import const, updater
from custom_components.powerengine.const import DOMAIN
from custom_components.powerengine.supervisor import EngineSupervisor
from custom_components.powerengine.watchdog import QUIET_FOR, Watchdog

HEARTBEAT = "sensor.pe_diag_heartbeat"
WINDOW = "number.solis_timed_charge_start_hours"
OPEN_WINDOW = "number.solis_timed_discharge_end_hours"
BUTTON = "button.solis_update_charge_discharge_times"
SELECT = "select.solis_energy_storage_control_switch"
CALLS = [
    {"service": "number/set_value", "data": {"entity_id": WINDOW, "value": 0}},
    {"service": "number/set_value", "data": {"entity_id": OPEN_WINDOW, "value": 0}},
    {"service": "select/select_option", "data": {"entity_id": SELECT, "option": "Self-Use"}},
    {"wait_s": 5},
    {"service": "button/press", "data": {"entity_id": BUTTON}},
]


async def setup(hass, tmp_path, monkeypatch, **options):
    monkeypatch.setattr(EngineSupervisor, "start", lambda self: None)
    hass.data[DATA_EXTRA_MODULE_URL] = set()
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={const.CONF_SETTINGS_DIR: str(tmp_path), **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def inverter(hass):
    """Fake inverter services that record what they are asked."""
    return (async_mock_service(hass, "number", "set_value"), async_mock_service(hass, "select", "select_option"),
            async_mock_service(hass, "button", "press"))


async def quiet_for(hass, wd, minutes):
    wd.last_alive = dt_util.utcnow() - timedelta(minutes=minutes)
    await wd.check()


# --- the watchdog ---------------------------------------------------------------------------------------

async def test_a_quiet_engine_while_in_control_gets_the_inverter_handed_back_once(hass, monkeypatch):
    numbers, selects, buttons = inverter(hass)
    monkeypatch.setattr("custom_components.powerengine.watchdog.asyncio.sleep", _no_sleep)
    hass.states.async_set(WINDOW, "0")                                  # already closed: no call needed
    hass.states.async_set(OPEN_WINDOW, "18")
    wd = Watchdog(hass, HEARTBEAT)
    wd.set(CALLS, True)
    await quiet_for(hass, wd, 20)
    assert [c.data["entity_id"] for c in numbers] == [OPEN_WINDOW] and numbers[0].data["value"] == 0
    assert selects[0].data["option"] == "Self-Use" and buttons[0].data["entity_id"] == BUTTON
    assert any("stopped updating" in n["message"] for n in hass.data["persistent_notification"].values())
    await wd.check()
    assert len(numbers) == 1 and len(buttons) == 1                      # once per silence


async def test_it_does_nothing_while_the_engine_is_alive_or_not_in_control_or_not_yet_quiet_enough(hass, monkeypatch):
    numbers, selects, buttons = inverter(hass)
    monkeypatch.setattr("custom_components.powerengine.watchdog.asyncio.sleep", _no_sleep)
    wd = Watchdog(hass, HEARTBEAT)
    wd.set(CALLS, True)
    hass.states.async_set(HEARTBEAT, dt_util.utcnow().isoformat())     # a fresh heartbeat
    await quiet_for(hass, wd, 60)
    assert not selects
    hass.states.async_set(HEARTBEAT, "unavailable")
    await quiet_for(hass, wd, QUIET_FOR.total_seconds() / 60 - 1)       # not quiet for long enough yet
    assert not selects
    wd.set(CALLS, False)                                                # PowerEngine is not in control
    await quiet_for(hass, wd, 60)
    assert not selects
    wd.set([], True)                                                    # nothing to do
    await quiet_for(hass, wd, 60)
    assert not selects and not numbers and not buttons


async def test_it_acts_again_after_the_engine_has_been_back(hass, monkeypatch):
    _, selects, _ = inverter(hass)
    monkeypatch.setattr("custom_components.powerengine.watchdog.asyncio.sleep", _no_sleep)
    wd = Watchdog(hass, HEARTBEAT)
    wd.set(CALLS, True)
    await quiet_for(hass, wd, 20)
    assert len(selects) == 1
    hass.states.async_set(HEARTBEAT, dt_util.utcnow().isoformat())
    await wd.check()
    assert wd.acted is False
    hass.states.async_set(HEARTBEAT, "unavailable")
    await quiet_for(hass, wd, 20)
    assert len(selects) == 2


async def test_one_call_failing_does_not_stop_the_rest(hass, monkeypatch):
    _, selects, buttons = inverter(hass)
    monkeypatch.setattr("custom_components.powerengine.watchdog.asyncio.sleep", _no_sleep)

    @callback
    def refuse(call):
        raise RuntimeError("the inverter is not answering")
    hass.services.async_register("number", "set_value", refuse)         # (replaces the recorder)
    wd = Watchdog(hass, HEARTBEAT)
    wd.set(CALLS, True)
    await quiet_for(hass, wd, 20)
    assert selects and buttons
    assert any("2 failed" in n["message"] for n in hass.data["persistent_notification"].values())


async def test_what_the_engine_said_survives_a_restart_of_home_assistant(hass):
    wd = Watchdog(hass, HEARTBEAT)
    wd.set(CALLS, True)
    await wd._store.async_save({"calls": wd.calls, "armed": wd.armed})
    again = Watchdog(hass, HEARTBEAT)
    await again.async_load()
    assert again.calls == CALLS and again.armed is True
    assert again.last_alive >= wd.last_alive - timedelta(seconds=5)     # a start counts as alive: 15 minutes' grace


async def test_the_engines_failsafe_reaches_the_watchdog(hass, tmp_path, monkeypatch):
    entry = await setup(hass, tmp_path, monkeypatch)
    await hass.async_add_executor_job(entry.runtime_data.home.set_failsafe, CALLS, True)
    assert entry.runtime_data.watchdog.calls == CALLS and entry.runtime_data.watchdog.armed is True
    await hass.config_entries.async_unload(entry.entry_id)


async def _no_sleep(_seconds):
    return None


# --- the update service ----------------------------------------------------------------------------------

def hacs_update(hass, entity_id="update.powerengine_update", state="on", installed="v0.9.1", latest="v0.9.2"):
    er.async_get(hass).async_get_or_create("update", "hacs", entity_id, suggested_object_id=entity_id.split(".")[1])
    hass.states.async_set(entity_id, state, {"installed_version": installed, "latest_version": latest})


async def test_the_update_service_installs_what_hacs_found_and_reports(hass, tmp_path, monkeypatch):
    entry = await setup(hass, tmp_path, monkeypatch)
    hacs_update(hass)
    hacs_update(hass, "update.powerengine_card_update")                 # a retired separate card: never touched
    looked = async_mock_service(hass, "homeassistant", "update_entity")
    installed = []

    @callback
    def install(call):
        installed.append(call.data["entity_id"])
        hass.states.async_set("update.powerengine_update", "off", {"installed_version": "v0.9.2",
                                                                    "latest_version": "v0.9.2"})
    hass.services.async_register("update", "install", install)
    monkeypatch.setattr(EngineSupervisor, "running", property(lambda self: "0.9.2"))
    message = await updater.async_update(hass, entry.runtime_data, settle_s=0, running_wait_s=0)
    assert looked[0].data["entity_id"] == ["update.powerengine_update"]
    assert installed == [["update.powerengine_update"]]
    assert "0.9.2 is running" in message and "Installed the latest release" in message
    assert hass.services.has_service(DOMAIN, "update")
    await hass.config_entries.async_unload(entry.entry_id)
    assert not hass.services.has_service(DOMAIN, "update")


async def test_nothing_new_means_nothing_installed(hass, tmp_path, monkeypatch):
    entry = await setup(hass, tmp_path, monkeypatch)
    hacs_update(hass, state="off", installed="v0.9.2", latest="v0.9.2")
    async_mock_service(hass, "homeassistant", "update_entity")
    installs = async_mock_service(hass, "update", "install")
    monkeypatch.setattr(EngineSupervisor, "running", property(lambda self: "0.9.2"))
    message = await updater.async_update(hass, entry.runtime_data, settle_s=0, running_wait_s=0)
    assert not installs and "Nothing new to install" in message
    await hass.config_entries.async_unload(entry.entry_id)


async def test_without_a_hacs_update_entity_the_service_says_so(hass, tmp_path, monkeypatch):
    entry = await setup(hass, tmp_path, monkeypatch)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "update", {}, blocking=True)
    assert any("No HACS update entity" in n["message"] for n in hass.data["persistent_notification"].values())
    await hass.config_entries.async_unload(entry.entry_id)
