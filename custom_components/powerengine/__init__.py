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
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .bridge import HassHome
from .const import (
    CONF_MODE,
    CONF_SETTINGS_DIR,
    DEFAULT_SETTINGS_FOLDER,
    DOMAIN,
    ISSUE_CRASHING,
    ISSUE_FILES,
    ISSUE_NO_SOURCE,
    ISSUE_PROTOCOL,
    MODE_SHADOW,
    PLATFORMS,
    SERVICE_REFRESH_SHADOW,
    SERVICE_RESTART_ENGINE,
    SHADOW_FOLDER,
    STATUS_NEEDS_RESTART,
)
from .frontend import async_refresh as refresh_card
from .frontend import async_register as register_card
from .frontend import async_unregister as unregister_card
from .host_server import HostServer
from .native import NativeEntities
from .protocol import new_secret
from .shadow import Ledger, clear, seed
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
    check: Callable[[], Awaitable[None]] | None = None


def signal(entry: ConfigEntry) -> str:
    return f"{DOMAIN}_{entry.entry_id}_status"


def shadow_run(entry: ConfigEntry) -> bool:
    return entry.options.get(CONF_MODE) == MODE_SHADOW


def settings_dir(hass: HomeAssistant, entry: ConfigEntry) -> Path:
    """Where the engine keeps its settings and data: the live folder, or the shadow's own copy of it."""
    folder = entry.options.get(CONF_SETTINGS_DIR) or entry.data.get(CONF_SETTINGS_DIR)
    return Path(folder or hass.config.path(SHADOW_FOLDER if shadow_run(entry) else DEFAULT_SETTINGS_FOLDER))


def settings_path(hass: HomeAssistant, entry: ConfigEntry) -> str:
    return str(settings_dir(hass, entry) / "config.yaml")


def live_dir(hass: HomeAssistant) -> Path:
    """The live PowerEngine's folder, which a shadow run copies."""
    return Path(hass.config.path(DEFAULT_SETTINGS_FOLDER))


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

    shadow = shadow_run(entry)
    folder = settings_dir(hass, entry)
    if shadow:
        if folder == live_dir(hass):
            raise ConfigEntryError("A shadow run needs a folder of its own, not the live one")
        copied = await hass.async_add_executor_job(seed, live_dir(hass), folder)
        _LOGGER.info("Shadow run: %s", f"copied {copied} files from {live_dir(hass)} to {folder}" if copied
                     else f"using {folder}")
        if await hass.async_add_executor_job((folder / "config.yaml").is_file):
            ir.async_delete_issue(hass, DOMAIN, ISSUE_NO_SOURCE)
        else:
            ir.async_create_issue(hass, DOMAIN, ISSUE_NO_SOURCE, is_fixable=False,
                                  severity=ir.IssueSeverity.WARNING, translation_key=ISSUE_NO_SOURCE,
                                  translation_placeholders={"live": str(live_dir(hass))})
    ledger = Ledger(folder / "shadow_ledger.jsonl", notify=lambda: hass.loop.call_soon_threadsafe(changed)) \
        if shadow else None
    native = NativeEntities(hass, entry)
    home = HassHome(hass, native, ledger)
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
        on_change=lambda: (changed(), _protocol_issue(hass, supervisor)), on_crashing=crashing,
        extra_args=("--shadow",) if shadow else ())
    runtime = Runtime(native, home, server, supervisor, [])
    entry.runtime_data = runtime
    await hass.async_add_executor_job(supervisor.refresh_installed)

    async def check(now: datetime | None = None) -> None:
        """Every minute: start the engine if it is not started; restart it when an update replaced its files."""
        await hass.async_add_executor_job(supervisor.refresh_installed)     # (a file read: not on the event loop)
        await refresh_card(hass)                    # an update may have replaced the card: a new address for it
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

    async def refresh_shadow(call: ServiceCall) -> None:
        """Copy the live folder afresh (the shadow starts again from what the live PowerEngine has now)."""
        if not shadow:
            raise ServiceValidationError("Only a shadow run has a copy to refresh")
        await supervisor.stop()
        await hass.async_add_executor_job(clear, folder)
        await hass.async_add_executor_job(seed, live_dir(hass), folder)
        supervisor.start()

    if not hass.services.has_service(DOMAIN, SERVICE_RESTART_ENGINE):
        hass.services.async_register(DOMAIN, SERVICE_RESTART_ENGINE, restart_engine, schema=vol.Schema({}))
    if shadow and not hass.services.has_service(DOMAIN, SERVICE_REFRESH_SHADOW):
        hass.services.async_register(DOMAIN, SERVICE_REFRESH_SHADOW, refresh_shadow, schema=vol.Schema({}))
    entry.async_on_unload(entry.add_update_listener(_reload))

    async def on_stop(event: Event) -> None:
        await supervisor.stop()
    runtime.unsub.append(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_stop))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if not shadow:
        await register_card(hass)                   # the card is part of PowerEngine: the integration serves it
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
    for issue in (ISSUE_PROTOCOL, ISSUE_CRASHING, ISSUE_NO_SOURCE):
        ir.async_delete_issue(hass, DOMAIN, issue)
    if not shadow_run(entry):
        unregister_card(hass)
    if not hass.config_entries.async_loaded_entries(DOMAIN):
        for service in (SERVICE_RESTART_ENGINE, SERVICE_REFRESH_SHADOW):
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
    return ok


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The settings changed (the mode, the folder): start again with them."""
    await hass.config_entries.async_reload(entry.entry_id)
