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
from .names import N
from .readings import Readings
from .tariff import cheap_threshold

SELF_USE, GRID_CHARGE, HOLD, FORCE_DISCHARGE, NONE = "self_use", "grid_charge", "hold", "force_discharge", "none"
EXPORT = "export"          # arbitrage: sell stored energy that will be refilled cheaply

ACTION_TEXT = {
    SELF_USE: "self-use",
    GRID_CHARGE: "grid-charge",
    HOLD: "hold the battery",
    FORCE_DISCHARGE: "force-discharge",
    EXPORT: "export from the battery",
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
    label_target_soc: float | None = None    # what the sentence says a charge is heading for (the end of the plan's
                                             # run of charging); the control keeps using target_soc, the slot's own

    def sentence(self, passive: bool) -> str:
        verb = ACTION_TEXT[self.action]
        shown = self.label_target_soc if self.label_target_soc is not None else self.target_soc
        if self.action == GRID_CHARGE and shown is not None:
            verb += f" to {shown:.0f}%"
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


def axle_power_w(cfg: Config) -> float:
    """Discharge power for an Axle event: as fast as the battery allows, since Axle pays per kWh exported. Within
    the RAM remote-control cap when that's the control method. AXLE_POWER_W_DEFAULT applies only when the battery's
    limit isn't configured. (Before 0.9.55 it capped every event at 4 kW, which on 28 Sep 2026 left about 1 kWh an
    hour unsold.)"""
    p = _static(cfg, "battery_max_discharge_power", AXLE_POWER_W_DEFAULT)
    if (cfg.system.get("control_method") or "timed_windows") == "ram_remote":
        try:
            p = min(p, float(cfg.safety.get("ram_max_power_w", 5000)))
        except (TypeError, ValueError):
            p = min(p, 5000.0)
    return p


def pre_axle_reserve(r: Readings, cfg: Config) -> float | None:
    """SoC (%) needed to cover a scheduled Axle event, or None if there isn't one."""
    if not (r.axle_start and r.axle_end):
        return None
    hours = max(0.0, (r.axle_end - r.axle_start).total_seconds() / 3600)
    capacity = _static(cfg, "battery_capacity", 0) or None
    power = axle_power_w(cfg)
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
    threshold = cheap_limit(r, cfg)
    cheap = price_p <= threshold
    price = f"{price_p:.2f}".rstrip("0").rstrip(".") + "p"

    # 1. Axle event in progress
    if f.get("axle") and r.axle_state() == "active":
        extra = (f" + {r.export_rate * 100:g}p export" if f.get("axle_plus_export", True) and r.export_rate
                 else "")
        return Decision(FORCE_DISCHARGE, "axle_active",
                        f"{N('event')} event in progress (paid £1{extra} per kWh exported)",
                        power_w=axle_power_w(cfg))

    # With a plan, live overrides first (Axle now, free power now, car charging now), then the plan.
    if plan is not None and plan.slots:
        return _with_plan(r, cfg, plan, soc, price, cheap, target, previous)

    # 2. Keep enough charge for an upcoming Axle event
    soon = r.axle_state() == "scheduled" and r.axle_start - r.now <= timedelta(hours=s["pre_axle_lookahead_h"])
    if f.get("axle") and soon:
        need = pre_axle_reserve(r, cfg)
        if need is not None and soc < need:
            when = (r.axle_start.astimezone(tz) if tz else r.axle_start).strftime("%H:%M")
            if cheap:
                why = f"{N('event')} event at {when} needs {need:.0f}%, and import is cheap ({price})"
                return Decision(GRID_CHARGE, "pre_axle", why, target_soc=max(need, target))
            return Decision(HOLD, "pre_axle", f"keep charge for the {N('event')} event at {when} (needs {need:.0f}%)",
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
    return Decision(d.action, d.rule, why, target_soc=d.target_soc, power_w=round(room), details=d.details,
                    label_target_soc=d.label_target_soc)


def _run_destination(plan, ps) -> float | None:
    """Where the plan's run of the same action, from the first slot on, ends up: the last slot's target."""
    target = ps.target_soc
    for nxt in plan.slots:
        if nxt.action != ps.action:
            break
        if nxt.target_soc is not None:
            target = nxt.target_soc
    return target


LATCH_BAND_SOC = 2.0     # a charge target reached this half-hour stays reached until the charge is this far below it


def _reached_text(target: float) -> str:
    return f"reached the {target:.0f}% charge target for this half-hour: holding until the next one"


def _held_at_target(previous: Decision | None, ps, soc: float) -> Decision | None:
    """The plan's charge target for this half-hour was reached (and held) a moment ago: keep holding until the half-hour
    ends. The inverter's charge is a whole number that reads a point lower while it is charging than while it holds
    (93 charging, 94 holding), so without this the app flipped between Force charge and Hold every 30 s at the target
    (29 Sep 2026, 22:38-22:44 and twice more). It lets go when the charge has really dropped (at least
    LATCH_BAND_SOC below the target), or the plan now wants a clearly higher target; the plan's action changing, or
    the half-hour ending, means it is not asked at all."""
    if (previous is None or previous.action != HOLD or previous.rule != "plan"
            or ps.action != GRID_CHARGE or ps.target_soc is None):
        return None
    latch = previous.details.get("reached")
    if not latch or latch.get("slot") != ps.slot.start.isoformat():
        return None
    held = latch["target"]
    if soc <= held - LATCH_BAND_SOC or ps.target_soc >= held + LATCH_BAND_SOC:
        return None
    return previous


def _with_plan(r: Readings, cfg: Config, plan, soc: float, price: str, cheap: bool, target: float,
               previous: Decision | None = None) -> Decision:
    s, f = cfg.safety, cfg.features
    if f.get("free_power_days") and r.free_state() == "active":
        return Decision(GRID_CHARGE, "free_power", "free-electricity session: fill the battery", target_soc=100)
    ps = plan.slots[0]
    held = _held_at_target(previous, ps, soc)
    if r.house_includes_ev and r.ev_state() == "charging":
        # the battery mustn't feed the car: follow the plan if it charges now; at a cheap rate charge it too
        if held is not None:
            return held
        if ps.action == GRID_CHARGE:
            return Decision(GRID_CHARGE, "car_charging", f"car is charging; {ps.reason}", target_soc=ps.target_soc,
                            label_target_soc=_run_destination(plan, ps))
        if cheap:
            top = min(target, s["arbitrage_max_soc"]) if f.get("arbitrage") and not ps.slot.overnight else target
            if f.get("fill_when_cheap", True) and soc < top:
                return Decision(GRID_CHARGE, "car_charging", f"car is charging at a cheap rate ({price}); charge "
                                f"the battery too, up to {top:.0f}%", target_soc=top)
            return Decision(HOLD, "car_charging", f"car is charging at a cheap rate ({price}); the battery holds")
        return _car_at_peak(r, soc, s, price)
    if ps.action == FORCE_DISCHARGE and r.axle_state() != "active":
        return Decision(HOLD, "plan", f"{N('event')} event due now per the plan, but not started yet: holding charge")
    if ps.action == SELF_USE and soc <= s["min_reserve_soc"]:
        return Decision(HOLD, "reserve", f"battery at its {s['min_reserve_soc']:.0f}% minimum reserve")
    if held is not None:
        return held
    if ps.action == GRID_CHARGE and ps.target_soc is not None and soc >= ps.target_soc:
        # the plan charges only up to its target and lets the grid cover the house for the rest of the half-hour;
        # without this the inverter kept charging (RAM control: 63% -> 82% against a 76% target, 28 Sep 2026)
        return Decision(HOLD, "plan", _reached_text(ps.target_soc), target_soc=ps.target_soc,
                        details={"reached": {"slot": ps.slot.start.isoformat(), "target": ps.target_soc}})
    return Decision(ps.action, "plan", ps.reason, target_soc=ps.target_soc,
                    label_target_soc=_run_destination(plan, ps) if ps.action == GRID_CHARGE else None)
