"""The one-button update: the latest PowerEngine from HACS (or Home Assistant's Marketplace, which took over from it in
2026.11), then the engine restarted onto it (`powerengine.update`).

What the `script.powerengine_update` in the package earlier versions wrote did, as part of the integration. HACS does
the download (its update entity for PowerEngine); the engine is restarted by the supervisor as soon as it sees the new
files. Home Assistant itself only needs a restart when the integration's own part changed, which HACS says by its own
"restart required" notice.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
NOTIFICATION = f"{DOMAIN}_update"
SETTLE_S = 5                       # after asking HACS to look: time for its entity to show what it found
RUNNING_WAIT_S = 180               # for the engine to come back on the new version


STORES = ("hacs", "marketplace")      # whose update entities carry PowerEngine: HACS, or the Marketplace since 2026.11


def hacs_updates(hass: HomeAssistant) -> list[str]:
    """The store's update entities for PowerEngine itself (not the retired separate card)."""
    return sorted(e.entity_id for e in er.async_get(hass).entities.values()
                  if e.platform in STORES and e.domain == "update" and "powerengine" in e.entity_id
                  and "card" not in e.entity_id and not e.disabled)


def _note(hass: HomeAssistant, message: str) -> None:
    persistent_notification.async_create(hass, message, title="PowerEngine update", notification_id=NOTIFICATION)


async def async_update(hass: HomeAssistant, runtime, settle_s: float = SETTLE_S,
                       running_wait_s: float = RUNNING_WAIT_S) -> str:
    """Look for, install and start the latest release. Returns the message it also leaves as a notification."""
    entities = hacs_updates(hass)
    if not entities:
        _note(hass, "No HACS update entity for PowerEngine was found. In HACS (or the Marketplace) check PowerEngine "
                    "is downloaded, and under Settings > Entities that its update entity is enabled.")
        raise ServiceValidationError("No HACS update entity for PowerEngine was found")
    try:
        await hass.services.async_call("homeassistant", "update_entity", {"entity_id": entities}, blocking=True)
    except Exception as err:                              # (carry on with what HACS already knows)
        _LOGGER.debug("Could not ask HACS to look for a new release: %r", err)
    await asyncio.sleep(settle_s)
    pending = [e for e in entities if hass.states.is_state(e, "on")]
    if pending:
        try:
            await hass.services.async_call("update", "install", {"entity_id": pending}, blocking=True)
        except HomeAssistantError as err:                 # e.g. the Marketplace's one-time warning, not yet accepted
            _note(hass, f"The update was not installed: {err}")
            raise
    state = hass.states.get(entities[0])
    installed = str(state.attributes.get("installed_version") or "").lstrip("v") if state else ""
    await runtime.check()                                 # the engine restarts onto new files at once
    waited = 0.0
    while installed and runtime.supervisor.running != installed and waited < running_wait_s:
        await asyncio.sleep(2)
        waited += 2
    if installed and runtime.supervisor.running == installed:
        message = f"PowerEngine {installed} is running."
    elif installed:
        message = (f"PowerEngine {installed} is installed but {runtime.supervisor.running or 'no engine'} is still "
                   "running: check PowerEngine's log.")
    else:
        message = "Updates checked."
    message += (" Nothing new to install." if not pending else " Installed the latest release.") + (
        " Refresh the browser (or reopen the app) so the dashboard loads the matching card.")
    _note(hass, message)
    return message
