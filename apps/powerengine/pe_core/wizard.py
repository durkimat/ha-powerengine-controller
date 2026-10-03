"""What the card's setup wizard needs from the app: the parts of a home, which are optional, how each is recognised
in Home Assistant, and which inputs (roles) belong to which part. Published as the `wizard` attribute of
`sensor.pe_diag_version` (compact: about 3 KB, the sensor's attributes must stay under 16 KB).

The wizard itself runs in the card, because only the card can see Home Assistant's entity and device registries. The
app only says what to look for. It is all data: the adapters' detection table (`adapters/detect.py`), the inverter
definitions' `detect:` blocks, the registry, and the role catalogue. Adding a definition file adds an option here.
"""

from __future__ import annotations

from .adapters import registry
from .adapters.definition import DefinitionError, detect_info
from .adapters.detect import LISTS, adapter_detect
from .config import SITE_EXTRA, SITE_KINDS
from .roles import ROLES

FORMAT = 1

# The parts of a home, in the order the wizard asks about them: (site key, title, what it is for, required).
# Required parts must be set up in Home Assistant before the wizard can finish; the others can be skipped (the site
# key is then "none", and the app leaves that part out: see docs/SITE.md, "What none does").
PARTS = (
    ("inverter", "Inverter and battery", "The hybrid inverter PowerEngine plans and (later) controls, with its battery "
     "and the grid meter. Without it there is nothing to manage.", True),
    ("tariff", "Electricity tariff", "Half-hourly import and export prices, and any smart-charge slots from your "
     "supplier. The plan is built on them.", True),
    ("ev_charger", "Car charger", "Lets PowerEngine keep the car's charging out of the house load and work with "
     "smart-charge slots. Skip it if you have no car charger.", False),
    ("forecast", "Solar forecast", "Tomorrow's sun, so the battery is not filled from the grid when solar will do it. "
     "Skip it and PowerEngine plans as if no sun is coming.", False),
    ("events", "Grid events", "Paid export events from an aggregator. Skip it if you are not signed up to one.", False),
)

# Which part each role belongs to. By catalogue group; a few roles sit in another group but come from a different
# part (the check meter is the charger's; the smart-charge request entities are the tariff's). Handover guards belong
# to no part: they are left to the config page.
GROUP_PART = {"battery": "inverter", "grid": "inverter", "controls": "inverter", "solar": "forecast",
              "tariff": "tariff", "smart": "tariff", "free": "tariff", "ev": "ev_charger", "axle": "events"}
ROLE_PART = {"smart_target_soc": "tariff", "smart_target_time": "tariff", "grid_power_reference": "ev_charger",
             "grid_import_today_check": "ev_charger", "grid_export_today_check": "ev_charger"}


def role_part(role) -> str | None:
    """The part (site key) a role belongs to, or None for roles the wizard leaves to the config page."""
    return ROLE_PART.get(role.key) or GROUP_PART.get(role.group)


def _option(part: str, name: str) -> dict:
    if part == "inverter":
        try:
            info = detect_info(name) or {}
        except DefinitionError:
            info = {}
    else:
        info = adapter_detect(part, name) or {}
    out = {"id": name}
    if info.get("integration"):
        out["integration"] = dict(info["integration"])
    for key in LISTS:
        if info.get(key):
            out[key] = list(info[key])
    return out


def wizard_info() -> dict:
    """{"v", "parts": [{"part", "title", "why", "required", "skip", "options", "roles"}, ...]}."""
    by_part: dict[str, list[str]] = {p[0]: [] for p in PARTS}
    for role in ROLES:
        part = role_part(role)
        if part in by_part:
            by_part[part].append(role.key)
    parts = []
    for part, title, why, required in PARTS:
        extra = SITE_EXTRA.get(part, ())
        names = [n for n in registry.names(SITE_KINDS[part]) if n not in extra]
        row = {"part": part, "title": title, "why": why, "required": required,
               "options": [_option(part, n) for n in names], "roles": by_part[part]}
        if not required:
            row["skip"] = "none"
        parts.append(row)
    return {"v": FORMAT, "parts": parts}


__all__ = ["PARTS", "FORMAT", "role_part", "wizard_info"]
