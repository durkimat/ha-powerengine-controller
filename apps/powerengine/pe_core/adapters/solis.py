"""The Solis inverter adapter: a `DefinedInverter` driven by `devices/solis.yml`.

Everything Solis-specific (the SolaX Modbus entity names, the three timed slots, the RAM remote-control registers,
the clock, the display names, the capabilities, firmware variants and the suggested entities) is data in that file;
`defined.py` is the generic driver. This class only picks the definition, so `SolisInverter` stays importable with
the constructor the app and the tests already use, and `("inverter", "solis")` resolves to it.

Written for the Solis S5-EH1P6K-L (via SolaX Modbus) on firmware 420044, the one it was tested on.
"""

from __future__ import annotations

from .defined import DefinedInverter, SlotContext
from .definition import load_definition
from .registry import register

_DEFINITION = load_definition("solis")


class SolisInverter(DefinedInverter):
    """Translates decisions into Solis settings. See the module docstring and `devices/solis.yml`."""

    name = _DEFINITION["name"]
    DISPLAY_NAMES = dict(_DEFINITION["display_names"])      # the class-level copy the names map starts from
    card_model = _DEFINITION["card_model"]

    def __init__(self, ha, role_entity, volts: float = 52.0, max_charge_w: float | None = None,
                 max_discharge_w: float | None = None, ram_max_w: float | None = None,
                 firmware: str | None = None):
        super().__init__(load_definition("solis", firmware), ha, role_entity, volts, max_charge_w,
                         max_discharge_w, ram_max_w)


register("inverter", "solis", SolisInverter)

__all__ = ["SolisInverter", "SlotContext"]
