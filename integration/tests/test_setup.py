"""The integration set up in Home Assistant with the real engine as its process."""

import asyncio
import shutil
from datetime import timedelta

from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

import custom_components.powerengine as integration
from custom_components.powerengine import const
from custom_components.powerengine.const import DOMAIN


async def entry_for(hass, tmp_path, **options):
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={const.CONF_SETTINGS_DIR: str(tmp_path), **options})
    entry.add_to_hass(hass)
    return entry


async def wait_for(hass, condition, timeout=40.0, what="condition"):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if condition():
            return
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
    raise AssertionError(f"timed out waiting for {what}")


def engine(hass):
    """The engine's status sensor (a shadow run's device is named for it, so its id is different)."""
    return hass.states.get("sensor.powerengine_engine") or hass.states.get("sensor.powerengine_shadow_engine")


async def start(hass, tmp_path, **options):
    entry = await entry_for(hass, tmp_path, **options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def running(hass, version_entity="sensor.pe_diag_version"):
    await wait_for(hass, lambda: engine(hass) and engine(hass).state == "running", what="the engine to run")
    await wait_for(hass, lambda: hass.states.get(version_entity) is not None
                   and hass.states.get(version_entity).state not in ("unknown", "unavailable"),
                   what="the engine's entities")


async def test_the_engine_starts_publishes_its_entities_and_stops_with_the_entry(hass, tmp_path):
    entry = await start(hass, tmp_path)
    runtime = entry.runtime_data
    await running(hass)
    version = hass.states.get("sensor.pe_diag_version").state
    assert version == engine(hass).attributes["installed_version"] == engine(hass).attributes["running_version"]
    assert len([s for s in hass.states.async_all("sensor") if s.entity_id.startswith("sensor.pe_")]) > 40
    assert not ir.async_get(hass).issues
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert runtime.supervisor._process is None or runtime.supervisor._process.returncode is not None


async def test_an_update_that_replaces_the_engine_files_restarts_only_the_engine(hass, tmp_path, monkeypatch):
    copy = tmp_path / "engine"
    shutil.copytree(integration.ENGINE_DIR, copy)
    monkeypatch.setattr(integration, "ENGINE_DIR", copy)
    entry = await start(hass, tmp_path / "settings")
    runtime = entry.runtime_data
    await running(hass)
    first = hass.states.get("sensor.pe_diag_version").state
    pid = runtime.supervisor._process.pid
    init = copy / "pe_core" / "__init__.py"                   # what a HACS update does: new files, same Home Assistant
    init.write_text(init.read_text().replace(f'"{first}"', '"9.9.9"'))
    assert engine(hass).attributes["update_pending"] is False        # (not yet seen)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await wait_for(hass, lambda: hass.states.get("sensor.pe_diag_version").state == "9.9.9", what="the new engine")
    await wait_for(hass, lambda: engine(hass).state == "running" and engine(hass).attributes["restarts"] >= 1)
    assert runtime.supervisor._process.pid != pid
    assert engine(hass).attributes["running_version"] == engine(hass).attributes["installed_version"] == "9.9.9"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_the_restart_button_and_service_restart_the_engine(hass, tmp_path):
    entry = await start(hass, tmp_path)
    runtime = entry.runtime_data
    await running(hass)
    pid = runtime.supervisor._process.pid
    await hass.services.async_call("button", "press", {"entity_id": "button.powerengine_restart_engine"}, blocking=True)
    await wait_for(hass, lambda: runtime.supervisor._process and runtime.supervisor._process.pid != pid
                   and engine(hass).state == "running", what="the engine to come back")
    pid = runtime.supervisor._process.pid
    await hass.services.async_call(DOMAIN, const.SERVICE_RESTART_ENGINE, {}, blocking=True)
    await wait_for(hass, lambda: runtime.supervisor._process and runtime.supervisor._process.pid != pid
                   and engine(hass).state == "running", what="the engine to come back again")
    await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics_hold_the_status_and_the_engines_last_lines_but_no_secret(hass, tmp_path):
    from custom_components.powerengine.diagnostics import async_get_config_entry_diagnostics
    entry = await start(hass, tmp_path)
    await running(hass)
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["status"] == "running" and any("starting" in line for line in data["engine_log"])
    secret_text = str(data)
    assert "PE_SECRET" not in secret_text and entry.runtime_data.server._secret not in secret_text
    await hass.config_entries.async_unload(entry.entry_id)
