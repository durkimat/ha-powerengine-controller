"""The explainable 24-48 h planner.

1. Every half-hour slot starts with a default action:
   Axle event -> force-discharge; free power -> grid-charge; cheap price -> hold
   (let the grid cover the house, save the battery); car smart slot -> hold;
   otherwise self-use.
2. Simulate the battery forward. Find the first *avoidable problem*: an import
   at a price worth avoiding (the battery ran out), or an Axle event the battery
   can't fully cover.
3. Fix it by grid-charging in the cheapest earlier slot where that pays
   (price / round-trip efficiency < value of what it avoids). Repeat.

Every slot keeps a plain-English reason, so the plan can always say why.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from .decide import FORCE_DISCHARGE, GRID_CHARGE, HOLD, SELF_USE
from .forecast import Slot

DT_H = 0.5
MAX_ITERATIONS = 200
MIN_GAIN = 0.005        # GBP/kWh a charge slot must save to be worth it


@dataclass(frozen=True)
class Params:
    capacity_kwh: float = 18.0
    max_charge_kw: float = 4.8
    max_discharge_kw: float = 4.8
    efficiency: float = 0.95          # one-way (charge or discharge)
    min_reserve_soc: float = 12.0
    target_soc: float = 100.0         # normal grid-charge target
    cheap_cap_p: float = 10.0         # normal charging only at or below this price
    axle_kw: float = 4.0
    axle_value: float = 1.00          # GBP/kWh exported during an Axle event
    axle_enabled: bool = True
    free_enabled: bool = True
    hold_for_car: bool = True         # car in house load: don't let the battery feed it


@dataclass
class PlanSlot:
    slot: Slot
    action: str
    reason: str
    target_soc: float | None = None
    soc_start: float = 0.0            # %
    soc_end: float = 0.0
    grid_import: float = 0.0          # kWh
    grid_export: float = 0.0
    cost: float = 0.0                 # GBP (negative = income)


@dataclass
class Plan:
    slots: list[PlanSlot]
    made_at: datetime
    cost: float = 0.0
    baseline_cost: float = 0.0        # same period, battery in plain self-use
    windows: list[dict] = field(default_factory=list)


def _p(gbp: float | None) -> str:
    return "?" if gbp is None else (f"{gbp * 100:.2f}".rstrip("0").rstrip(".") + "p")


def _hhmm(t: datetime, tz) -> str:
    return (t.astimezone(tz) if tz else t).strftime("%H:%M")


# --- battery physics for one slot ----------------------------------------------------

def step(ps: PlanSlot, soc: float, p: Params, dt_h: float = DT_H) -> float:
    """Apply ps.action for `dt_h` hours starting at `soc` (%). Fills in flows and cost; returns end SoC."""
    s = ps.slot
    cap = p.capacity_kwh
    stored = soc / 100 * cap
    floor = p.min_reserve_soc / 100 * cap
    net = s.load_kwh - s.solar_kwh                     # + house needs energy
    imp = exp = 0.0
    axle_export = 0.0

    def charge_from_surplus(surplus: float, limit_soc: float = 100.0) -> float:
        nonlocal stored
        room = max(0.0, limit_soc / 100 * cap - stored)
        into = min(surplus, p.max_charge_kw * dt_h, room / p.efficiency)
        stored += into * p.efficiency
        return surplus - into

    if ps.action == GRID_CHARGE:
        target = ps.target_soc if ps.target_soc is not None else p.target_soc
        room = max(0.0, target / 100 * cap - stored)
        into = min(p.max_charge_kw * dt_h, room / p.efficiency)
        stored += into * p.efficiency
        flow = net + into
        imp, exp = max(0.0, flow), max(0.0, -flow)
    elif ps.action == FORCE_DISCHARGE:
        out = min(p.axle_kw * dt_h, max(0.0, stored - floor) * p.efficiency)
        stored -= out / p.efficiency
        flow = net - out                                # + import, - export
        imp = max(0.0, flow)
        exp_total = max(0.0, -flow)
        axle_export = min(exp_total, out)
        exp = exp_total - axle_export
    else:                                               # SELF_USE or HOLD
        if net > 0:
            if ps.action == SELF_USE:
                out = min(net, p.max_discharge_kw * dt_h, max(0.0, stored - floor) * p.efficiency)
                stored -= out / p.efficiency
                imp = net - out
            else:
                imp = net
        else:
            exp = charge_from_surplus(-net)

    price = s.price if s.price is not None else 0.0
    export_price = s.export if s.export is not None else 0.0
    ps.grid_import, ps.grid_export = imp, exp + axle_export
    ps.cost = imp * price - exp * export_price - axle_export * p.axle_value
    ps.soc_start = soc
    ps.soc_end = stored / cap * 100
    return ps.soc_end


def simulate(slots: list[PlanSlot], soc: float, p: Params) -> float:
    for ps in slots:
        soc = step(ps, soc, p)
    return sum(ps.cost for ps in slots)


# --- the planner -----------------------------------------------------------------

def _default(s: Slot, p: Params, tz) -> PlanSlot:
    if p.axle_enabled and s.axle:
        return PlanSlot(s, FORCE_DISCHARGE, "Axle event: export for £1/kWh")
    if p.free_enabled and s.free:
        return PlanSlot(s, GRID_CHARGE, "free-electricity session: fill the battery", target_soc=100.0)
    if p.hold_for_car and s.smart_slot:
        return PlanSlot(s, HOLD, f"car smart-charge slot ({_p(s.price)}): the battery mustn't feed the car")
    if s.price is not None and s.price * 100 <= p.cheap_cap_p:
        why = f"cheap import ({_p(s.price)}): the grid covers the house, the battery is saved for later"
        return PlanSlot(s, HOLD, why)
    return PlanSlot(s, SELF_USE, "the battery covers the house")


def _first_problem(plan: list[PlanSlot], p: Params, start: int, eff2: float):
    """(index, value GBP/kWh, kind) of the first avoidable shortfall at or after `start`."""
    for i in range(start, len(plan)):
        ps = plan[i]
        at_floor = ps.soc_end <= p.min_reserve_soc + 0.05
        if ps.action == FORCE_DISCHARGE and at_floor:
            wanted = p.axle_kw * DT_H
            delivered = (ps.soc_start - ps.soc_end) / 100 * p.capacity_kwh * p.efficiency
            if delivered < wanted - 0.01:
                return i, p.axle_value, "axle"
        if ps.action == SELF_USE and ps.grid_import > 0.01 and at_floor and ps.slot.price is not None:
            return i, ps.slot.price, "import"
    return None


def make_plan(slots: list[Slot], soc: float, p: Params, now: datetime, tz=None) -> Plan:
    plan = [_default(s, p, tz) for s in slots]
    eff2 = p.efficiency ** 2
    baseline = [PlanSlot(s, FORCE_DISCHARGE if (p.axle_enabled and s.axle) else SELF_USE, "") for s in slots]
    baseline_cost = simulate(baseline, soc, p)

    simulate(plan, soc, p)
    scan_from, skipped = 0, set()
    for _ in range(MAX_ITERATIONS):
        prob = _first_problem(plan, p, scan_from, eff2)
        if prob is None:
            break
        i, value, kind = prob
        best = None
        for j in range(i):
            c = plan[j]
            if c.action not in (SELF_USE, HOLD) or c.slot.price is None or j in skipped:
                continue
            if c.soc_end >= 99.9:
                continue
            if kind == "import" and c.slot.price * 100 > p.cheap_cap_p:
                continue
            if c.slot.price / eff2 >= value - MIN_GAIN:
                continue
            if best is None or c.slot.price < plan[best].slot.price:
                best = j
        if best is None:
            scan_from = i + 1                           # can't fix this one economically
            continue
        c = plan[best]
        when = f"{_hhmm(plan[i].slot.start, tz)}"
        if kind == "axle":
            reason = f"top up for the Axle event at {when} (charging at {_p(c.slot.price)} to earn £1/kWh)"
            target = 100.0
        else:
            reason = f"cheapest time ({_p(c.slot.price)}) to cover {when} onwards at {_p(value)}"
            target = p.target_soc
        plan[best] = replace(c, action=GRID_CHARGE, reason=reason, target_soc=target)
        simulate(plan, soc, p)

    result = Plan(slots=plan, made_at=now, cost=sum(ps.cost for ps in plan), baseline_cost=baseline_cost)
    result.windows = windows(plan, tz)
    return result


# --- presenting the plan -------------------------------------------------------------

def windows(plan: list[PlanSlot], tz=None) -> list[dict]:
    """Merge consecutive slots with the same action and reason into windows."""
    out: list[dict] = []
    for ps in plan:
        key = (ps.action, ps.reason, ps.target_soc)
        if out and out[-1]["_key"] == key:
            w = out[-1]
            w["end"] = ps.slot.end
            w["prices"].append(ps.slot.price)
            w["soc_end"] = ps.soc_end
            w["cost"] += ps.cost
        else:
            out.append({"_key": key, "start": ps.slot.start, "end": ps.slot.end, "action": ps.action,
                        "reason": ps.reason, "target_soc": ps.target_soc, "prices": [ps.slot.price],
                        "soc_start": ps.soc_start, "soc_end": ps.soc_end, "cost": ps.cost,
                        "estimated": ps.slot.price_estimated})
    for w in out:
        prices = [x for x in w.pop("prices") if x is not None]
        w.pop("_key")
        lo, hi = (min(prices), max(prices)) if prices else (None, None)
        w["price"] = _p(lo) if lo == hi else f"{_p(lo)}–{_p(hi)}"
        w["from"], w["to"] = _hhmm(w["start"], tz), _hhmm(w["end"], tz)
        w["start"], w["end"] = w["start"].isoformat(), w["end"].isoformat()
        w["soc_start"], w["soc_end"] = round(w["soc_start"]), round(w["soc_end"])
        w["cost"] = round(w["cost"], 2)
    return out


ACTION_WORDS = {SELF_USE: "Self-use", GRID_CHARGE: "Grid-charge", HOLD: "Hold", FORCE_DISCHARGE: "Force-discharge"}


def headline(plan: Plan) -> str:
    """The next significant thing the plan does, in one sentence."""
    ws = plan.windows
    if not ws:
        return "No plan yet."
    now_w = ws[0]
    nxt = next((w for w in ws[1:] if w["action"] in (GRID_CHARGE, FORCE_DISCHARGE)), None)
    if now_w["action"] in (GRID_CHARGE, FORCE_DISCHARGE) or nxt is None:
        nxt = now_w
    verb = ACTION_WORDS[nxt["action"]]
    if nxt["action"] == GRID_CHARGE and nxt["target_soc"]:
        verb += f" to {nxt['target_soc']:.0f}%"
    when = "Now" if nxt is now_w else f"{nxt['from']}–{nxt['to']}"
    saving = plan.baseline_cost - plan.cost
    tail = f" Plan saves £{saving:.2f} vs plain self-use over this period." if saving > 0.005 else ""
    return f"{when}: {verb.lower() if when != 'Now' else verb} ({nxt['price']}): {nxt['reason']}.{tail}"


def params_from(cfg, readings=None) -> Params:
    """Planner parameters from the config (fixed values) and safety settings."""
    def static(role, default):
        spec = cfg.inputs.get(role) or {}
        try:
            return float(spec["value"]) if "value" in spec else default
        except (TypeError, ValueError):
            return default
    s, f = cfg.safety, cfg.features
    max_dis = static("battery_max_discharge_power", 4800) / 1000
    return Params(
        capacity_kwh=static("battery_capacity", 18.0),
        max_charge_kw=static("battery_max_charge_power", 4800) / 1000,
        max_discharge_kw=max_dis,
        min_reserve_soc=s["min_reserve_soc"],
        target_soc=s["grid_charge_target_soc"],
        cheap_cap_p=s["cheap_threshold_p"],
        axle_kw=min(4.0, max_dis),
        axle_enabled=bool(f.get("axle")),
        free_enabled=bool(f.get("free_power_days")),
        hold_for_car=bool(cfg.system.get("house_load_includes_ev", True)),
    )


def plan_entity_states(plan: Plan | None, extra: dict | None = None) -> dict:
    """key -> (state, attributes) for the plan_* entities."""
    if plan is None:
        return {"plan": ("unknown", {}), "plan_headline": ("No plan yet.", {"text": "No plan yet."})}
    ser = {"t": [], "soc": [], "price_p": [], "solar_kwh": [], "load_kwh": [], "charge_kwh": [], "discharge_kwh": [],
           "action": []}
    for ps in plan.slots:
        ser["t"].append(ps.slot.start.isoformat())
        ser["soc"].append(round(ps.soc_end, 1))
        ser["price_p"].append(None if ps.slot.price is None else round(ps.slot.price * 100, 2))
        ser["solar_kwh"].append(round(ps.slot.solar_kwh, 2))
        ser["load_kwh"].append(round(ps.slot.load_kwh, 2))
        ser["charge_kwh"].append(round(ps.grid_import, 2) if ps.action == GRID_CHARGE else 0)
        ser["discharge_kwh"].append(round(ps.grid_export, 2) if ps.action == FORCE_DISCHARGE else 0)
        ser["action"].append(ps.action)
    text = headline(plan)
    est = next((ps.slot.start.isoformat() for ps in plan.slots if ps.slot.price_estimated), None)
    nxt = plan.windows[1] if len(plan.windows) > 1 else None
    attrs = {"windows": plan.windows, "series": ser, "cost": round(plan.cost, 2),
             "baseline_cost": round(plan.baseline_cost, 2), "saving": round(plan.baseline_cost - plan.cost, 2),
             "horizon_end": plan.slots[-1].slot.end.isoformat() if plan.slots else None,
             "estimated_prices_from": est}
    attrs.update(extra or {})
    return {
        "plan": (plan.made_at.isoformat(timespec="seconds"), attrs),
        "plan_headline": (text[:254], {"text": text}),
        "plan_next_mode": (nxt["action"] if nxt else "none", {"reason": nxt["reason"] if nxt else None}),
        "plan_next_start": (nxt["start"] if nxt else "unknown", {}),
        "plan_next_target_soc": (nxt["target_soc"] if nxt and nxt["target_soc"] is not None else "unknown", {}),
    }
