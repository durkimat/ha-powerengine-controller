"""Supervised tests of the Solis "remote control" (RC) registers: can the inverter be driven without the
EEPROM-backed timed windows?

The SolaX Modbus integration exposes three RC entities on Solis hybrids:
  select  ..._battery_control_override              register 43135: Off / Force charge / Force discharge
  number  ..._battery_control_override_charge_power      43136 (W)
  number  ..._battery_control_override_discharge_power   43129 (W)
While the select is on a force option the integration re-sends it every poll; the inverter drops the command by
itself if it stops arriving (its RC timeout). That self-revert is the fallback a RAM-driven PowerEngine would rely
on, so one test checks it.

Tests (all start by closing the timed windows so they can't interfere, and all end with the select Off):
  rc_charge     Force charge at the given power.
  rc_discharge  Force discharge at the given power.
  rc_hold       Force charge at 0 W: does that hold the battery (neither charge nor discharge)?
  rc_failsafe   Force charge for 2 minutes, then stop the re-sending (reload SolaX Modbus) without writing Off,
                and watch whether the inverter goes back to Self-Use on its own.
"""

from __future__ import annotations

OPTION_OFF, OPTION_CHARGE, OPTION_DISCHARGE = "Off", "Force charge", "Force discharge"
RC_TESTS = {
    "rc_charge": OPTION_CHARGE,
    "rc_discharge": OPTION_DISCHARGE,
    "rc_hold": OPTION_CHARGE,
    "rc_failsafe": OPTION_CHARGE,
}
DEFAULT_POWER_W = 2000
FAILSAFE_FORCE_S = 120               # rc_failsafe: force this long before the re-sending stops
SAMPLE_S = 30
SUFFIX = {
    "rc_mode": ("select.", "battery_control_override"),
    "rc_charge_power": ("number.", "battery_control_override_charge_power"),
    "rc_discharge_power": ("number.", "battery_control_override_discharge_power"),
}


def find_entities(entity_ids) -> dict:
    """The RC entities among HA's entity ids (by name; a 'solis' one wins if there are several)."""
    found = {}
    ids = sorted(entity_ids)
    for role, (domain, tail) in SUFFIX.items():
        hits = [e for e in ids if e.startswith(domain) and e.endswith(tail)]
        hits.sort(key=lambda e: ("solis" not in e, e))
        if hits:
            found[role] = hits[0]
    return found


def missing_roles(entities: dict, test: str) -> list[str]:
    need = ["rc_mode", "rc_discharge_power" if test == "rc_discharge" else "rc_charge_power"]
    return [r for r in need if not entities.get(r)]


def power_for(test: str, power_w: float | None) -> int:
    if test == "rc_hold":
        return 0
    return int(round(power_w or DEFAULT_POWER_W))


def _charging(b: float | None, p: float) -> bool:
    """battery_w is + discharging, so charging is negative."""
    return b is not None and b <= -0.6 * p


def judge(test: str, power_w: int, samples: list[dict], stop_at: str | None = None) -> tuple[str, str]:
    """(verdict, explanation) from the samples taken while forcing ({'time', 'battery_w', ...}).
    Verdicts: 'worked', 'no effect', 'inconclusive', 'reverted', 'did not revert'."""
    bat = [s.get("battery_w") for s in samples if s.get("battery_w") is not None]
    if not bat:
        return "inconclusive", "no battery power readings"
    if test == "rc_charge":
        hits = sum(_charging(b, power_w) for b in bat)
        if hits:
            return "worked", f"battery charged at about {round(-min(bat))} W (asked {power_w} W) in {hits} of " \
                             f"{len(bat)} readings"
        return "no effect", f"battery never charged near {power_w} W (most {round(-min(bat))} W)"
    if test == "rc_discharge":
        hits = sum(b >= 0.6 * power_w for b in bat)
        if hits:
            return "worked", f"battery discharged at about {round(max(bat))} W (asked {power_w} W) in {hits} of " \
                             f"{len(bat)} readings"
        return "no effect", f"battery never discharged near {power_w} W (most {round(max(bat))} W)"
    if test == "rc_hold":
        worst = max(abs(b) for b in bat)
        if worst <= 300:
            return "worked", f"battery stayed within {round(worst)} W of zero"
        return "no effect", f"battery moved up to {round(worst)} W (a hold would keep it near zero)"
    # rc_failsafe: forcing first, then (after stop_at) back to normal on its own
    before = [s for s in samples if stop_at is None or s["time"] <= stop_at]
    after = [s for s in samples if stop_at is not None and s["time"] > stop_at]
    if not any(_charging(s.get("battery_w"), power_w) for s in before):
        return "inconclusive", "force charge never took effect, so there was nothing to revert"
    run = 0
    for s in after:
        run = run + 1 if not _charging(s.get("battery_w"), 0.4 / 0.6 * power_w) else 0
        if run >= 2:
            return "reverted", f"stopped force charging by itself by {s['time'][11:19]} UTC " \
                               f"(re-sending stopped at {stop_at[11:19]} UTC)"
    return "did not revert", "still force charging at the end of the watch; it was switched Off by the test"
