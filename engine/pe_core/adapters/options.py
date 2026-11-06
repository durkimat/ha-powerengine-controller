"""What a site can choose from, for the card's "Your system" block: `site_options()`.

Per site key, a compact list of {id, name, status, firmware_variants}. The adapters come from the registry and the
inverter definitions, so a new definition file or adapter shows up here by itself. "none" and "auto" are the
neutral choices the config also allows. `firmware_variants` is only used for inverters: the firmware versions the
definition has a variant for (a regular-expression match is shown as its pattern), and `verified_firmware` the
versions it has been proven on.
"""

from __future__ import annotations

from ..config import SITE_EXTRA, SITE_KINDS, site_choices
from . import registry
from .definition import DefinitionError, load_definition
from .vocabulary import EV_CHARGER, EVENT_SOURCE, FORECAST, SUPPLIER

_TERM = {"ev_charger": EV_CHARGER, "forecast": FORECAST, "events": EVENT_SOURCE}
_EXTRA_NAMES = {"none": "None", "auto": "Detect automatically"}


def _inverter(name: str) -> dict:
    try:
        d = load_definition(name)
    except DefinitionError:                      # a hand-written class with no definition file, or a broken file
        return {"id": name, "name": name, "status": "draft", "firmware_variants": []}
    fw = d.get("firmware") or {}
    out = {"id": name, "name": f"{d.get('brand', name)} {d.get('model', '')}".strip(),
           "status": d.get("status", "draft"),
           "firmware_variants": [str(v["match"]) for v in fw.get("variants") or []]}
    if d.get("verified_firmware"):
        out["verified_firmware"] = list(d["verified_firmware"])
    return out


def _adapter(key: str, name: str) -> dict:
    adapter = registry.get(SITE_KINDS[key], name)()
    status = getattr(adapter, "status", None)
    if key == "tariff":
        from .kraken import STATUS
        status = STATUS.get(name, "draft")
    label = adapter.display_names().get(SUPPLIER if key == "tariff" else _TERM[key], name)
    return {"id": name, "name": label, "status": status or "draft", "firmware_variants": []}


def site_options() -> dict[str, list[dict]]:
    """{site key: [{id, name, status, firmware_variants}, ...]} for every key that takes a name."""
    out = {}
    for key, allowed in site_choices().items():
        rows = []
        for name in allowed:
            if key in SITE_EXTRA and name in SITE_EXTRA[key]:
                rows.append({"id": name, "name": _EXTRA_NAMES[name], "status": "verified", "firmware_variants": []})
            elif key == "inverter":
                rows.append(_inverter(name))
            else:
                rows.append(_adapter(key, name))
        out[key] = rows
    return out


__all__ = ["site_options"]
