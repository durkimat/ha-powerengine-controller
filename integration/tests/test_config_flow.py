"""Setting up from the UI: one entry, the settings folder asked for with the app's own folder filled in."""

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.powerengine.const import CONF_MODE, CONF_SETTINGS_DIR, DOMAIN, MODE_LIVE, MODE_SHADOW


async def test_the_user_flow_asks_for_the_mode_and_folder_and_makes_one_entry(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults == {CONF_MODE: MODE_LIVE, CONF_SETTINGS_DIR: ""}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODE: MODE_SHADOW, CONF_SETTINGS_DIR: "/data/pe"})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "PowerEngine"
    assert result["options"] == {CONF_MODE: MODE_SHADOW, CONF_SETTINGS_DIR: "/data/pe"}
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_remove(entry.entry_id)


async def test_an_empty_folder_means_the_usual_one_and_live_is_the_default(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_MODE: MODE_LIVE})
    assert result["options"] == {CONF_MODE: MODE_LIVE}
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_remove(entry.entry_id)


async def test_a_second_entry_is_refused(hass):
    first = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await hass.config_entries.flow.async_configure(first["flow_id"], {CONF_MODE: MODE_LIVE})
    await hass.async_block_till_done()
    second = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert second["type"] is FlowResultType.ABORT and second["reason"] == "single_instance_allowed"
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_remove(entry.entry_id)
