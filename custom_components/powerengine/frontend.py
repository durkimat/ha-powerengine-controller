"""PowerEngine's dashboard card, served by the integration.

The card (`card/` in the repository, built into `frontend/ha-powerengine-card.js`) is a part of PowerEngine, not a
separate install: the integration serves the file and asks Home Assistant's frontend to load it on every page (the
same as an integration's own panel code), so the dashboard's `custom:powerengine-...` cards work with nothing added to
Lovelace's resources. The address carries a fingerprint of the file, so a browser fetches a new card as soon as an
update replaces it (the once-a-minute check in `__init__` calls `async_refresh`).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
CARD_FILE = Path(__file__).parent / "frontend" / "ha-powerengine-card.js"
CARD_URL = f"/{DOMAIN}_card/ha-powerengine-card.js"
_KEY = f"{DOMAIN}_card"                      # hass.data: {"served": bool, "url": the address now registered or None}


def _fingerprint(path: Path) -> str | None:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:12]      # noqa: S324 (a cache-buster, not security)
    except OSError:
        return None


def _extra_urls(hass: HomeAssistant):
    """Home Assistant's set of extra modules to load, or None where the frontend is not running (nothing to add to)."""
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
    return hass.data.get(DATA_EXTRA_MODULE_URL)


def _state(hass: HomeAssistant) -> dict:
    return hass.data.setdefault(_KEY, {"served": False, "url": None})


async def async_register(hass: HomeAssistant) -> None:
    """Serve the card (once per run of Home Assistant) and add it to the pages the frontend loads."""
    state = _state(hass)
    if not await hass.async_add_executor_job(CARD_FILE.is_file):
        _LOGGER.warning("The PowerEngine card is missing from the integration (%s): reinstall PowerEngine", CARD_FILE)
        return
    if not state["served"]:
        await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_FILE), False)])
        state["served"] = True
    await async_refresh(hass)


async def async_refresh(hass: HomeAssistant) -> None:
    """Point the frontend at the card as it is on disk now (a new address when the file has changed)."""
    state = _state(hass)
    if not state["served"]:
        return
    fingerprint = await hass.async_add_executor_job(_fingerprint, CARD_FILE)
    urls = _extra_urls(hass)
    if fingerprint is None or urls is None:
        return
    url = f"{CARD_URL}?v={fingerprint}"
    if state["url"] == url:
        return
    if state["url"] is not None:
        urls.discard(state["url"])
    urls.add(url)
    state["url"] = url
    _LOGGER.debug("PowerEngine card registered with the frontend: %s", url)


def async_unregister(hass: HomeAssistant) -> None:
    """Stop adding the card to pages (the served file stays: it cannot be removed until Home Assistant restarts)."""
    state = _state(hass)
    urls = _extra_urls(hass)
    if state["url"] is not None and urls is not None:
        urls.discard(state["url"])
    state["url"] = None
