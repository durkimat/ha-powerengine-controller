"""Setting PowerEngine up: one entry, no questions needed (the settings folder is the one the app already uses)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

from .const import CONF_MODE, CONF_SETTINGS_DIR, DOMAIN, MODE_LIVE, MODE_SHADOW


class PowerEngineConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="PowerEngine", data={}, options=_options(user_input))
        return self.async_show_form(step_id="user", data_schema=_schema(MODE_LIVE, ""))

    async def async_step_import(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PowerEngineOptionsFlow()


class PowerEngineOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_options(user_input))
        options = self.config_entry.options
        return self.async_show_form(step_id="init", data_schema=_schema(options.get(CONF_MODE, MODE_LIVE),
                                                                         options.get(CONF_SETTINGS_DIR, "")))


def _schema(mode: str, folder: str) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_MODE, default=mode): SelectSelector(SelectSelectorConfig(
            options=[MODE_LIVE, MODE_SHADOW], translation_key=CONF_MODE, mode=SelectSelectorMode.LIST)),
        vol.Optional(CONF_SETTINGS_DIR, default=folder): str,
    })


def _options(user_input: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {CONF_MODE: user_input.get(CONF_MODE, MODE_LIVE)}
    folder = str(user_input.get(CONF_SETTINGS_DIR, "")).strip()
    if folder:
        options[CONF_SETTINGS_DIR] = folder
    return options
