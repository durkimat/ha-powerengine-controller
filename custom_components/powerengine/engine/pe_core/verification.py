"""Whether Active may be used with the inverter the site names.

Active writes to the inverter, so it is allowed only on a definition that a person has proven on real hardware: its
`status` must be `verified`, and when it lists `verified_firmware` the firmware that applies (the site's, else the
definition's default) must be one of them. Anything else stays Passive, with the reason in plain words. A definition
that can't be loaded can't be vouched for, so it is refused too."""

from __future__ import annotations

from pe_core.adapters.definition import load_definition
from pe_core.names import N


def active_refusal(inverter: str, firmware: str | None) -> str | None:
    """Why Active is not allowed for this inverter and firmware, or None when it is."""
    try:
        d = load_definition(inverter, firmware)
    except Exception:
        return f"the {N('inverter')} definition could not be loaded, so it cannot be vouched for"
    status = d.get("status", "draft")
    if status != "verified":
        return (f"the {N('inverter')} setup is {status}, not yet verified on real hardware; "
                "run the supervised tests and report the result first")
    verified = list(d.get("verified_firmware") or [])
    fw = firmware or (d.get("firmware") or {}).get("default")
    if verified and fw not in verified:
        return (f"{N('inverter')} firmware {fw or 'unknown'} has not been verified (verified: "
                f"{', '.join(verified)}); run the supervised tests and report the result first")
    return None
