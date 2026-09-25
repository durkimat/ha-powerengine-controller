"""The rule stack: what PowerEngine would do right now, and why.

0.2 is reactive (no forward plan yet; that arrives in 0.3). Rules are checked
in priority order and the first that applies wins. Each decision carries a
plain-English reason and the abstract action it implies; how an action maps
onto a specific inverter's controls is decided in the Active design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta, tzinfo

from .config import Config
from .readings import Readings
from .tariff import cheap_threshold

SELF_USE, GRID_CHARGE, HOLD, FORCE_DISCHARGE, NONE = "self_use", "grid_charge", "hold", "force_discharge", "none"

ACTION_TEXT = {
    SELF_USE: "self-use",
    GRID_CHARGE: "grid-charge",
    HOLD: "hold the battery",
    FORCE_DISCHARGE: "force-discharge",
    NONE: "do nothing",
}

AXLE_POWER_W_DEFAULT = 4000   # typical Axle export rate if limits aren't mapped


@dataclass(frozen=True)
class Decision:
    action: str
    rule: str                      # short id of the rule that decided
    reason: str                    # plain English, no leading capital needed by callers
    target_soc: float | None = None
    power_w: float | None = None
    details: dict = field(default_factory=dict)

    def sentence(self, passive: bool) -> str:
        verb = ACTION_TEXT[self.action]
        if self.action == GRID_CHARGE and self.target_soc is not None:
            verb += f" to {self.target_soc:.0f}%"
        if self.action == FORCE_DISCHARGE and self.power_w:
            verb += f" at {self.power_w / 1000:.1f} kW"
        if self.action == SELF_USE and self.power_w is not None:
            verb += f" (battery limited to {self.power_w / 1000:.1f} kW)"
        if self.action == NONE:
            return f"No decision: {self.reason}"
        lead = "Would " if passive else ""
        text = f"{lead}{verb}: {self.reason}"
        return text[0].upper() + text[1:]


def _static(cfg: Config, role: str, default: float) -> float:
    spec = cfg.inputs.get(role) or {}
    try:
        return float(spec["value"]) if "value" in spec else default
    except (TypeError, ValueError):
        return default


def cheap_limit(r: Readings, cfg: Config) -> float:
    """Cheap-import threshold now (p/kWh): automatic from the next 24 hours' prices, or the fixed setting."""
    s = cfg.safety
    if not cfg.features.get("auto_cheap_threshold", True):
        return s["cheap_threshold_p"]
    ahead = [w.value for w in r.rates if w.end > r.now and w.start < r.now + timedelta(hours=24)]
    return cheap_threshold(ahead, s["cheap_threshold_p"], wear_p=s.get("battery_wear_p", 2.0))


def pre_axle_reserve(r: Readings, cfg: Config) -> float | None:
    """SoC (%) needed to cover a scheduled Axle event, or None if there isn't one."""
    if not (r.axle_start and r.axle_end):
        return None
    hours = max(0.0, (r.axle_end - r.axle_start).total_seconds() / 3600)
    capacity = _static(cfg, "battery_capacity", 0) or None
    power = min(AXLE_POWER_W_DEFAULT, _static(cfg, "battery_max_discharge_power", AXLE_POWER_W_DEFAULT))
    if not capacity:
        return None
    need = power / 1000 * hours / capacity * 100
    s = cfg.safety
    return min(100.0, s["min_reserve_soc"] + need + s["axle_margin_soc"])


def decide(r: Readings | None, cfg: Config, previous: Decision | None = None, tz: tzinfo | None = None,
           plan=None) -> Decision:
    """What PowerEngine would do now (the rule stack, or the plan with live overrides), within the fuse limit."""
    return fuse_limited(_decide(r, cfg, previous, tz, plan), r, cfg)


def _decide(r: Readings | None, cfg: Config, previous: Decision | None = None, tz: tzinfo | None = None,
            plan=None) -> Decision:
    if r is None:
        return Decision(NONE, "unconfigured", "PowerEngine isn't configured yet")
    if r.battery_soc is None or r.import_rate is None:
        missing = ", ".join(x for x in ("battery SoC" if r.battery_soc is None else "",
                                        "import rate" if r.import_rate is None else "") if x)
        return Decision(NONE, "no_data", f"no reading for {missing}")

    s, f = cfg.safety, cfg.features
    soc, price_p = r.battery_soc, r.import_rate * 100
    target = s["grid_charge_target_soc"]
    max_dis = _static(cfg, "battery_max_discharge_power", AXLE_POWER_W_DEFAULT)
    threshold = cheap_limit(r, cfg)
    cheap = price_p <= threshold
    price = f"{price_p:.2f}".rstrip("0").rstrip(".") + "p"

    # 1. Axle event in progress
    if f.get("axle") and r.axle_state() == "active":
        return Decision(FORCE_DISCHARGE, "axle_active", "Axle event in progress (paid £1/kWh exported)",
                        power_w=min(AXLE_POWER_W_DEFAULT, max_dis))

    # With a plan, live overrides first (Axle now, free power now, car charging now), then the plan.
    if plan is not None and plan.slots:
        return _with_plan(r, cfg, plan, soc, price, cheap, target)

    # 2. Keep enough charge for an upcoming Axle event
    soon = r.axle_state() == "scheduled" and r.axle_start - r.now <= timedelta(hours=s["pre_axle_lookahead_h"])
    if f.get("axle") and soon:
        need = pre_axle_reserve(r, cfg)
        if need is not None and soc < need:
            when = (r.axle_start.astimezone(tz) if tz else r.axle_start).strftime("%H:%M")
            if cheap:
                why = f"Axle event at {when} needs {need:.0f}%, and import is cheap ({price})"
                return Decision(GRID_CHARGE, "pre_axle", why, target_soc=max(need, target))
            return Decision(HOLD, "pre_axle", f"keep charge for the Axle event at {when} (needs {need:.0f}%)",
                            target_soc=need)

    # 3. Free-electricity session
    if f.get("free_power_days") and r.free_state() == "active":
        return Decision(GRID_CHARGE, "free_power", "free-electricity session: fill the battery", target_soc=100)

    # 4. Car charging: the battery must never charge the car
    if r.house_includes_ev and r.ev_state() == "charging":
        if cheap and soc < target:
            why = f"car is charging at a cheap rate ({price}); charge the battery too"
            return Decision(GRID_CHARGE, "car_charging", why, target_soc=target)
        return _car_at_peak(r, soc, s, price)

    # 5. Cheap import
    if cheap:
        was_charging = previous is not None and previous.action == GRID_CHARGE and previous.rule == "cheap_rate"
        resume_below = target - (0 if was_charging else s["charge_hysteresis_soc"])
        if soc < resume_below or (was_charging and soc < target):
            return Decision(GRID_CHARGE, "cheap_rate", f"import is cheap ({price} ≤ {threshold:g}p)",
                            target_soc=target)
        why = f"import is cheap ({price}) and the battery is full enough; use the grid, save the battery"
        return Decision(HOLD, "cheap_rate", why)

    # 6. Default
    floor = s["min_reserve_soc"]
    if soc <= floor:
        return Decision(HOLD, "reserve", f"battery at its {floor:.0f}% minimum reserve")
    return Decision(SELF_USE, "default", f"nothing better to do at {price}; the battery covers the house")


def _car_at_peak(r: Readings, soc: float, s: dict, price: str) -> Decision:
    """Car charging at a non-cheap rate: hold the battery. (Covering the house but not the car was tried in
    0.5.2 and dropped: a 7.4 kW car charge dwarfs the house load, so it isn't worth the extra control.)"""
    return Decision(HOLD, "car_charging", f"car is charging at {price}; the battery holds and the grid covers "
                                          "house and car")


def fuse_limited(d: Decision, r: Readings, cfg: Config) -> Decision:
    """Cap grid charging so house + car + battery stay under 90% of the main fuse (battery reduced first)."""
    if d.action != GRID_CHARGE or r is None:
        return d
    s = cfg.safety
    max_w = _static(cfg, "battery_max_charge_power", 4800)
    limit_w = s.get("main_fuse_a", 60) * 230 * 0.9
    car_w = (r.ev_power or 0.0) if r.ev_state() == "charging" else 0.0
    house_w = max(0.0, (r.house_power or 0.0) - (r.solar_power or 0.0))
    room = max(0.0, limit_w - house_w - car_w)
    if room >= max_w:
        return d
    why = f"{d.reason} (charging limited to {room / 1000:.1f} kW by the {s.get('main_fuse_a', 60):g} A fuse)"
    return Decision(d.action, d.rule, why, target_soc=d.target_soc, power_w=round(room), details=d.details)


def _with_plan(r: Readings, cfg: Config, plan, soc: float, price: str, cheap: bool, target: float) -> Decision:
    s, f = cfg.safety, cfg.features
    if f.get("free_power_days") and r.free_state() == "active":
        return Decision(GRID_CHARGE, "free_power", "free-electricity session: fill the battery", target_soc=100)
    if r.house_includes_ev and r.ev_state() == "charging":
        if cheap and soc < target:
            why = f"car is charging at a cheap rate ({price}); charge the battery too"
            return Decision(GRID_CHARGE, "car_charging", why, target_soc=target)
        return _car_at_peak(r, soc, s, price)
    ps = plan.slots[0]
    if ps.action == FORCE_DISCHARGE and r.axle_state() != "active":
        return Decision(HOLD, "plan", "Axle event due now per the plan, but not started yet: holding charge")
    if ps.action == SELF_USE and soc <= s["min_reserve_soc"]:
        return Decision(HOLD, "reserve", f"battery at its {s['min_reserve_soc']:.0f}% minimum reserve")
    return Decision(ps.action, "plan", ps.reason, target_soc=ps.target_soc)
