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

from .decide import EXPORT, FORCE_DISCHARGE, GRID_CHARGE, HOLD, SELF_USE
from .forecast import Slot
from .tariff import cheap_threshold

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
    fuse_kw: float = 60 * 0.230 * 0.9  # import limit: 90% of the main fuse at 230 V
    ev_charger_kw: float = 7.4        # car draw assumed during planned smart slots
    fill_when_cheap: bool = True      # top up to the target in every cheap slot (a buffer against forecast error)
    arbitrage: bool = False           # sell stored energy before a cheap refill when it pays
    export_limit_kw: float = 6.0      # DNO export limit
    wear_p: float = 2.0               # battery wear per kWh cycled (p)
    min_margin_p: float = 1.0         # arbitrage must clear this per kWh after losses and wear (p)
    arbitrage_keep_soc: float = 10.0  # keep this much above the reserve when the refill starts (%)


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
    grid_to_battery: float = 0.0      # kWh drawn from the grid into the battery (grid-charge only)
    cost: float = 0.0                 # GBP (negative = income)


@dataclass
class Plan:
    slots: list[PlanSlot]
    made_at: datetime
    cost: float = 0.0
    baseline_cost: float = 0.0        # same period, battery in plain self-use
    windows: list[dict] = field(default_factory=list)
    cheap_p: float | None = None      # the cheap-import threshold used (p/kWh)
    extra_kwh: float = 0.0            # energy left in the battery at the end, compared with plain self-use
    extra_value: float = 0.0          # that energy valued at the cheapest import price in the period (GBP)

    @property
    def saving(self) -> float:
        """Cash saving plus the value of any extra charge left at the end (so topping up isn't counted as a loss)."""
        return self.baseline_cost - self.cost + self.extra_value


def _p(gbp: float | None) -> str:
    return "?" if gbp is None else (f"{gbp * 100:.2f}".rstrip("0").rstrip(".") + "p")


def _hhmm(t: datetime, tz) -> str:
    return (t.astimezone(tz) if tz else t).strftime("%H:%M")


def _day(t: datetime, now: datetime | None, tz) -> str:
    """'' for today, 'tomorrow', else a short weekday; all in local time."""
    if now is None:
        return ""
    d = (t.astimezone(tz) if tz else t).date()
    today = (now.astimezone(tz) if tz else now).date()
    delta = (d - today).days
    if delta == 0:
        return ""
    if delta == 1:
        return "tomorrow"
    return (t.astimezone(tz) if tz else t).strftime("%a")


def _when(t: datetime, now: datetime | None, tz) -> str:
    day = _day(t, now, tz)
    return f"{_hhmm(t, tz)} {day}".strip()


# --- battery physics for one slot ----------------------------------------------------

def car_kw(s: Slot, p: Params) -> float:
    if s.car_kw is not None:
        return max(0.0, s.car_kw)
    return p.ev_charger_kw if s.smart_slot else 0.0


def grid_charge_kw(s: Slot, p: Params, dt_h: float = DT_H) -> float:
    """Battery grid-charge power allowed in this slot: the inverter limit, reduced to keep total import under the fuse.

    House (net of solar) and car come first; the battery gets what is left.
    """
    house_kw = max(0.0, (s.load_kwh - s.solar_kwh) / dt_h) if dt_h else 0.0
    headroom = p.fuse_kw - house_kw - car_kw(s, p)
    return max(0.0, min(p.max_charge_kw, headroom))


def step(ps: PlanSlot, soc: float, p: Params, dt_h: float = DT_H) -> float:
    """Apply ps.action for `dt_h` hours starting at `soc` (%). Fills in flows and cost; returns end SoC."""
    s = ps.slot
    cap = p.capacity_kwh
    stored = soc / 100 * cap
    floor = p.min_reserve_soc / 100 * cap
    net = s.load_kwh - s.solar_kwh                     # + house needs energy
    imp = exp = 0.0
    axle_export = 0.0
    ps.grid_to_battery = 0.0

    def charge_from_surplus(surplus: float, limit_soc: float = 100.0) -> float:
        nonlocal stored
        room = max(0.0, limit_soc / 100 * cap - stored)
        into = min(surplus, p.max_charge_kw * dt_h, room / p.efficiency)
        stored += into * p.efficiency
        return surplus - into

    if ps.action == GRID_CHARGE:
        target = ps.target_soc if ps.target_soc is not None else p.target_soc
        room = max(0.0, target / 100 * cap - stored)
        into = min(grid_charge_kw(s, p, dt_h) * dt_h, room / p.efficiency)
        stored += into * p.efficiency
        ps.grid_to_battery = into
        flow = net + into
        imp, exp = max(0.0, flow), max(0.0, -flow)
    elif ps.action == EXPORT:
        room_kw = min(p.max_discharge_kw, p.export_limit_kw + max(0.0, net) / dt_h)
        out = min(room_kw * dt_h, max(0.0, stored - floor) * p.efficiency)
        stored -= out / p.efficiency
        flow = net - out                                # the battery covers the house first, the rest is sold
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
    cheap = s.price is not None and s.price * 100 <= p.cheap_cap_p
    if cheap and p.fill_when_cheap:
        why = f"cheap import ({_p(s.price)}): top up to {p.target_soc:.0f}% as a buffer in case the forecast is wrong"
        return PlanSlot(s, GRID_CHARGE, why, target_soc=p.target_soc)
    if p.hold_for_car and s.smart_slot:
        return PlanSlot(s, HOLD, f"car smart-charge slot ({_p(s.price)}): the battery mustn't feed the car")
    if cheap:
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


def _add_arbitrage(plan: list[PlanSlot], soc: float, p: Params, now: datetime, tz=None) -> None:
    """Sell stored energy just before a cheap refill, when it pays and the house doesn't go short.

    For each cheap period in the plan, walk back from its start through the self-use half-hours before it and
    turn them into exports (latest first), as long as:
      - selling beats buying it back: export price - refill price / round trip - wear >= the minimum margin;
      - the battery still reaches the refill with the reserve plus a margin (so the house isn't pushed onto the
        peak rate by a forecast that's a little out);
      - no half-hour before the refill ends up importing more than it did.
    """
    rte = p.efficiency ** 2
    def cheap(k: int) -> bool:
        return plan[k].slot.price is not None and plan[k].slot.price * 100 <= p.cheap_cap_p
    starts = [i for i in range(1, len(plan)) if cheap(i) and not cheap(i - 1)]
    for i in starts:
        buy = plan[i].slot.price
        refill = _hhmm(plan[i].slot.start, tz)
        keep = p.min_reserve_soc + p.arbitrage_keep_soc
        for j in range(i - 1, -1, -1):
            c = plan[j]
            if c.action != SELF_USE or c.slot.axle or c.slot.free or c.slot.smart_slot or c.slot.export is None:
                break
            margin_p = (c.slot.export - buy / rte) * 100 - p.wear_p
            if margin_p < p.min_margin_p:
                break
            before_imports = [ps.grid_import for ps in plan[j:i]]
            why = (f"sell at {_p(c.slot.export)}: refilled at {_p(buy)} from {refill} "
                   f"(about {margin_p:.1f}p/kWh after losses and wear)")
            plan[j] = replace(c, action=EXPORT, reason=why)
            simulate(plan, soc, p)
            worse = any(ps.grid_import > b + 0.01 for ps, b in zip(plan[j:i], before_imports, strict=True)
                        if ps.action != EXPORT)
            if plan[i - 1].soc_end < keep or worse:
                plan[j] = c                                  # undo, and stop for this refill
                simulate(plan, soc, p)
                break


def make_plan(slots: list[Slot], soc: float, p: Params, now: datetime, tz=None, auto_cheap: bool = False,
              wear_p: float = 2.0) -> Plan:
    if auto_cheap:
        p = replace(p, cheap_cap_p=cheap_threshold([s.price for s in slots], p.cheap_cap_p, p.efficiency ** 2, wear_p))
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
        when = _when(plan[i].slot.start, now, tz)
        if kind == "axle":
            reason = f"top up for the Axle event at {when} (charging at {_p(c.slot.price)} to earn £1/kWh)"
            target = 100.0
        else:
            reason = f"cheapest time ({_p(c.slot.price)}) to avoid buying at {_p(value)} from {when}"
            target = p.target_soc
        plan[best] = replace(c, action=GRID_CHARGE, reason=reason, target_soc=target)
        simulate(plan, soc, p)

    if p.arbitrage:
        _add_arbitrage(plan, soc, p, now, tz)

    result = Plan(slots=plan, made_at=now, cheap_p=p.cheap_cap_p, cost=sum(ps.cost for ps in plan),
                  baseline_cost=baseline_cost)
    if plan and baseline:
        extra_kwh = (plan[-1].soc_end - baseline[-1].soc_end) / 100 * p.capacity_kwh
        prices = [s.price for s in slots if s.price is not None]
        result.extra_kwh = extra_kwh
        result.extra_value = extra_kwh * min(prices) if prices else 0.0
    result.windows = windows(plan, tz, now)
    return result


# --- presenting the plan -------------------------------------------------------------

def windows(plan: list[PlanSlot], tz=None, now: datetime | None = None) -> list[dict]:
    """Merge consecutive slots with the same action and reason into windows.

    Back-to-back grid-charge slots merge even when their reasons differ (each names the shortfall it fixes); the
    window keeps the first reason, which is the earliest shortfall. A grid-charge window's target is the level the
    plan actually reaches, not the configured ceiling.
    """
    out: list[dict] = []
    for ps in plan:
        key = (ps.action, ps.reason, ps.target_soc)
        same = out and (out[-1]["_key"] == key or (ps.action == GRID_CHARGE and out[-1]["action"] == GRID_CHARGE))
        if same:
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
        w["day"] = _day(w["start"], now, tz)
        end_day = _day(w["end"], now, tz)
        if end_day != w["day"] and w["to"] != "00:00":         # a window running past midnight
            w["to"] = f"{w['to']} {end_day or 'today'}"
        if w["action"] == GRID_CHARGE and w["target_soc"] is not None:
            w["target_soc"] = float(round(w["soc_end"]))
        w["start"], w["end"] = w["start"].isoformat(), w["end"].isoformat()
        w["soc_start"], w["soc_end"] = round(w["soc_start"]), round(w["soc_end"])
        w["cost"] = round(w["cost"], 2)
    return out


ACTION_WORDS = {SELF_USE: "Self-use", GRID_CHARGE: "Grid-charge", HOLD: "Hold", FORCE_DISCHARGE: "Force-discharge",
                EXPORT: "Export"}


def headline(plan: Plan) -> str:
    """The next significant thing the plan does, in one sentence."""
    ws = plan.windows
    if not ws:
        return "No plan yet."
    now_w = ws[0]
    nxt = next((w for w in ws[1:] if w["action"] in (GRID_CHARGE, FORCE_DISCHARGE, EXPORT)), None)
    if now_w["action"] in (GRID_CHARGE, FORCE_DISCHARGE, EXPORT) or nxt is None:
        nxt = now_w
    verb = ACTION_WORDS[nxt["action"]]
    if nxt["action"] == GRID_CHARGE and nxt["target_soc"]:
        verb += f" to {nxt['target_soc']:.0f}%"
    day = f"{nxt['day'].capitalize()} " if nxt.get("day") else ""
    when = "Now" if nxt is now_w else f"{day}{nxt['from']}–{nxt['to']}"
    saving = plan.saving
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
        fuse_kw=s.get("main_fuse_a", 60) * 0.230 * 0.9,
        fill_when_cheap=bool(f.get("fill_when_cheap", True)),
        arbitrage=bool(f.get("arbitrage", False)),
        export_limit_kw=s.get("export_limit_kw", 6.0),
        wear_p=s.get("battery_wear_p", 2.0),
        min_margin_p=s.get("arbitrage_min_margin_p", 1.0),
        ev_charger_kw=s.get("ev_charger_kw", 7.4),
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
        ser["charge_kwh"].append(round(ps.grid_to_battery, 2))
        ser["discharge_kwh"].append(round(ps.grid_export, 2) if ps.action in (FORCE_DISCHARGE, EXPORT) else 0)
        ser["action"].append(ps.action)
    text = headline(plan)
    est = next((ps.slot.start.isoformat() for ps in plan.slots if ps.slot.price_estimated), None)
    nxt = plan.windows[1] if len(plan.windows) > 1 else None
    attrs = {"windows": plan.windows, "series": ser, "cost": round(plan.cost, 2),
             "baseline_cost": round(plan.baseline_cost, 2), "saving": round(plan.saving, 2),
             "extra_kwh": round(plan.extra_kwh, 1), "cheap_p": plan.cheap_p, "extra_value": round(plan.extra_value, 2),
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
