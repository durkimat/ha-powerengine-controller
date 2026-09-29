"""How PowerEngine's own entities get into Home Assistant: over MQTT (today's way) or directly through AppDaemon.

The core and the app shell never know which. They call a `StatePublisher`:

    discover(ent, version)   create an entity (MQTT discovery, or a state with its attributes)
    publish(key, state, attrs)   a new state and, optionally, its JSON attributes
    preset(key, value)       set a switch or select that HA also controls (the retained command/state topic)
    retire(ent) / retire_all()   remove entities
    available(online)        PowerEngine up or down

`MqttPublisher` is exactly the old behaviour (same topics, payloads, QoS 1, retained). `DirectPublisher` needs no MQTT
broker: it writes HA states through AppDaemon's `set_state`. Entity ids are the same either way, `ent.entity_id`
(`sensor.pe_diag_version`), which is also the `default_entity_id` MQTT discovery asks HA for.

Direct mode gaps: entities are not in HA's entity registry (no unique id, so no renaming or area in the UI, and they
vanish when HA restarts until PowerEngine next publishes); they are rebuilt at every start; and commands from HA
(switch toggles, select changes) don't reach PowerEngine: that is step B2. `available(False)` marks every entity
`unavailable`; `retire` marks it `unavailable` with a note unless a `remove_state` callable is given.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from ..entities import (
    AVAILABILITY_TOPIC,
    BASE_TOPIC,
    ENTITIES,
    OFFLINE,
    ONLINE,
    EntityDef,
    discovery_payload,
    entity_removal_messages,
    removal_messages,
)

CHOICES = ("auto", "mqtt", "direct")
DEVICE_NAME = "PowerEngine"
# What MQTT discovery would give HA as state attributes (the rest of the options are registry or MQTT-only settings).
STATE_ATTRIBUTE_OPTIONS = ("icon", "unit_of_measurement", "device_class", "state_class")
_BY_KEY = {e.key: e for e in ENTITIES}


@runtime_checkable
class StatePublisher(Protocol):
    name: str
    retains: bool      # True when what is published outlives a restart (MQTT retained messages)

    def discover(self, ent: EntityDef, version: str) -> None: ...

    def publish(self, key: str, state: Any, attrs: dict | str | None = None) -> None: ...

    def preset(self, key: str, value: str) -> None: ...

    def retire(self, ent: EntityDef) -> None: ...

    def retire_all(self) -> None: ...

    def available(self, online: bool) -> None: ...


class MqttPublisher:
    """Publishes through the AppDaemon MQTT plugin's `mqtt_publish`, as PowerEngine always has."""

    name = "MQTT"
    retains = True

    def __init__(self, api: Any):
        self.api = api

    def _send(self, topic: str, payload: Any) -> None:
        if not isinstance(payload, str):
            payload = json.dumps(payload, default=str)
        self.api.mqtt_publish(topic, payload, qos=1, retain=True)

    def discover(self, ent: EntityDef, version: str) -> None:
        self._send(ent.discovery_topic, discovery_payload(ent, version))

    def publish(self, key: str, state: Any, attrs: dict | str | None = None) -> None:
        self._send(f"{BASE_TOPIC}/{key}/state", str(state))
        if attrs is not None:
            self._send(f"{BASE_TOPIC}/{key}/attributes", attrs)

    def preset(self, key: str, value: str) -> None:
        ent = _BY_KEY.get(key)
        self._send(ent.options["command_topic"] if ent and "command_topic" in ent.options
                   else f"{BASE_TOPIC}/{key}/set", value)

    def retire(self, ent: EntityDef) -> None:
        for topic, payload in entity_removal_messages(ent):
            self._send(topic, payload)

    def retire_all(self) -> None:
        for topic, payload in removal_messages():
            self._send(topic, payload)

    def available(self, online: bool) -> None:
        self._send(AVAILABILITY_TOPIC, ONLINE if online else OFFLINE)


class DirectPublisher:
    """Creates and updates HA states through AppDaemon: `set_state(entity_id, state=..., attributes=...)`."""

    name = "direct"
    retains = False

    def __init__(self, set_state: Callable[..., Any], remove_state: Callable[[str], Any] | None = None):
        self._set = set_state
        self._remove = remove_state
        self._ents: dict[str, EntityDef] = {}
        self._state: dict[str, str] = {}
        self._json: dict[str, dict] = {}

    # attributes MQTT discovery would have given the entity, plus the last JSON attributes published for it
    def attributes(self, ent: EntityDef) -> dict[str, Any]:
        attrs: dict[str, Any] = {"friendly_name": f"{DEVICE_NAME} {ent.name}"}
        attrs.update({k: ent.options[k] for k in STATE_ATTRIBUTE_OPTIONS if k in ent.options})
        if "options" in ent.options:
            attrs["options"] = list(ent.options["options"])
        attrs.update(self._json.get(ent.key, {}))
        return attrs

    @staticmethod
    def state_text(ent: EntityDef, state: Any) -> str:
        text = str(state)
        if ent.component in ("switch", "binary_sensor") and text.upper() in ("ON", "OFF"):
            return text.lower()
        return text

    def _entity(self, key: str) -> EntityDef:
        ent = self._ents.get(key) or _BY_KEY.get(key)
        if ent is None:                                   # not defined anywhere: a plain sensor
            ent = EntityDef("sensor", key, key)
        self._ents[key] = ent
        return ent

    def _write(self, ent: EntityDef, state: str) -> None:
        self._state[ent.key] = state
        self._set(ent.entity_id, state=state, attributes=self.attributes(ent))

    def discover(self, ent: EntityDef, version: str) -> None:
        self._ents[ent.key] = ent
        self._write(ent, self._state.get(ent.key, "unknown"))         # a repeat keeps the value it has

    def publish(self, key: str, state: Any, attrs: dict | str | None = None) -> None:
        ent = self._entity(key)
        if attrs is not None:
            try:
                parsed = json.loads(attrs) if isinstance(attrs, str) else attrs
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                self._json[key] = json.loads(json.dumps(parsed, default=str))
        self._write(ent, self.state_text(ent, state))

    def preset(self, key: str, value: str) -> None:
        self.publish(key, value)

    def retire(self, ent: EntityDef) -> None:
        self._ents.pop(ent.key, None)
        self._state.pop(ent.key, None)
        self._json.pop(ent.key, None)
        if self._remove is not None:
            self._remove(ent.entity_id)
        else:
            self._set(ent.entity_id, state="unavailable",
                      attributes={"friendly_name": f"{DEVICE_NAME} {ent.name}", "note": "retired by PowerEngine"})

    def retire_all(self) -> None:
        for ent in {**_BY_KEY, **self._ents}.values():
            self.retire(ent)

    def available(self, online: bool) -> None:
        if not online:                                    # coming back online: the next states replace these
            for ent in list(self._ents.values()):
                self._set(ent.entity_id, state="unavailable", attributes=self.attributes(ent))


def select_publisher(choice: str, mqtt_api: Any, set_state: Callable[..., Any] | None,
                     remove_state: Callable[[str], Any] | None = None) -> StatePublisher | None:
    """`auto`: MQTT when the AppDaemon MQTT plugin is there, else direct. `mqtt` without the plugin: None."""
    if choice == "direct" or (choice == "auto" and mqtt_api is None):
        return DirectPublisher(set_state, remove_state) if set_state is not None else None
    return MqttPublisher(mqtt_api) if mqtt_api is not None else None
