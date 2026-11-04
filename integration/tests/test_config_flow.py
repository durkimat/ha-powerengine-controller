"""Setting up from the UI: one entry, the settings folder asked for with the app's own folder filled in."""

from custom_components.powerengine.const import CONF_SETTINGS_DIR, DOMAIN
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType


async def test_the_user_flow_asks_for_the_settings_folder_and_makes_one_entry(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    default = next(iter(result["data_schema"].schema)).default()
    assert default.endswith("/powerengine")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_SETTINGS_DIR: "/data/pe"})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "PowerEngine"
    assert result["options"] == {CONF_SETTINGS_DIR: "/data/pe"}
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_remove(entry.entry_id)


async def test_a_second_entry_is_refused(hass):
    first = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await hass.config_entries.flow.async_configure(first["flow_id"], {CONF_SETTINGS_DIR: "/data/pe"})
    await hass.async_block_till_done()
    second = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert second["type"] is FlowResultType.ABORT and second["reason"] == "single_instance_allowed"
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_remove(entry.entry_id)
