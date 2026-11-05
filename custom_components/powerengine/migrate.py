"""Taking over from the AppDaemon app (docs/plans/native-integration.md, "Migrating a running system").

The app publishes its entities through MQTT discovery. Native entities have the same unique ids (`powerengine_<key>`)
and entity ids (`sensor.pe_<key>`), but they are another platform's, so while the old registry entries exist the new
ones would come out as `sensor.pe_<key>_2`. The engine is therefore not started until `migrate_from_mqtt` has run:

  1. it writes a snapshot of every old entity (its state, and what the owner set on it: name, icon, area, labels,
     hidden) to `<settings folder>/migration/`, so nothing is lost and a rollback can be checked against it;
  2. it clears the app's retained MQTT messages (discovery, state, attributes and the switches' command topics), so
     the old entities do not come back at the next Home Assistant start;
  3. it removes the old registry entries. History is kept by entity id, so it carries on under the new entities;
  4. it removes a shadow run's leftovers (`sensor.pe_shadow_...`) when the entry is no longer a shadow run.

The two switches (pause, right-align numbers) start in the state they had, and the owner's customisations are applied to
the new entities as they are made (`Snapshot`, used by native.py).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

OLD_PLATFORM = "mqtt"
UNIQUE_PREFIX = "powerengine_"
SHADOW_UNIQUE_PREFIX = "powerengine_shadow_"
SNAPSHOT_DIR = "migration"
BASE_TOPIC = "powerengine"                     # the app's MQTT topics (pe_core/entities.py)
DISCOVERY_PREFIX = "homeassistant"
CUSTOM = ("name", "icon", "area_id", "labels", "hidden_by")        # what an owner can set on an entity


def old_entries(hass: HomeAssistant) -> list[er.RegistryEntry]:
    """The AppDaemon app's entities: MQTT-platform entries with PowerEngine's unique ids."""
    return [e for e in er.async_get(hass).entities.values()
            if e.platform == OLD_PLATFORM and (e.unique_id or "").startswith(UNIQUE_PREFIX)]


def shadow_entries(hass: HomeAssistant) -> list[er.RegistryEntry]:
    """A shadow run's entities, which are this integration's."""
    return [e for e in er.async_get(hass).entities.values()
            if e.platform == DOMAIN and (e.unique_id or "").startswith(SHADOW_UNIQUE_PREFIX)]


def topics(entry: er.RegistryEntry) -> list[str]:
    """Every retained topic the app keeps for one of its entities."""
    key = entry.unique_id[len(UNIQUE_PREFIX):]
    domain = entry.entity_id.split(".", 1)[0]
    found = [f"{DISCOVERY_PREFIX}/{domain}/{BASE_TOPIC}/{key}/config", f"{BASE_TOPIC}/{key}/state",
             f"{BASE_TOPIC}/{key}/attributes"]
    if domain == "switch":
        found.append(f"{BASE_TOPIC}/{key}/set")
    return found


def describe(hass: HomeAssistant, entry: er.RegistryEntry) -> dict[str, Any]:
    state = hass.states.get(entry.entity_id)
    return {"entity_id": entry.entity_id, "unique_id": entry.unique_id,
            "state": state.state if state else None,
            "name": entry.name, "icon": entry.icon, "area_id": entry.area_id,
            "labels": sorted(entry.labels), "hidden_by": entry.hidden_by.value if entry.hidden_by else None,
            "disabled_by": entry.disabled_by.value if entry.disabled_by else None}


def write_snapshot(folder: Path, rows: list[dict[str, Any]], when: datetime) -> Path:
    target = folder / SNAPSHOT_DIR
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"mqtt-entities-{when:%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"taken": when.isoformat(), "entities": rows}, indent=1), encoding="utf-8")
    return path


class Snapshot:
    """The newest snapshot in a folder, for the native entities to start from. Empty when there is none."""

    def __init__(self, entities: dict[str, dict[str, Any]] | None = None) -> None:
        self.entities = entities or {}
        self.applied: set[str] = set()

    @classmethod
    def load(cls, folder: Path) -> Snapshot:
        files = sorted((folder / SNAPSHOT_DIR).glob("mqtt-entities-*.json"))
        if not files:
            return cls()
        try:
            rows = json.loads(files[-1].read_text(encoding="utf-8"))["entities"]
        except (OSError, ValueError, KeyError):
            _LOGGER.warning("Could not read the migration snapshot %s", files[-1])
            return cls()
        return cls({row["entity_id"]: row for row in rows})

    def state(self, entity_id: str) -> str | None:
        return (self.entities.get(entity_id) or {}).get("state")

    def customisation(self, entity_id: str) -> dict[str, Any]:
        """What to set on the new entity, once: the fields the owner had set (not yet applied)."""
        row = self.entities.get(entity_id)
        if row is None or entity_id in self.applied:
            return {}
        self.applied.add(entity_id)
        found: dict[str, Any] = {}
        if row.get("name"):
            found["name"] = row["name"]
        if row.get("icon"):
            found["icon"] = row["icon"]
        if row.get("area_id"):
            found["area_id"] = row["area_id"]
        if row.get("labels"):
            found["labels"] = set(row["labels"])
        if row.get("hidden_by"):
            found["hidden_by"] = er.RegistryEntryHider(row["hidden_by"])
        return found


@dataclass
class Result:
    entities: int = 0                          # old entities found
    snapshot: str | None = None
    cleared_topics: int = 0
    removed: int = 0
    shadow_removed: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"entities": self.entities, "snapshot": self.snapshot, "cleared_topics": self.cleared_topics,
                "removed": self.removed, "shadow_removed": self.shadow_removed, "notes": self.notes}


async def async_migrate(hass: HomeAssistant, folder: Path, shadow_leftovers: bool = True) -> Result:
    """Do the migration (the caller has checked the AppDaemon app is stopped). Safe to run again: it finds nothing."""
    result = Result()
    old = old_entries(hass)
    result.entities = len(old)
    registry = er.async_get(hass)
    if old:
        rows = [describe(hass, e) for e in old]
        when = datetime.now(timezone.utc)
        path = await hass.async_add_executor_job(write_snapshot, folder, rows, when)
        result.snapshot = str(path)
        if hass.services.has_service("mqtt", "publish"):
            for entry in old:                                       # the discovery topic first: HA removes the entity
                for topic in topics(entry):
                    await hass.services.async_call("mqtt", "publish", {"topic": topic, "payload": "", "retain": True,
                                                                       "qos": 1}, blocking=True)
                    result.cleared_topics += 1
            await hass.services.async_call("mqtt", "publish", {"topic": f"{BASE_TOPIC}/status", "payload": "",
                                                               "retain": True, "qos": 1}, blocking=True)
            await hass.async_block_till_done()
        else:
            result.notes.append("MQTT is not loaded here, so the app's retained messages were left alone: clear the "
                                f"topics under '{BASE_TOPIC}/' and '{DISCOVERY_PREFIX}/+/{BASE_TOPIC}/' by hand")
        for entry in old_entries(hass):                             # whatever MQTT did not remove itself
            registry.async_remove(entry.entity_id)
        for entry in old:                                           # and a state left behind would make the new entity
            if registry.async_get(entry.entity_id) is None and hass.states.get(entry.entity_id) is not None:
                hass.states.async_remove(entry.entity_id)               # take another id (sensor.pe_..._2)
        result.removed = len(old)
        unknown = [r["entity_id"] for r in rows if r["entity_id"].startswith("switch.")
                   and r["state"] in (None, "unavailable", "unknown")]
        if unknown:
            result.notes.append(f"{', '.join(unknown)} had no state to carry over (the app was already stopped): "
                                "set them by hand if they were not off")
    if shadow_leftovers:
        leftovers = shadow_entries(hass)
        for entry in leftovers:
            registry.async_remove(entry.entity_id)
            if hass.states.get(entry.entity_id) is not None:
                hass.states.async_remove(entry.entity_id)
        result.shadow_removed = len(leftovers)
        if leftovers:                                               # the shadow's device, once it has no entities
            devices = dr.async_get(hass)
            for entry_id in {e.config_entry_id for e in leftovers if e.config_entry_id}:
                device = devices.async_get_device(identifiers={(DOMAIN, f"{entry_id}_shadow")})
                if device is not None and not er.async_entries_for_device(registry, device.id,
                                                                          include_disabled_entities=True):
                    devices.async_remove_device(device.id)
    _LOGGER.info("Migration from the AppDaemon app: %s", result.as_dict())
    return result
