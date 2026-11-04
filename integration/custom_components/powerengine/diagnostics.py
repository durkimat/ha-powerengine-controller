"""Download diagnostics: the engine's status, versions and the last lines it printed (no secret, no settings)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import Runtime, settings_path


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    runtime: Runtime = entry.runtime_data
    supervisor = runtime.supervisor
    return {
        "status": supervisor.status,
        "installed_version": supervisor.installed,
        "running_version": supervisor.running,
        "update_pending": supervisor.update_needed(),
        "restarts": supervisor.restarts,
        "last_exit_status": supervisor.exit_code,
        "settings_file": settings_path(hass, entry),
        "engine_log": list(supervisor.log),
    }
