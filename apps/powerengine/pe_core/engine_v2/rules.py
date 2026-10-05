"""Layer 4: rules and limits. Which modes are allowed or forced, and the floors and caps that go with them.

One core answers for "now" (`limits_now`, from the live readings) and for every future segment (`limits_for`, from the
forecast), so the plan (layer 3) and the live decision (layer 5) can never disagree about the rules
(docs/plans/engine-v2.md, section 7). In order, the first that applies wins:

  grid event in progress   forced EVENT, down to the battery's hard floor plus a margin (not the owner's reserve)
  manual override          forced to its mode (Self-use and Export keep the reserve; Charge stops at the ceiling)
  free power               forced FREE (charge to 100%)
  car charging             only Hold and Charge: the battery never feeds the car
  level at the reserve     no discharge mode (every one of them, except a grid event)
  otherwise                Self-use, Hold, Charge, and Export when arbitrage is on

Active, Passive and Pause are not rules here: v2 always decides, and `Situation.active` says whether it is sent.
"""

from __future__ import annotations

from ..override import describe
from .settings import V2Settings
from .types import (
    CHARGE,
    CHOICE_MODES,
    EVENT,
    EXPORT,
    FREE,
    HOLD,
    SELF_USE,
    BatteryFacts,
    Limits,
    Observation,
    Segment,
    Situation,
)

# v1's override modes (decide.py action words) -> v2 modes
OVERRIDE_MODE = {"self_use": SELF_USE, "hold": HOLD, "grid_charge": CHARGE, "export": EXPORT}


def event_floor(facts: BatteryFacts, settings: V2Settings) -> float:
    """Where a grid event stops: the hard floor plus the margin, so the battery's own cut-off never ends it."""
    return min(100.0, facts.hard_floor_soc + settings.hard_floor_margin_pct)


def normal_floor(facts: BatteryFacts, settings: V2Settings) -> float:
    return max(settings.reserve_soc, facts.hard_floor_soc)


def caps(facts: BatteryFacts, charge_factor: float = 1.0, house_kw: float | None = None,
         solar_kw: float | None = None, car_kw: float = 0.0) -> tuple[float | None, float | None]:
    """(charge cap, discharge cap) in kW, each only when below the battery's own limit (else None).
    Charge: the fuse (house and car first, with the sun taken off the house), the battery's own limit and the cold."""
    charge = [facts.max_charge_kw * min(1.0, max(0.0, min(facts.charge_factor, charge_factor)))]
    if facts.bms_charge_kw is not None:
        charge.append(max(0.0, facts.bms_charge_kw))
    if house_kw is not None:
        charge.append(max(0.0, facts.fuse_kw - max(0.0, house_kw - (solar_kw or 0.0)) - car_kw))
    c = min(charge)
    d = max(0.0, facts.bms_discharge_kw) if facts.bms_discharge_kw is not None else None
    return (c if c < facts.max_charge_kw - 1e-9 else None,
            d if d is not None and d < facts.max_discharge_kw - 1e-9 else None)


def _core(settings: V2Settings, facts: BatteryFacts, *, event: bool, manual: str | None, free: bool, car: bool,
          level: float | None, charge_factor: float, house_kw: float | None, solar_kw: float | None, car_kw: float,
          reserve_latched: bool = False, override_text: str = "") -> Limits:
    s = settings
    floor = normal_floor(facts, s)
    ceiling = float(s.charge_ceiling_soc)
    charge_cap, discharge_cap = caps(facts, charge_factor, house_kw, solar_kw, car_kw)
    common = {"charge_cap_kw": charge_cap, "discharge_cap_kw": discharge_cap}

    if event and s.events:
        f = event_floor(facts, s)
        return Limits(allowed=frozenset(), forced=EVENT, floor_soc=f, ceiling_soc=100.0, rule="event",
                      reason=f"Grid event: the battery sells down to {f:.0f}% (the battery's hard floor of "
                             f"{facts.hard_floor_soc:.0f}% plus a {s.hard_floor_margin_pct:g} point margin), "
                             f"not to your {s.reserve_soc:.0f}% reserve", **common)
    if manual is not None:
        text = override_text or f"Manual override: {manual.replace('_', '-')}"
        return Limits(allowed=frozenset({manual}) & frozenset(CHOICE_MODES), forced=manual, floor_soc=floor,
                      ceiling_soc=ceiling, rule="override", reason=text, **common)
    if free and s.free_power:
        return Limits(allowed=frozenset(), forced=FREE, floor_soc=floor, ceiling_soc=100.0, rule="free",
                      reason="Free-power session: the battery charges to 100%", **common)
    allowed = {SELF_USE, HOLD, CHARGE}
    if s.arbitrage:
        allowed.add(EXPORT)
    rule, reason = "normal", ""
    if car:
        allowed &= {HOLD, CHARGE}
        rule, reason = "car", "The car is charging: the battery holds so it never feeds the car"
    held_below = floor + (s.level_band_pct if reserve_latched else 0.0)
    if level is not None and level <= held_below:
        allowed -= {SELF_USE, EXPORT}
        allowed.add(HOLD)
        if rule != "car":
            rule = "reserve"
        reason = (f"The battery is at its {floor:.0f}% " + ("reserve" if floor > facts.hard_floor_soc
                                                            else "hard floor") + ": nothing may discharge it")
    return Limits(allowed=frozenset(allowed), forced=None, floor_soc=floor, ceiling_soc=ceiling, rule=rule,
                  reason=reason, **common)


def limits_now(situation: Situation, obs: Observation, seg: Segment | None, facts: BatteryFacts,
               settings: V2Settings, readings, *, reserve_latched: bool = False) -> Limits:
    """The rules now, from the live readings. `reserve_latched` (the executor's memory of being held at the reserve)
    makes the level rise the level band above it before discharging may start again."""
    r = readings
    manual = None
    text = ""
    if situation.override is not None:
        manual = OVERRIDE_MODE.get(getattr(situation.override, "mode", None))
        if manual is not None:
            text = f"Manual override: {describe(situation.override)}"
    level = obs.level_filtered if obs.level_filtered is not None else obs.level_reported
    car_kw = (r.ev_power or 0.0) / 1000.0 if (obs.car_charging and r.ev_power) else (
        facts.ev_charger_kw if obs.car_charging else 0.0)
    return _core(settings, facts,
                 event=r.axle_state() == "active", manual=manual, free=r.free_state() == "active",
                 car=obs.car_charging and situation.house_load_includes_ev, level=level,
                 charge_factor=seg.charge_factor if seg is not None else 1.0,
                 house_kw=(r.house_power or 0.0) / 1000.0 if r.house_power is not None else None,
                 solar_kw=(r.solar_power or 0.0) / 1000.0, car_kw=car_kw,
                 reserve_latched=reserve_latched, override_text=text)


def limits_for(seg: Segment, facts: BatteryFacts, settings: V2Settings, *,
               house_load_includes_ev: bool = True) -> Limits:
    """The same rules for a future segment, from the forecast (no live level: the reserve is a floor in the physics)."""
    hours = seg.hours or 1e-9
    return _core(settings, facts, event=seg.event, manual=seg.manual, free=seg.free,
                 car=seg.car_kw > 0 and house_load_includes_ev, level=None, charge_factor=seg.charge_factor,
                 house_kw=seg.load_kwh.mid / hours, solar_kw=seg.solar_kwh.mid / hours, car_kw=seg.car_kw)
