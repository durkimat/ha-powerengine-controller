"""The myenergi Zappi adapter: the car charger's plug status, charger status, mode and session energy, in neutral terms.

`read` and the two classifiers are pure. `read` takes `state`, a function `role -> {"state": ..., "attributes": {...}}
| None` (the accessor `pe_core.readings.read()` builds) and returns the raw fields `Readings` stores. `EVAdapter.state`
wraps them over the `HomeAssistant` door, using the entity mapped to each role.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..parsing import State, _energy_kwh, _power_w
from .base import EVState, HomeAssistant
from .registry import register
from .vocabulary import EV_CHARGER

StateFn = Callable[[str], State | None]

CHARGING, PLUGGED_IN, UNPLUGGED = "charging", "plugged_in", "unplugged"


class ZappiCharger:
    """Reads a myenergi Zappi. See module docstring."""

    name = "zappi"
    status = "verified"

    def __init__(self, role_entity: Callable[[str], str | None] | None = None):
        self.role_entity = role_entity

    def display_names(self) -> dict[str, str]:
        return {EV_CHARGER: "Zappi"}

    def read(self, state: StateFn) -> dict[str, Any]:
        """The raw charger fields: plug (text), status (text), mode (text), session_kwh and power_w."""
        return {"power_w": _power_w(state("ev_charge_power")),
                "plug": (state("ev_plug_status") or {}).get("state"),
                "status": (state("ev_charger_status") or {}).get("state"),
                "mode": (state("ev_charge_mode") or {}).get("state"),
                "session_kwh": _energy_kwh(state("ev_session_energy"))}

    @staticmethod
    def classify(plug: str | None, power_w: float | None) -> str:
        """'charging' | 'plugged_in' | 'unplugged' from the plug status.

        The plug status reads 'Charging' exactly while the car draws power, so it is the source of truth. Charging
        power is only a fallback for when the plug status is unmapped or unavailable.
        """
        text = (plug or "").strip().lower()
        if text in ("", "unknown", "unavailable"):
            return CHARGING if (power_w or 0) > 100 else UNPLUGGED
        if text == "charging":
            return CHARGING
        if "disconnect" in text:
            return UNPLUGGED
        return PLUGGED_IN

    @staticmethod
    def complete(plug_state: str, status: str | None) -> bool:
        """The charger reports the charge complete (car full) while the car is plugged in and not charging."""
        return plug_state == PLUGGED_IN and "complet" in str(status or "").lower()

    def state(self, ha: HomeAssistant) -> EVState:
        if self.role_entity is None:
            raise RuntimeError("ZappiCharger needs a role_entity function to read through Home Assistant")

        def get(role: str) -> State | None:
            eid = self.role_entity(role)
            return ha.get_state(eid, attribute="all") if eid else None

        raw = self.read(get)
        plug_state = self.classify(raw["plug"], raw["power_w"])
        return EVState(plugged=plug_state != UNPLUGGED, charging=plug_state == CHARGING,
                       complete=self.complete(plug_state, raw["status"]), power_w=raw["power_w"])


register("ev", "zappi", ZappiCharger)
