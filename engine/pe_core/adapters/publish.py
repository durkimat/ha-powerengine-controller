"""How PowerEngine's own entities get into Home Assistant: as real entities made by the integration, or as plain states.

The core and the app shell never know which. They call a `StatePublisher`:

    discover(ent, version)   create an entity (a native entity, or a state with its attributes)
    publish(key, state, attrs)   a new state and, optionally, its JSON attributes
    preset(key, value)       set a switch or select that HA also controls
    retire(ent) / retire_all()   remove entities
    available(online)        PowerEngine up or down

`NativePublisher` is what runs inside Home Assistant (the integration's host makes the entities). `DirectPublisher`
writes HA states with `set_state`: the demo, the tests and a host that makes no native entities use it. Entity ids are
the same either way, `ent.entity_id` (`sensor.pe_diag_version`).

Direct mode gaps: entities are not in HA's entity registry (no unique id, so no renaming or area in the UI, and they
vanish when HA restarts until PowerEngine next publishes); they are rebuilt at every start; and commands from HA
(switch toggles, select changes) reach PowerEngine as `call_service` events (commands.py). `available(False)` marks
every entity `unavailable`; `retire` marks it `unavailable` with a note unless a `remove_state` callable is given.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from ..entities import ENTITIES, EntityDef

DEVICE_NAME = "PowerEngine"
# What an entity's definition gives HA as state attributes when it is written as a plain state.
STATE_ATTRIBUTE_OPTIONS = ("icon", "unit_of_measurement", "device_class", "state_class")
_BY_KEY = {e.key: e for e in ENTITIES}


@runtime_checkable
class StatePublisher(Protocol):
    name: str
    retains: bool      # True when what is published outlives a restart (native entities are in the registry)

    def discover(self, ent: EntityDef, version: str) -> None: ...

    def publish(self, key: str, state: Any, attrs: dict | str | None = None) -> None: ...

    def preset(self, key: str, value: str) -> None: ...

    def retire(self, ent: EntityDef) -> None: ...

    def retire_all(self) -> None: ...

    def available(self, online: bool) -> None: ...


class DirectPublisher:
    """Creates and updates HA states with `set_state(entity_id, state=..., attributes=...)`."""

    name = "direct"
    retains = False

    def __init__(self, set_state: Callable[..., Any], remove_state: Callable[[str], Any] | None = None):
        self._set = set_state
        self._remove = remove_state
        self._ents: dict[str, EntityDef] = {}
        self._state: dict[str, str] = {}
        self._json: dict[str, dict] = {}

    # the attributes the definition gives the entity, plus the last JSON attributes published for it
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


class NativePublisher(DirectPublisher):
    """Real Home Assistant entities, made by the integration the engine runs under (host.native_entities): the engine
    says what each is (`discover`), writes its state and extra attributes as it goes, and says when it is up or down.
    They are in the entity registry with a unique id (`powerengine_<key>`) and belong to the PowerEngine
    device, so they keep their place across restarts, can be renamed and given an area."""

    name = "native"
    retains = True

    def __init__(self, native: Any, set_state: Callable[..., Any], told_once: Callable[..., Any] | None = None):
        super().__init__(set_state, None)
        self._native = native
        self._told = told_once or (lambda *a, **k: None)
        self._plain: set[str] = set()                      # keys the integration would not make native: plain states

    def attributes(self, ent: EntityDef) -> dict[str, Any]:
        if ent.key in self._plain:
            return super().attributes(ent)
        return dict(self._json.get(ent.key, {}))           # the entity itself carries the rest

    def discover(self, ent: EntityDef, version: str) -> None:
        self._ents[ent.key] = ent
        try:
            self._native.define(ent.component, ent.key, ent.name, dict(ent.options))
            self._plain.discard(ent.key)
        except Exception as err:                           # not connected, or the integration cannot make it
            self._plain.add(ent.key)
            self._told("native_define", f"Could not make {ent.entity_id} a native entity ({err}); "
                                        "writing a plain state instead", "WARNING")
        self._write(ent, self._state.get(ent.key, "unknown"))

    def retire(self, ent: EntityDef) -> None:
        self._ents.pop(ent.key, None)
        self._state.pop(ent.key, None)
        self._json.pop(ent.key, None)
        try:
            self._native.retire(ent.entity_id)
        except Exception as err:
            self._told("native_retire", f"Could not remove {ent.entity_id}: {err}", "WARNING")

    def available(self, online: bool) -> None:
        try:
            self._native.online(online)
        except Exception as err:
            self._told("native_online", f"Could not tell Home Assistant the engine is {'up' if online else 'down'}: "
                                        f"{err}", "WARNING")


def select_publisher(set_state: Callable[..., Any] | None, remove_state: Callable[[str], Any] | None = None,
                     native: Any = None, told_once: Callable[..., Any] | None = None) -> StatePublisher | None:
    """Native entities where the host makes them (the integration), else plain states with `set_state`; None when the
    host can do neither."""
    if set_state is None:
        return None
    if native is not None:
        return NativePublisher(native, set_state, told_once)
    return DirectPublisher(set_state, remove_state)
