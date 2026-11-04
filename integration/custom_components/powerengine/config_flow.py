"""Setting PowerEngine up: one entry, no questions needed (the settings folder is the one the app already uses)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import CONF_SETTINGS_DIR, DEFAULT_SETTINGS_FOLDER, DOMAIN


class PowerEngineConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="PowerEngine", data={}, options=_options(user_input))
        return self.async_show_form(step_id="user", data_schema=_schema(self.hass.config.path(DEFAULT_SETTINGS_FOLDER)))

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
        current = self.config_entry.options.get(CONF_SETTINGS_DIR) or self.hass.config.path(DEFAULT_SETTINGS_FOLDER)
        return self.async_show_form(step_id="init", data_schema=_schema(current))


def _schema(default: str) -> vol.Schema:
    return vol.Schema({vol.Required(CONF_SETTINGS_DIR, default=default): str})


def _options(user_input: dict[str, Any]) -> dict[str, Any]:
    folder = str(user_input.get(CONF_SETTINGS_DIR, "")).strip()
    return {CONF_SETTINGS_DIR: folder} if folder else {}
