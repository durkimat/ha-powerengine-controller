"""The setup wizard's "candidate entities" export: the file someone sends when their device isn't supported yet.

The card writes it (`buildCandidateExport` in ha-powerengine-card.js) from Home Assistant's own device and entity
registries and current states, before PowerEngine is configured. It is for whoever writes the inverter's definition
(docs/INVERTERS.md): which integration, which device (manufacturer, model, firmware), and every entity of the
chosen devices with its unit, class, current value and, for selects and numbers, its options and limits.

This module only checks a file and summarises it (`tools/candidates_summary.py`); nothing in the app reads one.
Format (version 1):

    {"format": "powerengine-candidates", "version": 1, "generated": ISO time, "card_version": ..., "app_version": ...,
     "ha_version": ..., "chosen": {part: id | "unlisted" | "none"}, "note": text,
     "devices": [{"key": "d1", "integration": domain, "manufacturer": ..., "model": ..., "sw_version": ...}],
     "entities": [{"id": ..., "domain": ..., "platform": ..., "device": "d1" | null, "unit": ..., "device_class": ...,
                   "state_class": ..., "state": text (at most 60 characters), "options": [...], "min": ..., "max": ...,
                   "step": ..., "attributes": [names only]}]}

Scrubbing: long digit runs in ids and states (meter and account numbers, serials) become `<n>` in the card (not in a
device's manufacturer, model and firmware: 420044 is what a definition needs); text that looks like an email address
or a postcode is dropped. `unscrubbed` is the second check on a received file.
"""

from __future__ import annotations

import re

FORMAT = "powerengine-candidates"
VERSION = 1
PARTS = ("inverter", "tariff", "ev_charger", "forecast", "events")
ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_<>]+$")
DIGITS = re.compile(r"\d{6,}")                              # an account, meter or serial number
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b", re.I)
MAX_STATE = 60


def problems(data) -> list[str]:
    """What is wrong with the file's shape, in plain words (empty when it is a usable export)."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return [f"not a {FORMAT} file"]
    out = []
    if data.get("version") != VERSION:
        out.append(f"version {data.get('version')!r}, expected {VERSION}")
    devices, entities = data.get("devices"), data.get("entities")
    if not isinstance(devices, list):
        out.append("'devices' must be a list")
        devices = []
    if not isinstance(entities, list):
        out.append("'entities' must be a list")
        entities = []
    keys = {d.get("key") for d in devices if isinstance(d, dict)}
    for e in entities:
        if not isinstance(e, dict) or not ENTITY_ID.match(str(e.get("id", ""))):
            out.append(f"entity with a bad id: {e.get('id') if isinstance(e, dict) else e!r}")
        elif e.get("device") is not None and e["device"] not in keys:
            out.append(f"{e['id']}: device {e['device']!r} is not in 'devices'")
    chosen = data.get("chosen") or {}
    out += [f"chosen has an unknown part {p!r}" for p in chosen if p not in PARTS]
    return out


def unscrubbed(data) -> list[str]:
    """Places where something that should have been scrubbed is still there (digits, emails, postcodes)."""
    found = []

    def look(where: str, text, digits: bool = True) -> None:
        text = str(text)
        for name, rx in (("a long number", DIGITS), ("an email address", EMAIL), ("a postcode", POSTCODE)):
            if (digits or rx is not DIGITS) and rx.search(text):
                found.append(f"{where} has {name}")

    for d in (data.get("devices") or []):
        for key in ("manufacturer", "model", "sw_version"):          # a firmware such as 420044 keeps its digits
            look(f"device {d.get('key')} {key}", d.get(key, ""), digits=False)
    for e in (data.get("entities") or []):
        for key in ("id", "state", "unit"):
            look(f"{e.get('id')} {key}", e.get(key, ""))
        if len(str(e.get("state", ""))) > MAX_STATE:
            found.append(f"{e.get('id')} state is longer than {MAX_STATE} characters")
    look("note", data.get("note", ""))
    return found


def summary(data) -> str:
    """A short text for a person reading the export: what was chosen, then each device and its entities by domain."""
    lines = [f"PowerEngine candidates, generated {data.get('generated', '?')} (card {data.get('card_version', '?')}, "
             f"app {data.get('app_version', '?')}, HA {data.get('ha_version', '?')})"]
    chosen = data.get("chosen") or {}
    if chosen:
        lines.append("Chosen: " + ", ".join(f"{p}={chosen[p]}" for p in PARTS if p in chosen))
    if data.get("note"):
        lines.append(f"Note: {data['note']}")
    by_device: dict = {}
    for e in data.get("entities") or []:
        by_device.setdefault(e.get("device"), []).append(e)
    for d in data.get("devices") or []:
        ents = by_device.pop(d.get("key"), [])
        lines.append(f"\n{d.get('manufacturer') or '?'} {d.get('model') or '?'}  "
                     f"[{d.get('integration') or '?'}]  firmware {d.get('sw_version') or '?'}  ({len(ents)} entities)")
        lines += [_entity_line(e) for e in sorted(ents, key=lambda x: x.get("id", ""))]
    loose = by_device.pop(None, [])
    if loose:
        lines.append(f"\nOther entities, matched by name ({len(loose)})")
        lines += [_entity_line(e) for e in sorted(loose, key=lambda x: x.get("id", ""))]
    return "\n".join(lines)


def _entity_line(e: dict) -> str:
    extra = []
    if e.get("unit"):
        extra.append(e["unit"])
    if e.get("device_class"):
        extra.append(e["device_class"])
    if e.get("options"):
        extra.append("options: " + ", ".join(map(str, e["options"])))
    if e.get("min") is not None or e.get("max") is not None:
        extra.append(f"range {e.get('min')}..{e.get('max')}")
    return f"  {e.get('id')} = {e.get('state', '')}" + (f"  ({'; '.join(extra)})" if extra else "")


__all__ = ["FORMAT", "VERSION", "PARTS", "problems", "unscrubbed", "summary"]
