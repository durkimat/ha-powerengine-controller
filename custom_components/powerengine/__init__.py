"""PowerEngine: an energy controller for a solar, battery and EV home, as a Home Assistant integration.

The integration is thin and rarely changes. The engine that makes the decisions is a separate process, started and
watched from here, so that updating PowerEngine restarts only that process and not Home Assistant
(docs/plans/native-integration.md). The two talk over a local connection (protocol.py); the engine holds no Home
Assistant token, and this side reads and acts for it (bridge.py).
"""

from __future__ import annotations

import logging
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
from homeassistant.util import dt as dt_util

from .bridge import HassHome
from .const import (
    CONF_SETTINGS_DIR,
    DEFAULT_SETTINGS_FOLDER,
    DOMAIN,
    HEARTBEAT_ENTITY,
    ISSUE_CRASHING,
    ISSUE_FILES,
    ISSUE_PROTOCOL,
    ISSUE_RUNNING_ELSEWHERE,
    PLATFORMS,
    RUNNING_ELSEWHERE_WITHIN_S,
    SERVICE_RESTART_ENGINE,
    STATUS_NEEDS_RESTART,
    STATUS_WAITING,
)
from .host_server import HostServer
from .native import NativeEntities
from .protocol import new_secret
from .supervisor import EngineSupervisor

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


def signal(entry: ConfigEntry) -> str:
    return f"{DOMAIN}_{entry.entry_id}_status"


def settings_path(hass: HomeAssistant, entry: ConfigEntry) -> str:
    folder = entry.options.get(CONF_SETTINGS_DIR) or entry.data.get(CONF_SETTINGS_DIR)
    return str(Path(folder or hass.config.path(DEFAULT_SETTINGS_FOLDER)) / "config.yaml")


def running_elsewhere(hass: HomeAssistant) -> bool:
    """Is another PowerEngine (the AppDaemon app) beating? Two would both control the inverter."""
    state = hass.states.get(HEARTBEAT_ENTITY)
    if state is None or state.state in ("unavailable", "unknown"):
        return False
    return (dt_util.utcnow() - state.last_updated).total_seconds() < RUNNING_ELSEWHERE_WITHIN_S


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not (ENGINE_DIR / "engine_main.py").is_file():
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

    native = NativeEntities(hass, entry)
    home = HassHome(hass, native)
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
    runtime = Runtime(native, home, server, supervisor, [])
    entry.runtime_data = runtime

    async def check(now: datetime | None = None) -> None:
        """Every minute: start the engine once nothing else is running PowerEngine; restart it when an update replaced
        its files."""
        if not supervisor.started:
            if running_elsewhere(hass):
                if supervisor.status != STATUS_WAITING:
                    supervisor.wait_for_other()
                    create_issue(ISSUE_RUNNING_ELSEWHERE, ir.IssueSeverity.WARNING)
                return
            ir.async_delete_issue(hass, DOMAIN, ISSUE_RUNNING_ELSEWHERE)
            supervisor.start()
        elif supervisor.update_needed():
            _LOGGER.info("PowerEngine %s is installed and %s is running: restarting the engine", supervisor.installed,
                         supervisor.running)
            await supervisor.restart()

    runtime.unsub.append(async_track_time_interval(hass, check, CHECK_EVERY))

    async def restart_engine(call: ServiceCall) -> None:
        await supervisor.restart()

    if not hass.services.has_service(DOMAIN, SERVICE_RESTART_ENGINE):
        hass.services.async_register(DOMAIN, SERVICE_RESTART_ENGINE, restart_engine, schema=vol.Schema({}))

    async def on_stop(event: Event) -> None:
        await supervisor.stop()
    runtime.unsub.append(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_stop))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
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
    for issue in (ISSUE_PROTOCOL, ISSUE_RUNNING_ELSEWHERE, ISSUE_CRASHING):
        ir.async_delete_issue(hass, DOMAIN, issue)
    if not hass.config_entries.async_loaded_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_RESTART_ENGINE)
    return ok
