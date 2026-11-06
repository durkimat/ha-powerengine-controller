"""PowerEngine's dashboard is registered by the integration, with nothing added to configuration.yaml (dashboard.py)."""

import pytest
from homeassistant.components import frontend as ha_frontend
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.components.lovelace import LOVELACE_DATA
from homeassistant.components.lovelace.const import ConfigNotFound
from homeassistant.components.lovelace.dashboard import LovelaceYAML
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.powerengine import const, dashboard
from custom_components.powerengine.const import DOMAIN
from custom_components.powerengine.supervisor import EngineSupervisor


async def setup(hass, tmp_path, monkeypatch, lovelace_config=None, **options):
    monkeypatch.setattr(EngineSupervisor, "start", lambda self: None)
    hass.data[DATA_EXTRA_MODULE_URL] = set()
    assert await async_setup_component(hass, "lovelace", lovelace_config or {})
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={const.CONF_SETTINGS_DIR: str(tmp_path), **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def panels(hass):
    return hass.data.get(ha_frontend.DATA_PANELS, {})


async def test_the_dashboard_is_in_the_sidebar_and_reads_the_engines_file(hass, tmp_path, monkeypatch):
    entry = await setup(hass, tmp_path, monkeypatch)
    ours = hass.data[LOVELACE_DATA].dashboards[dashboard.URL_PATH]
    panel = panels(hass)[dashboard.URL_PATH]
    assert isinstance(ours, LovelaceYAML) and ours.path == str(tmp_path / "dashboard.yaml")
    assert panel.component_name == "lovelace" and panel.sidebar_title == "Power Engine" and not panel.require_admin
    assert panel.sidebar_icon == "mdi:lightning-bolt" and panel.config == {"mode": "yaml"}
    with pytest.raises(ConfigNotFound):                          # the engine has not written it yet
        await ours.async_load(False)
    (tmp_path / "dashboard.yaml").write_text("views:\n  - title: Monitoring\n    path: monitoring\n")
    assert (await ours.async_load(True))["views"][0]["path"] == "monitoring"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_unloading_takes_it_away_and_loading_again_brings_it_back(hass, tmp_path, monkeypatch):
    entry = await setup(hass, tmp_path, monkeypatch)
    await hass.config_entries.async_unload(entry.entry_id)
    assert dashboard.URL_PATH not in hass.data[LOVELACE_DATA].dashboards and dashboard.URL_PATH not in panels(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert dashboard.URL_PATH in hass.data[LOVELACE_DATA].dashboards and dashboard.URL_PATH in panels(hass)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_an_entry_in_configuration_yaml_comes_first_and_stays(hass, tmp_path, monkeypatch):
    mine = {"lovelace": {"dashboards": {dashboard.URL_PATH: {
        "mode": "yaml", "title": "Power Engine", "icon": "mdi:lightning-bolt", "show_in_sidebar": True,
        "filename": "powerengine/dashboard.yaml"}}}}
    entry = await setup(hass, tmp_path, monkeypatch, lovelace_config=mine)
    existing = hass.data[LOVELACE_DATA].dashboards[dashboard.URL_PATH]
    assert existing.path.endswith("powerengine/dashboard.yaml")          # theirs, not the settings folder's
    await hass.config_entries.async_unload(entry.entry_id)
    assert hass.data[LOVELACE_DATA].dashboards[dashboard.URL_PATH] is existing and dashboard.URL_PATH in panels(hass)


async def test_without_lovelace_nothing_breaks_and_it_is_added_when_lovelace_arrives(hass, tmp_path, monkeypatch):
    monkeypatch.setattr(EngineSupervisor, "start", lambda self: None)
    hass.data[DATA_EXTRA_MODULE_URL] = set()
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={const.CONF_SETTINGS_DIR: str(tmp_path)})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert dashboard.URL_PATH not in panels(hass)
    assert await async_setup_component(hass, "lovelace", {})
    await entry.runtime_data.check()                                      # the once-a-minute check
    assert dashboard.URL_PATH in hass.data[LOVELACE_DATA].dashboards and dashboard.URL_PATH in panels(hass)
    await hass.config_entries.async_unload(entry.entry_id)
