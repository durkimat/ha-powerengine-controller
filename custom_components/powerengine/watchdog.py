"""The inverter's watchdog: hands the inverter back to Self-Use if the engine goes quiet while it is in control.

PowerEngine programs the inverter ahead (timed windows, or a remote-control command that is re-sent). If the engine
stops, those stay as they were. The engine therefore tells this side, ahead of time and whenever it changes, what to do
(protocol.py, `failsafe`: service calls it built from the inverter it drives, and whether it is in control now); the
integration, which outlives the engine, keeps that (it is saved, so it survives a restart of Home Assistant) and, if
the engine's heartbeat has not been seen for 15 minutes while it is armed, makes the calls once and says so.

Nothing here knows an inverter: it makes the calls the engine gave it. A number already at the value asked is left
alone; selects and the update buttons are always sent (the window values are kept in Home Assistant until a button
sends them to the inverter).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
STORE_VERSION = 1
QUIET_FOR = timedelta(minutes=15)           # no heartbeat for this long while armed: act
FRESH_WITHIN = timedelta(minutes=5)         # a heartbeat this recent: the engine is alive
MAX_WAIT_S = 30
NOTIFICATION = f"{DOMAIN}_watchdog"


class Watchdog:
    def __init__(self, hass: HomeAssistant, heartbeat_entity: str) -> None:
        self.hass = hass
        self.heartbeat_entity = heartbeat_entity
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}_failsafe")
        self.calls: list[dict] = []
        self.armed = False
        self.acted = False                  # made the calls for this silence already (once per silence)
        self.last_alive = dt_util.utcnow()  # a start of Home Assistant counts as a sign of life: 15 minutes' grace

    async def async_load(self) -> None:
        saved = await self._store.async_load() or {}
        self.calls = list(saved.get("calls") or [])
        self.armed = bool(saved.get("armed"))

    @callback
    def set(self, calls: list, armed: bool) -> None:
        """What the engine said to do, and whether it is in control now."""
        if (calls, armed) == (self.calls, self.armed):
            return
        self.calls, self.armed = calls, armed
        self._store.async_delay_save(lambda: {"calls": self.calls, "armed": self.armed}, 1)

    def _alive(self, now: datetime) -> bool:
        state = self.hass.states.get(self.heartbeat_entity)
        beat = dt_util.parse_datetime(state.state) if state is not None else None
        return beat is not None and now - dt_util.as_utc(beat) <= FRESH_WITHIN

    async def check(self, now: datetime | None = None) -> None:
        """Every minute."""
        now = now or dt_util.utcnow()
        if self._alive(now):
            self.last_alive, self.acted = now, False
            return
        if self.armed and self.calls and not self.acted and now - self.last_alive >= QUIET_FOR:
            self.acted = True
            await self.act()

    async def act(self) -> None:
        """Make the engine's failsafe calls (each on its own: one that fails does not stop the rest)."""
        _LOGGER.warning("The PowerEngine engine has been quiet for %d minutes while in control: handing the inverter "
                        "back to Self-Use", QUIET_FOR.total_seconds() // 60)
        sent, failed, waited = 0, 0, False
        for step in self.calls:
            try:
                if "wait_s" in step:
                    if sent and not waited:
                        await asyncio.sleep(min(float(step["wait_s"]), MAX_WAIT_S))
                    waited = True
                    continue
                domain, _, service = str(step["service"]).partition("/")
                data = dict(step.get("data") or {})
                if self._already(domain, service, data):
                    continue
                await self.hass.services.async_call(domain, service, data, blocking=True)
                sent += 1
            except Exception as err:                    # (an entity gone, the inverter not answering)
                failed += 1
                _LOGGER.warning("Failsafe call %s failed: %r", step, err)
        persistent_notification.async_create(
            self.hass, "PowerEngine stopped updating for 15 minutes, so the inverter was handed back to Self-Use "
            f"({sent} change{'s' if sent != 1 else ''} made{f', {failed} failed' if failed else ''}). Check "
            "PowerEngine's Engine sensor and Repairs, then resume.", title="PowerEngine watchdog",
            notification_id=NOTIFICATION)

    def _already(self, domain: str, service: str, data: dict) -> bool:
        """A number already at the value asked for needs no call."""
        if (domain, service) != ("number", "set_value"):
            return False
        state = self.hass.states.get(str(data.get("entity_id")))
        try:
            return state is not None and float(state.state) == float(data["value"])
        except (TypeError, ValueError, KeyError):
            return False
