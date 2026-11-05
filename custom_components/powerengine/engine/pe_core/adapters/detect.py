"""How the setup wizard recognises each part of a home in Home Assistant, as data.

The wizard (the card's `powerengine-wizard-card`) searches Home Assistant for what each adapter reads: which
integration (the entity registry's `platform`) provides it, the device's manufacturer and model, and the entity ids
it usually creates. This table holds that for the tariff, car charger, forecast and grid-event adapters. An inverter
definition carries the same block as its own `detect:` section (`devices/<name>.yml`), so a new inverter is found by
adding a file.

Every entry is {"integration": {"name", "url"?}, "domains": [...], "manufacturers": [...], "models": [...],
"entities": [...]}. `domains` are Home Assistant integration domains, the others are regular expressions (case
insensitive for manufacturers and models). A part is "found" when any one of them matches. The `url` is only given
where it is known; the wizard shows the name alone otherwise.

Nothing here is read by the control code: it never changes what PowerEngine does, only what the wizard suggests.
"""

from __future__ import annotations

# The Octopus Energy integration (BottlecapDave's) also serves EDF, which is why both suppliers name it; see kraken.py.
_KRAKEN = {"name": "Octopus Energy", "url": "https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy"}

DETECT: dict[str, dict[str, dict]] = {
    "tariff": {
        "edf": {"integration": _KRAKEN, "domains": ["edf_energy", "octopus_energy"], "manufacturers": [], "models": [],
                "entities": [r"^sensor\.edf_energy_electricity_"]},
        "octopus": {"integration": _KRAKEN, "domains": ["octopus_energy"], "manufacturers": [r"octopus"], "models": [],
                    "entities": [r"^sensor\.octopus_energy_electricity_"]},
    },
    "ev_charger": {
        "zappi": {"integration": {"name": "myenergi"},
                  "domains": ["myenergi"], "manufacturers": [r"myenergi"], "models": [r"zappi"],
                  "entities": [r"^sensor\.myenergi_zappi_"]},
    },
    "forecast": {
        "solcast": {"integration": {"name": "Solcast PV Forecast", "url": "https://github.com/BJReplay/ha-solcast-solar"},
                    "domains": ["solcast_solar"], "manufacturers": [r"solcast"], "models": [],
                    "entities": [r"^sensor\.solcast_pv_forecast_"]},
    },
    "events": {
        "axle": {"integration": {"name": "Axle"}, "domains": ["axle", "axle_vpp"], "manufacturers": [r"axle"],
                 "models": [], "entities": [r"^sensor\.axle_vpp_"]},
    },
}

KEYS = ("integration", "domains", "manufacturers", "models", "entities")
LISTS = ("domains", "manufacturers", "models", "entities")


def adapter_detect(part: str, name: str) -> dict | None:
    """The detection entry for a part other than the inverter (by site key), or None if the table has none."""
    return DETECT.get(part, {}).get(name)


__all__ = ["DETECT", "KEYS", "LISTS", "adapter_detect"]
