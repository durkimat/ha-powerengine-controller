"""PowerEngine's dashboard card, served by the integration.

The card (`card/` in the repository, built into `frontend/ha-powerengine-card.js`) is a part of PowerEngine, not a
separate install: the integration serves the file and adds it to Lovelace's resources itself, so the dashboard's
`custom:powerengine-...` cards work with nothing added by hand. A resource is what makes Home Assistant wait for the
card before it draws a dashboard; a page-wide extra module (used only where resources are kept in YAML, or Lovelace is
absent) loads alongside the dashboard, and on a slow route (a phone on mobile data through the cloud) the cards were
drawn first and stayed "Configuration error". The address carries a fingerprint of the file, so a browser fetches a
new card as soon as an update replaces it (the once-a-minute check in `__init__` calls `async_refresh`), and the file
is served with a long cache lifetime.
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
_KEY = f"{DOMAIN}_card"      # hass.data: {"served": bool, "url": the address registered or None, "resource": id}


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
    return hass.data.setdefault(_KEY, {"served": False, "url": None, "resource": None})


async def async_register(hass: HomeAssistant) -> None:
    """Serve the card (once per run of Home Assistant) and add it to the pages the frontend loads."""
    state = _state(hass)
    if not await hass.async_add_executor_job(CARD_FILE.is_file):
        _LOGGER.warning("The PowerEngine card is missing from the integration (%s): reinstall PowerEngine", CARD_FILE)
        return
    if not state["served"]:
        await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_FILE), True)])
        state["served"] = True
    await async_refresh(hass)


def _resources(hass: HomeAssistant):
    """Lovelace's resource store when it is kept in storage (so we may add to it), else None."""
    try:
        from homeassistant.components.lovelace import LOVELACE_DATA
    except ImportError:
        return None
    data = hass.data.get(LOVELACE_DATA)
    resources = getattr(data, "resources", None)
    return resources if hasattr(resources, "async_create_item") else None


def _ours(item: dict) -> bool:
    return str(item.get("url", "")).split("?")[0] == CARD_URL


async def async_refresh(hass: HomeAssistant) -> None:
    """Point the frontend at the card as it is on disk now (a new address when the file has changed)."""
    state = _state(hass)
    if not state["served"]:
        return
    fingerprint = await hass.async_add_executor_job(_fingerprint, CARD_FILE)
    if fingerprint is None:
        return
    url = f"{CARD_URL}?v={fingerprint}"
    resources = _resources(hass)
    if resources is not None:
        await _resource(hass, resources, state, url)
        return
    urls = _extra_urls(hass)
    if urls is None or state["url"] == url:
        return
    if state["url"] is not None:
        urls.discard(state["url"])
    urls.add(url)
    state["url"] = url
    _LOGGER.debug("PowerEngine card registered with the frontend as an extra module: %s", url)


async def _resource(hass: HomeAssistant, resources, state: dict, url: str) -> None:
    """Make Lovelace's resource list hold exactly one entry for the card, at `url`."""
    await resources.async_get_info()                      # loads the stored list the first time
    mine = [item for item in resources.async_items() if _ours(item)]
    urls = _extra_urls(hass)
    if urls is not None and state["url"] is not None and state["url"] not in {i.get("url") for i in mine}:
        urls.discard(state["url"])                        # the resource now does the job: not loaded twice
    if mine:
        keep, *extra = mine
        for item in extra:
            await resources.async_delete_item(item["id"])
        if keep.get("url") != url or keep.get("type") != "module":
            await resources.async_update_item(keep["id"], {"res_type": "module", "url": url})
        state["resource"] = keep["id"]
    else:
        item = await resources.async_create_item({"res_type": "module", "url": url})
        state["resource"] = item["id"]
    state["url"] = url


async def async_unregister(hass: HomeAssistant) -> None:
    """Stop adding the card to pages (the served file stays: it cannot be removed until Home Assistant restarts)."""
    state = _state(hass)
    urls = _extra_urls(hass)
    if state["url"] is not None and urls is not None:
        urls.discard(state["url"])
    resources = _resources(hass)
    if resources is not None:
        await resources.async_get_info()
        for item in [i for i in resources.async_items() if _ours(i)]:
            await resources.async_delete_item(item["id"])
    state["url"] = state["resource"] = None
