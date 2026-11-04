"""The engine's status, as a diagnostic sensor on the PowerEngine device."""

from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import Runtime, signal
from .const import STATUSES
from .native import device_info


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([EngineSensor(entry)])
    entry.runtime_data.native.attach("sensor", async_add_entities)


class EngineSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Engine"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:engine-outline"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_engine"
        self._attr_device_info = device_info(entry)

    @property
    def _runtime(self) -> Runtime:
        return self._entry.runtime_data

    @property
    def native_value(self) -> str:
        return self._runtime.supervisor.status

    @property
    def extra_state_attributes(self) -> dict:
        supervisor = self._runtime.supervisor
        started = None
        if supervisor.started_at:
            started = datetime.fromtimestamp(supervisor.started_at, timezone.utc).isoformat()
        attributes = {"installed_version": supervisor.installed, "running_version": supervisor.running,
                      "update_pending": supervisor.update_needed(), "restarts": supervisor.restarts,
                      "last_exit_status": supervisor.exit_code, "started": started, "mode": "live"}
        ledger = self._runtime.home.ledger
        if ledger is not None:                                      # a shadow run: what it asked for and was refused
            attributes.update(mode="shadow", shadow=ledger.summary())
        return attributes

    async def async_added_to_hass(self) -> None:
        @callback
        def changed() -> None:
            self.async_write_ha_state()
        self.async_on_remove(async_dispatcher_connect(self.hass, signal(self._entry), changed))
