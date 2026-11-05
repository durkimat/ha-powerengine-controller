"""A button that restarts the engine (to pick up an update at once)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .native import device_info


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([RestartEngineButton(entry)])


class RestartEngineButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Restart engine"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_restart_engine"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        await self._entry.runtime_data.supervisor.restart()
