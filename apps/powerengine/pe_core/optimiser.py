"""An optimiser run alongside the heuristic planner, for comparison only (never used for decisions).

Dynamic programming over the battery's state of charge in 1% steps: for every half-hour, working back from the
end of the horizon, it finds the cheapest action for each possible charge level, using exactly the same battery
physics, prices, limits and forecasts as the planner. Energy left at the end is valued at the cheapest price in
the period (as the planner's saving is). The result is the lowest cost any plan could reach under these
forecasts, so the gap to the heuristic shows how much a smarter planner could be worth.
"""

from __future__ import annotations

from .decide import EXPORT, FORCE_DISCHARGE, GRID_CHARGE, HOLD, SELF_USE
from .forecast import Slot
from .planner import DT_H, Params, PlanSlot, car_slot, step

LEVELS = 101                                  # 0..100 %


def grid_target(p: Params) -> float:
    """How full grid charging may take the battery. Always 100%: with arbitrage on, charging above the band costs
    the band penalty instead (a guide, not a limit)."""
    return 100.0


def car_cheap_charge(s: Slot, p: Params) -> bool:
    """A car smart-charge slot at a cheap price, with top-up when cheap on: the battery charges alongside the car."""
    price = s.slot_price if s.slot_price is not None else s.price      # the slot's own price: if it happens, it's cheap
    return (car_slot(s, p) and p.fill_when_cheap and price is not None
            and price * 100 <= p.cheap_cap_p)


def slot_target(s: Slot, p: Params, final: bool = False) -> float:
    """Grid-charge target for a slot. With arbitrage on, grid charging stops at the top of the arbitrage band
    (the full zone wears the battery), except for the final top-up at the end of the fixed overnight window
    (`final`: the battery goes into the morning full) and free-power sessions. Alongside a cheap car charge outside
    the overnight window: the top-up level. Otherwise grid_target (100%)."""
    if car_cheap_charge(s, p) and not s.overnight:
        return p.buffer_target
    if p.arbitrage and not final and not s.free:
        return min(grid_target(p), p.arbitrage_max_soc)
    return grid_target(p)


def _target(s: Slot, p: Params, final: bool) -> float:
    """slot_target, except that an override's charge heads for the charge target, as the controller does."""
    return p.target_soc if s.manual == GRID_CHARGE else slot_target(s, p, final)


def final_topup(slots: list[Slot], p: Params) -> list[bool]:
    """The last half-hours of each fixed overnight window, long enough to charge from the top of the arbitrage band
    to the grid-charge target (plus one to spare): the only time grid charging goes above the band."""
    ends = window_ends(slots)
    out = [False] * len(slots)
    gap_kwh = max(0.0, p.target_soc - p.arbitrage_max_soc) / 100 * p.capacity_kwh
    for t, end in enumerate(ends):
        if not end:
            continue
        rate = max(0.1, p.max_charge_kw * 0.5 * p.efficiency * (slots[t].charge_factor or 1.0))
        k = int(-(-gap_kwh // rate)) + 1
        for i in range(t, max(-1, t - k), -1):
            if not slots[i].overnight:
                break
            out[i] = True
    return out


def sell_floor(s: Slot, p: Params) -> float:
    """How low a sale may take the battery. Inside the fixed overnight window the refill is guaranteed, so down to
    the reserve plus a margin; anywhere else the refill may depend on optional smart-charge slots that EDF can
    withdraw, so selling stops at the arbitrage band's bottom (a hard limit there, for safety)."""
    floor = p.min_reserve_soc + p.arbitrage_keep_soc
    return floor if s.overnight and p.deep_overnight else max(floor, p.arbitrage_min_soc)


def band_penalty(a: str, lv: float, end: float, p: Params, overnight: bool = False) -> float:
    """GBP for the part of an arbitrage move outside the band: selling below its bottom (not inside the fixed
    overnight window, where the refill is guaranteed: one deeper sale and one refill beat many shallow cycles), or
    grid-charging above its top."""
    if not p.arbitrage or not p.arbitrage_band_penalty_p:
        return 0.0
    kwh = 0.0
    if a == EXPORT and end < lv and overnight and p.deep_overnight:
        return 0.0
    if a == EXPORT and end < lv:
        kwh = max(0.0, min(lv, p.arbitrage_min_soc) - end) / 100 * p.capacity_kwh
    elif a == GRID_CHARGE and end > lv:
        kwh = max(0.0, end - max(lv, p.arbitrage_max_soc)) / 100 * p.capacity_kwh
    return kwh * p.arbitrage_band_penalty_p / 100


def _tiered(acts: list[str], s: Slot, p: Params) -> list[str]:
    """The actions a low-write plan tier allows (docs/plans/low-write-mode.md): 0 is plain self-use, 1 charges only
    inside the fixed overnight window, 2 also sells inside it, 3 charges and holds anywhere, 4 (the default) is the
    whole plan."""
    t = p.plan_tier
    if t >= 4:
        return acts
    if t <= 0:
        return [SELF_USE]
    keep = []
    for a in acts:
        charge_ok = t >= 3 or s.overnight
        sell_ok = t >= 2 and s.overnight
        if a == SELF_USE or (a in (GRID_CHARGE, HOLD) and charge_ok) or (a == EXPORT and sell_ok):
            keep.append(a)
    return keep or [SELF_USE]


def _actions(s: Slot, p: Params) -> list[str]:
    if p.axle_enabled and s.axle:
        return [FORCE_DISCHARGE]
    if s.manual:
        return [s.manual]                          # the owner's override fixes this half-hour
    if p.free_enabled and s.free:
        return [GRID_CHARGE]
    return _tiered(_untiered_actions(s, p), s, p)


def _untiered_actions(s: Slot, p: Params) -> list[str]:
    if car_slot(s, p):
        if car_cheap_charge(s, p):
            return [GRID_CHARGE]                   # the car charges cheaply: so does the battery (to the top-up level)
        return [HOLD, GRID_CHARGE]                 # the car is in the house load: the battery mustn't feed it
    acts = [SELF_USE, HOLD, GRID_CHARGE]
    if p.arbitrage:
        acts.append(EXPORT)
    return acts


NONE_K, HOLD_K, CHARGE_K, DISCHARGE_K = 0, 1, 2, 3
KIND = {SELF_USE: NONE_K, HOLD: HOLD_K, GRID_CHARGE: CHARGE_K, EXPORT: DISCHARGE_K, FORCE_DISCHARGE: DISCHARGE_K}
HIGH_DWELL = 1.5e-3                   # GBP/kWh above the band per half-hour there (arbitrage on): fill it last
EARLY_BIAS = 5e-4                     # GBP per kWh per half-hour of delay, in the fixed overnight window only: charge
                                      # early there (same price all night) rather than leave it all to the last hours
SELL_BIAS = 5e-4                      # GBP per kWh per half-hour of delay, for a sale: banked sooner is surer (a
                                      # dispatch can be withdrawn, a forecast revised), and a plan that waits has
                                      # nothing over one that sells now, so a tie goes to selling early
FULL_PENALTY = 1.0                    # GBP per kWh short of the target at the end of the fixed overnight window


def switch_cost(prev: int, new: int, p: Params, overnight: bool = False) -> float:
    """GBP for changing what the inverter's timed windows do (EEPROM writes): a full switch between self-use,
    charging and discharging costs the setting; hold <-> charge only changes the current (a fifth of it).

    Inside the fixed overnight window with deeper selling on, a full switch costs at least the overnight switch
    cost: the refill is guaranteed there, so one deep sale earns about the same as several shallow cycles, and the
    fewer switches win (28 Sep 2026: two cycles overnight where one would do)."""
    if prev == new:
        return 0.0
    base = p.switch_cost_p
    if overnight and p.deep_overnight and p.arbitrage:
        base = max(base, p.overnight_switch_cost_p)
    if not base:
        return 0.0
    if {prev, new} == {HOLD_K, CHARGE_K}:
        return p.switch_cost_p / 500
    return base / 100


def window_ends(slots: list[Slot]) -> list[bool]:
    """True for the last half-hour of each run of the fixed overnight window."""
    return [s.overnight and (t + 1 == len(slots) or not slots[t + 1].overnight) for t, s in enumerate(slots)]


MID_SLOT_STICK = 0.15                 # GBP: changing the running half-hour's action part-way through (a replan
                                      # mid-slot): only for a clear gain, not a near-tie (changes cost writes)


def optimise(slots: list[Slot], soc: float, p: Params, wear: float = 0.0, prev_action: str | None = None,
             stick: float = 0.0, first_h: float | None = None) -> dict | None:
    """`first_h`: hours of the first slot still to run (None: a whole half-hour): it is simulated for that part
    only, so feasibility, the sale floor, energy, cost and end charge are those of the rest of the half-hour.

    `wear`: GBP per kWh taken out of the battery, counted in the choice (not in the cash cost returned).

    Also counted in the choice: the arbitrage band's outside-band cost (not inside the fixed overnight window),
    a cost per switch of the inverter's timed windows, and, with top-up when cheap on, a steep cost for ending the
    fixed overnight window below the grid-charge target (the battery is full when the cheap window closes)."""
    if not slots:
        return None
    prices = [s.price for s in slots if s.price is not None]
    end_value = min(prices) if prices else 0.0
    cap = p.capacity_kwh
    T = len(slots)
    hours = [first_h if (t == 0 and first_h is not None and first_h < DT_H) else DT_H for t in range(T)]
    K = 4
    ends = window_ends(slots)
    final = final_topup(slots, p)
    # value[t][level][k] = lowest cost from half-hour t onwards, at that level, the previous half-hour's kind k
    value = [[[0.0] * K for _ in range(LEVELS)] for _ in range(T + 1)]
    for lv in range(LEVELS):
        value[T][lv] = [-(lv / 100 * cap) * end_value] * K
    choice = [[[SELF_USE] * K for _ in range(LEVELS)] for _ in range(T)]
    for t in range(T - 1, -1, -1):
        s, nxt = slots[t], value[t + 1]
        acts = _actions(s, p)
        row, pick = value[t], choice[t]
        for lv in range(LEVELS):
            options = []
            for a in acts:
                ps = PlanSlot(s, a, "", target_soc=_target(s, p, final[t]), hours=hours[t])
                end = step(ps, float(lv), p)
                if a == EXPORT and not s.manual and end < sell_floor(s, p) - 1e-6:
                    continue                           # below the band only where the refill is guaranteed
                k = KIND[a]
                total = ps.cost + nxt[min(LEVELS - 1, max(0, round(end)))][k]
                if t == 0 and stick and prev_action and a != prev_action:
                    total += stick                     # mid-slot: keep what the inverter is already doing
                if wear and end < lv:
                    total += (lv - end) / 100 * cap * wear
                total += band_penalty(a, float(lv), end, p, s.overnight)
                if p.arbitrage and end > p.arbitrage_max_soc:
                    # sitting above the band costs a little (wear): sell or use the top first, fill it last
                    total += (end - p.arbitrage_max_soc) / 100 * cap * HIGH_DWELL
                if ends[t] and p.fill_when_cheap and end < p.target_soc:
                    total += (p.target_soc - end) / 100 * cap * FULL_PENALTY
                if a == EXPORT and end < lv:
                    total += (lv - end) / 100 * cap * SELL_BIAS * t     # a tie sells now, not later
                if a == GRID_CHARGE and end > lv and s.overnight:
                    total += (end - lv) / 100 * cap * EARLY_BIAS * t     # same price all night: charge sooner
                options.append((total, a, k))
            for kp in range(K):
                best, best_a = None, SELF_USE
                for total, a, k in options:
                    v = total + switch_cost(kp, k, p, s.overnight)
                    if best is None or v < best - 1e-9:
                        best, best_a = v, a
                row[lv][kp], pick[lv][kp] = best, best_a
    # replay from the real starting charge with exact (not rounded) physics
    actions, socs, cost, lvl = [], [], 0.0, soc
    kp = KIND.get(prev_action, NONE_K) if prev_action else NONE_K
    for t, s in enumerate(slots):
        a = choice[t][min(LEVELS - 1, max(0, round(lvl)))][kp]
        ps = PlanSlot(s, a, "", target_soc=_target(s, p, final[t]), hours=hours[t])
        lvl = step(ps, lvl, p)
        actions.append(a)
        socs.append(round(lvl, 1))
        cost += ps.cost
        kp = KIND[a]
    net = cost - lvl / 100 * cap * end_value
    return {"cost": round(cost, 2), "net": round(net, 2), "end_soc": round(lvl, 1), "actions": actions, "soc": socs,
            "end_value_p": round(end_value * 100, 2),
            "switches": sum(1 for a, b in zip(actions, actions[1:], strict=False) if KIND[a] != KIND[b])}


def compare(plan, opt: dict | None, p: Params) -> dict | None:
    """Heuristic plan vs optimiser on the same terms (cash cost less the value of charge left at the end)."""
    if opt is None or not plan.slots:
        return None
    end_soc = plan.slots[-1].soc_end
    heur_net = plan.cost - end_soc / 100 * p.capacity_kwh * opt["end_value_p"] / 100
    differ = sum(1 for ps, a in zip(plan.slots, opt["actions"], strict=True) if ps.action != a)
    return {"optimiser_net": opt["net"], "planner_net": round(heur_net, 2),
            "better_by": round(heur_net - opt["net"], 2), "half_hours_differ": differ, "soc": opt["soc"]}
