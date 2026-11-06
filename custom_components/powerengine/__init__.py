"""PowerEngine: an energy controller for a solar, battery and EV home, as a Home Assistant integration.

The integration is thin and rarely changes. The engine that makes the decisions is a separate process, started and
watched from here, so that updating PowerEngine restarts only that process and not Home Assistant
(docs/plans/native-integration.md). The two talk over a local connection (protocol.py); the engine holds no Home
Assistant token, and this side reads and acts for it (bridge.py).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .bridge import HassHome
from .const import (
    CONF_SETTINGS_DIR,
    DEFAULT_SETTINGS_FOLDER,
    DOMAIN,
    ISSUE_CRASHING,
    ISSUE_FILES,
    ISSUE_PROTOCOL,
    PLATFORMS,
    SERVICE_RESTART_ENGINE,
    SERVICE_UPDATE,
    STATUS_NEEDS_RESTART,
)
from .dashboard import async_register as register_dashboard
from .dashboard import async_unregister as unregister_dashboard
from .frontend import async_refresh as refresh_card
from .frontend import async_register as register_card
from .frontend import async_unregister as unregister_card
from .host_server import HostServer
from .native import NativeEntities
from .protocol import new_secret
from .supervisor import EngineSupervisor
from .updater import async_update
from .watchdog import Watchdog

_LOGGER = logging.getLogger(__name__)
ENGINE_DIR = Path(__file__).parent / "engine"
CHECK_EVERY = timedelta(minutes=1)


@dataclass
class Runtime:
    native: NativeEntities
    home: HassHome
    server: HostServer
    supervisor: EngineSupervisor
    unsub: list
    check: Callable[[], Awaitable[None]] | None = None
    watchdog: Watchdog | None = None


def signal(entry: ConfigEntry) -> str:
    return f"{DOMAIN}_{entry.entry_id}_status"


def settings_dir(hass: HomeAssistant, entry: ConfigEntry) -> Path:
    """Where the engine keeps its settings and data."""
    folder = entry.options.get(CONF_SETTINGS_DIR) or entry.data.get(CONF_SETTINGS_DIR)
    return Path(folder or hass.config.path(DEFAULT_SETTINGS_FOLDER))


def settings_path(hass: HomeAssistant, entry: ConfigEntry) -> str:
    return str(settings_dir(hass, entry) / "config.yaml")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.async_add_executor_job((ENGINE_DIR / "engine_main.py").is_file):
        ir.async_create_issue(hass, DOMAIN, ISSUE_FILES, is_fixable=False, severity=ir.IssueSeverity.ERROR,
                              translation_key=ISSUE_FILES)
        raise ConfigEntryError("The engine's files are missing: reinstall PowerEngine in HACS")
    ir.async_delete_issue(hass, DOMAIN, ISSUE_FILES)

    @callback
    def changed() -> None:
        async_dispatcher_send(hass, signal(entry))

    @callback
    def engine_moved(info: dict | None) -> None:
        if info is not None:
            supervisor.connected()
        changed()

    folder = settings_dir(hass, entry)
    native = NativeEntities(hass, entry)
    watchdog = Watchdog(hass, "sensor.pe_diag_heartbeat")
    await watchdog.async_load()
    home = HassHome(hass, native, watchdog)
    secret = new_secret()
    server = HostServer(home, secret, on_watch=home.watch, on_listen=home.listen,
                        on_engine=lambda info: hass.loop.call_soon_threadsafe(engine_moved, info))
    home.server = server
    address = await hass.async_add_executor_job(server.start)

    def create_issue(key: str, severity: ir.IssueSeverity = ir.IssueSeverity.ERROR) -> None:
        ir.async_create_issue(hass, DOMAIN, key, is_fixable=False, severity=severity, translation_key=key)

    @callback
    def crashing(is_crashing: bool) -> None:
        if is_crashing:
            create_issue(ISSUE_CRASHING)
        else:
            ir.async_delete_issue(hass, DOMAIN, ISSUE_CRASHING)

    supervisor = EngineSupervisor(
        ENGINE_DIR, settings_path(hass, entry), lambda: address, secret,
        running_version=lambda: (server.engine or {}).get("engine"), ask_stop=server.ask_to_stop,
        on_change=lambda: (changed(), _protocol_issue(hass, supervisor)), on_crashing=crashing)
    runtime = Runtime(native, home, server, supervisor, [], watchdog=watchdog)
    entry.runtime_data = runtime
    await hass.async_add_executor_job(supervisor.refresh_installed)

    async def check(now: datetime | None = None) -> None:
        """Every minute: start the engine if it is not started; restart it when an update replaced its files."""
        await hass.async_add_executor_job(supervisor.refresh_installed)     # (a file read: not on the event loop)
        await refresh_card(hass)                    # an update may have replaced the card: a new address for it
        register_dashboard(hass, folder / "dashboard.yaml")          # (the dashboard that uses the card: added once)
        await watchdog.check()                      # the inverter back to Self-Use if the engine has gone quiet
        if not supervisor.started:
            supervisor.start()
        elif supervisor.update_needed():
            _LOGGER.info("PowerEngine %s is installed and %s is running: restarting the engine", supervisor.installed,
                         supervisor.running)
            await supervisor.restart()

    runtime.unsub.append(async_track_time_interval(hass, check, CHECK_EVERY))
    runtime.check = check

    async def restart_engine(call: ServiceCall) -> None:
        await supervisor.restart()

    async def update(call: ServiceCall) -> None:
        await async_update(hass, runtime)

    if not hass.services.has_service(DOMAIN, SERVICE_RESTART_ENGINE):
        hass.services.async_register(DOMAIN, SERVICE_RESTART_ENGINE, restart_engine, schema=vol.Schema({}))
    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE):
        hass.services.async_register(DOMAIN, SERVICE_UPDATE, update, schema=vol.Schema({}))
    entry.async_on_unload(entry.add_update_listener(_reload))

    async def on_stop(event: Event) -> None:
        await supervisor.stop()
    runtime.unsub.append(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_stop))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await register_card(hass)                       # the card is part of PowerEngine: the integration serves it
    await check()
    return True


def _protocol_issue(hass: HomeAssistant, supervisor: EngineSupervisor) -> None:
    if supervisor.status == STATUS_NEEDS_RESTART:
        ir.async_create_issue(hass, DOMAIN, ISSUE_PROTOCOL, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                              translation_key=ISSUE_PROTOCOL)
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_PROTOCOL)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    runtime: Runtime = entry.runtime_data
    for unsub in runtime.unsub:
        unsub()
    await runtime.supervisor.stop()
    runtime.home.close()
    await hass.async_add_executor_job(runtime.server.stop)
    for issue in (ISSUE_PROTOCOL, ISSUE_CRASHING):
        ir.async_delete_issue(hass, DOMAIN, issue)
    await unregister_card(hass)
    unregister_dashboard(hass)
    if not hass.config_entries.async_loaded_entries(DOMAIN):
        for service in (SERVICE_RESTART_ENGINE, SERVICE_UPDATE):
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
    return ok


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The settings changed (the folder): start again with them."""
    await hass.config_entries.async_reload(entry.entry_id)
