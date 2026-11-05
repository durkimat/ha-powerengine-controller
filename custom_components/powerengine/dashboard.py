"""PowerEngine's own dashboard, registered by the integration.

The engine writes the dashboard it ships to `dashboard.yaml` in its settings folder (and keeps it current). This adds a
Lovelace dashboard that reads that file, in the sidebar as "Power Engine", the same as a `lovelace: dashboards:` entry
in `configuration.yaml` would, so there is nothing to add there. An entry of that kind (the address `powerengine-dash`
is the one the install guide used to ask for) is left alone: it was there first and it wins.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
URL_PATH = "powerengine-dash"
TITLE = "Power Engine"
ICON = "mdi:lightning-bolt"
_KEY = f"{DOMAIN}_dashboard"                 # hass.data: {"mine": whether this integration registered the dashboard}


def _lovelace(hass: HomeAssistant):
    """Lovelace's data where it is running (not in recovery mode, not absent), else None."""
    try:
        from homeassistant.components.lovelace import LOVELACE_DATA
    except ImportError:
        return None
    return hass.data.get(LOVELACE_DATA)


def async_register(hass: HomeAssistant, path: Path) -> bool:
    """Add the dashboard to Home Assistant, reading `path`. True when it is there (ours or an earlier entry)."""
    state = hass.data.setdefault(_KEY, {"mine": False})
    if state["mine"]:
        return True
    lovelace = _lovelace(hass)
    if lovelace is None:
        return False
    if URL_PATH in lovelace.dashboards or URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        _LOGGER.debug("A dashboard at /%s already exists: leaving it as it is", URL_PATH)
        return True
    from homeassistant.components.lovelace.dashboard import LovelaceYAML
    config = {"mode": "yaml", "filename": str(path), "title": TITLE, "icon": ICON, "show_in_sidebar": True,
              "require_admin": False}
    try:
        frontend.async_register_built_in_panel(hass, "lovelace", sidebar_title=TITLE, sidebar_icon=ICON,
                                               frontend_url_path=URL_PATH, config={"mode": "yaml"},
                                               require_admin=False, show_in_sidebar=True)
    except ValueError:
        return True                                  # the address was taken between the check and now
    lovelace.dashboards[URL_PATH] = LovelaceYAML(hass, URL_PATH, config)
    state["mine"] = True
    _LOGGER.info("The PowerEngine dashboard is in the sidebar (%s)", path)
    return True


def async_unregister(hass: HomeAssistant) -> None:
    """Take our dashboard away again (an entry somebody else made is not ours to remove)."""
    state = hass.data.setdefault(_KEY, {"mine": False})
    if not state["mine"]:
        return
    lovelace = _lovelace(hass)
    if lovelace is not None:
        lovelace.dashboards.pop(URL_PATH, None)
    frontend.async_remove_panel(hass, URL_PATH, warn_if_unknown=False)
    state["mine"] = False
