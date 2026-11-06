"""How the setup wizard recognises each part of a home in Home Assistant, as data.

The wizard (the card's `powerengine-wizard-card`) searches Home Assistant for what each adapter reads: which
integration (the entity registry's `platform`) provides it, the device's manufacturer and model, and the entity ids
it usually creates. That lives in `integrations/*.yaml` for the tariff, car charger, forecast and grid-event adapters
(integration_data.py reads it), and an inverter definition carries the same block as its own `detect:` section
(`devices/<name>.yaml`), so a new part is found by adding a file. This module is the table the wizard reads.

Every entry is {"integration": {"name", "url"?}, "domains": [...], "manufacturers": [...], "models": [...],
"entities": [...]}. `domains` are Home Assistant integration domains, the others are regular expressions (case
insensitive for manufacturers and models). A part is "found" when any one of them matches. The `url` is only given
where it is known; the wizard shows the name alone otherwise.

Nothing here is read by the control code: it never changes what PowerEngine does, only what the wizard suggests.
"""

from __future__ import annotations

from .integration_data import KEYS, LISTS, detect_table

DETECT: dict[str, dict[str, dict]] = detect_table()


def adapter_detect(part: str, name: str) -> dict | None:
    """The detection entry for a part other than the inverter (by site key), or None if there is none."""
    return DETECT.get(part, {}).get(name)


__all__ = ["DETECT", "KEYS", "LISTS", "adapter_detect"]
