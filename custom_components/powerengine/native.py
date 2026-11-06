"""The engine's own entities as Home Assistant entities.

The engine says what each entity is (`define`: component, key, name, options as PowerEngine's catalogue has them) and
writes its state and extra attributes as it goes (`update`). Here each becomes a real entity: in the entity registry
with the unique id `powerengine_<key>` (as the entities of the first versions had), the entity id
`<component>.pe_<key>`, and on the PowerEngine device. Because the engine describes them, a new entity in an
engine update needs nothing from this integration, so no Home Assistant restart.

Only sensors, binary sensors and switches exist in the catalogue. A switch holds the state it was given (the pause
switch), and the engine hears a change of it as it hears any other state, so no command path is needed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import CONF_MODE, DOMAIN, MODE_SHADOW
from .shadow import SHADOW_PREFIX

_LOGGER = logging.getLogger(__name__)
NOTHING = (None, "", "unknown", "None", "none")
NUMERIC_DEVICE_CLASSES = {c for c in SensorDeviceClass if c not in (
    SensorDeviceClass.ENUM, SensorDeviceClass.TIMESTAMP, SensorDeviceClass.DATE)}


def is_shadow(entry: ConfigEntry) -> bool:
    return entry.options.get(CONF_MODE) == MODE_SHADOW


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """One device for everything the engine makes; a shadow run's is its own, so it never mixes with the live one."""
    shadow = is_shadow(entry)
    return DeviceInfo(identifiers={(DOMAIN, f"{entry.entry_id}_shadow" if shadow else entry.entry_id)},
                      name="PowerEngine (shadow)" if shadow else "PowerEngine", manufacturer="PowerEngine",
                      model="Engine process", configuration_url="https://github.com/durkimat/ha-powerengine-controller")


class NativeEntities:
    """Makes, updates and removes the engine's entities. Everything here runs on Home Assistant's event loop."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass, self.entry = hass, entry
        self.shadow = is_shadow(entry)
        self.prefix = SHADOW_PREFIX if self.shadow else "pe_"          # sensor.pe_<key>, or the shadow's own ids
        self.unique_prefix = "powerengine_shadow_" if self.shadow else "powerengine_"
        self.entities: dict[str, PowerEngineEntity] = {}
        self.adders: dict[str, Callable[[list], None]] = {}
        self.online = True

    @callback
    def attach(self, component: str, add_entities: Callable[[list], None]) -> None:
        """A platform is ready: it takes the entities of its component, those already defined first."""
        self.adders[component] = add_entities
        waiting = [e for e in self.entities.values() if e.component == component and not e.added]
        if waiting:
            add_entities(waiting)

    @callback
    def define(self, component: str, key: str, name: str, options: dict) -> bool:
        """Make the entity, or change how it is described. False: this integration has no such kind of entity (the
        engine then writes a plain state)."""
        cls = {"sensor": PowerEngineSensor, "binary_sensor": PowerEngineBinarySensor,
               "switch": PowerEngineSwitch}.get(component)
        if cls is None:
            raise ValueError(f"no native {component} entities")
        entity_id = f"{component}.{self.prefix}{key}"
        entity = self.entities.get(entity_id)
        if entity is None:
            entity = self.entities[entity_id] = cls(self, entity_id, key, name, options)
            add = self.adders.get(component)
            if add is not None:
                add([entity])
        else:
            entity.redefine(name, options)
        return True

    def knows(self, entity_id: str) -> bool:
        return entity_id in self.entities

    @callback
    def update(self, entity_id: str, state: Any, attributes: dict) -> None:
        self.entities[entity_id].update(state, attributes)

    @callback
    def retire(self, entity_id: str) -> None:
        entity = self.entities.pop(entity_id, None)
        if entity is not None and entity.added:
            self.hass.async_create_task(entity.async_remove())
        registry = er.async_get(self.hass)
        if registry.async_get(entity_id) is not None:
            registry.async_remove(entity_id)

    @callback
    def set_online(self, online: bool) -> None:
        self.online = online
        for entity in self.entities.values():
            entity.refresh()


class PowerEngineEntity(Entity):
    """What every one of the engine's entities has: its identity, its place on the device and the state it was given."""

    component = ""
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, native: NativeEntities, entity_id: str, key: str, name: str, options: dict) -> None:
        self._native = native
        self.entity_id = entity_id
        self._key = key
        self._attr_unique_id = f"{native.unique_prefix}{key}"
        self._attr_device_info = device_info(native.entry)
        self._state: str | None = None
        self._attrs: dict[str, Any] = {}
        self.added = False
        self._told: set[str] = set()
        self.redefine(name, options, write=False)

    def redefine(self, name: str, options: dict, write: bool = True) -> None:
        self._attr_name = name
        self._attr_icon = options.get("icon")
        category = options.get("entity_category")
        self._attr_entity_category = EntityCategory(category) if category in ("config", "diagnostic") else None
        self._options = options
        self._describe(options)
        if write:
            self.refresh()

    def _describe(self, options: dict) -> None:
        """Whatever more the kind of entity takes from its options."""

    @callback
    def update(self, state: Any, attributes: dict) -> None:
        self._state = None if state is None else str(state)
        self._attrs = dict(attributes or {})
        self.refresh()

    @callback
    def refresh(self) -> None:
        if self.added:
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._native.online and self._state != "unavailable"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs

    async def async_added_to_hass(self) -> None:
        self.added = True

    async def async_will_remove_from_hass(self) -> None:
        self.added = False

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._told:
            self._told.add(key)
            _LOGGER.warning("%s: %s", self.entity_id, message)


class PowerEngineSensor(PowerEngineEntity, SensorEntity):
    component = "sensor"

    def _describe(self, options: dict) -> None:
        self._attr_native_unit_of_measurement = options.get("unit_of_measurement")
        self._attr_suggested_display_precision = options.get("suggested_display_precision")
        self._attr_options = list(options["options"]) if options.get("options") else None
        try:
            device_class = options.get("device_class")
            self._attr_device_class = SensorDeviceClass(device_class) if device_class else None
        except ValueError:
            self._attr_device_class = None
        try:
            self._attr_state_class = SensorStateClass(options["state_class"]) if options.get("state_class") else None
        except ValueError:
            self._attr_state_class = None

    @property
    def native_value(self) -> Any:
        value = self._state
        if value in NOTHING or value == "unavailable":
            return None
        device_class = self._attr_device_class
        if device_class is SensorDeviceClass.TIMESTAMP:
            parsed = dt_util.parse_datetime(value)
            if parsed is None:
                self._warn_once("timestamp", f"{value!r} is not a time")
                return None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.UTC)
        if device_class is SensorDeviceClass.ENUM:
            if value not in (self._attr_options or []):
                self._warn_once("enum", f"{value!r} is not one of {self._attr_options}")
                return None
            return value
        numeric = (self._attr_native_unit_of_measurement is not None or self._attr_state_class is not None
                   or device_class in NUMERIC_DEVICE_CLASSES)
        if numeric:
            try:
                float(value)
            except ValueError:
                self._warn_once("number", f"{value!r} is not a number")
                return None
        return value


class PowerEngineBinarySensor(PowerEngineEntity, BinarySensorEntity):
    component = "binary_sensor"

    @property
    def is_on(self) -> bool | None:
        value = (self._state or "").lower()
        return True if value in ("on", "true", "1") else False if value in ("off", "false", "0") else None


class PowerEngineSwitch(PowerEngineEntity, SwitchEntity, RestoreEntity):
    """Holds the state it is given: by the engine (`preset`) or by whoever turns it on or off. The state is restored
    when Home Assistant restarts."""

    component = "switch"

    @callback
    def update(self, state: Any, attributes: dict) -> None:
        if state is None or str(state) in NOTHING:                    # "unknown" is the engine finding it new
            self._attrs = dict(attributes or {})
            self.refresh()
            return
        super().update(state, attributes)

    @property
    def is_on(self) -> bool | None:
        value = (self._state or "").lower()
        return True if value == "on" else False if value == "off" else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.update("on", self._attrs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.update("off", self._attrs)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._state is None and (last := await self.async_get_last_state()) is not None:
            if last.state in ("on", "off"):
                self._state = last.state
