"""Setting up from the UI: one click, one entry; the settings folder is an option for whoever wants another."""

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.powerengine.const import CONF_SETTINGS_DIR, DOMAIN


async def remove_entries(hass):
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_remove(entry.entry_id)


async def test_the_user_flow_asks_nothing_and_makes_one_entry(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user" and not result["data_schema"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "PowerEngine"
    assert result["options"] == {}                                           # the usual settings folder
    await remove_entries(hass)


async def test_a_second_entry_is_refused(hass):
    first = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await hass.config_entries.flow.async_configure(first["flow_id"], {})
    await hass.async_block_till_done()
    second = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert second["type"] is FlowResultType.ABORT and second["reason"] == "single_instance_allowed"
    await remove_entries(hass)


async def test_the_settings_folder_can_be_changed_in_the_options(hass):
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done()
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SETTINGS_DIR: " /data/pe "})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"] == {CONF_SETTINGS_DIR: "/data/pe"}
    await remove_entries(hass)
