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
from .planner import Params, PlanSlot, step

LEVELS = 101                                  # 0..100 %


def grid_target(p: Params) -> float:
    """How full grid charging may take the battery. Always 100%: with arbitrage on, charging above the band costs
    the band penalty instead (a guide, not a limit)."""
    return 100.0


def band_penalty(a: str, lv: float, end: float, p: Params) -> float:
    """GBP for the part of an arbitrage move outside the band: selling below its bottom, or grid-charging above
    its top."""
    if not p.arbitrage or not p.arbitrage_band_penalty_p:
        return 0.0
    kwh = 0.0
    if a == EXPORT and end < lv:
        kwh = max(0.0, min(lv, p.arbitrage_min_soc) - end) / 100 * p.capacity_kwh
    elif a == GRID_CHARGE and end > lv:
        kwh = max(0.0, end - max(lv, p.arbitrage_max_soc)) / 100 * p.capacity_kwh
    return kwh * p.arbitrage_band_penalty_p / 100


def _actions(s: Slot, p: Params) -> list[str]:
    if p.axle_enabled and s.axle:
        return [FORCE_DISCHARGE]
    if p.free_enabled and s.free:
        return [GRID_CHARGE]
    if p.hold_for_car and s.smart_slot:
        return [HOLD, GRID_CHARGE]                 # the car is in the house load: the battery mustn't feed it
    acts = [SELF_USE, HOLD, GRID_CHARGE]
    if p.arbitrage:
        acts.append(EXPORT)
    return acts


NONE_K, HOLD_K, CHARGE_K, DISCHARGE_K = 0, 1, 2, 3
KIND = {SELF_USE: NONE_K, HOLD: HOLD_K, GRID_CHARGE: CHARGE_K, EXPORT: DISCHARGE_K, FORCE_DISCHARGE: DISCHARGE_K}
FULL_PENALTY = 1.0                    # GBP per kWh short of the target at the end of the fixed overnight window


def switch_cost(prev: int, new: int, p: Params) -> float:
    """GBP for changing what the inverter's timed windows do (EEPROM writes): a full switch between self-use,
    charging and discharging costs the setting; hold <-> charge only changes the current (a fifth of it)."""
    if prev == new or not p.switch_cost_p:
        return 0.0
    if {prev, new} == {HOLD_K, CHARGE_K}:
        return p.switch_cost_p / 500
    return p.switch_cost_p / 100


def window_ends(slots: list[Slot]) -> list[bool]:
    """True for the last half-hour of each run of the fixed overnight window."""
    return [s.overnight and (t + 1 == len(slots) or not slots[t + 1].overnight) for t, s in enumerate(slots)]


def optimise(slots: list[Slot], soc: float, p: Params, wear: float = 0.0, prev_action: str | None = None
             ) -> dict | None:
    """`wear`: GBP per kWh taken out of the battery, counted in the choice (not in the cash cost returned).

    Also counted in the choice: the arbitrage band's outside-band cost (not inside the fixed overnight window),
    a cost per switch of the inverter's timed windows, and, with top-up when cheap on, a steep cost for ending the
    fixed overnight window below the grid-charge target (the battery is full when the cheap window closes)."""
    if not slots:
        return None
    prices = [s.price for s in slots if s.price is not None]
    end_value = min(prices) if prices else 0.0
    cap = p.capacity_kwh
    T = len(slots)
    K = 4
    ends = window_ends(slots)
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
                ps = PlanSlot(s, a, "", target_soc=grid_target(p))
                end = step(ps, float(lv), p)
                if a == EXPORT and end < p.min_reserve_soc + p.arbitrage_keep_soc - 1e-6:
                    continue                           # a sale never takes the battery near the reserve
                k = KIND[a]
                total = ps.cost + nxt[min(LEVELS - 1, max(0, round(end)))][k]
                if wear and end < lv:
                    total += (lv - end) / 100 * cap * wear
                if not s.overnight:
                    total += band_penalty(a, float(lv), end, p)
                if ends[t] and p.fill_when_cheap and end < p.target_soc:
                    total += (p.target_soc - end) / 100 * cap * FULL_PENALTY
                options.append((total, a, k))
            for kp in range(K):
                best, best_a = None, SELF_USE
                for total, a, k in options:
                    v = total + switch_cost(kp, k, p)
                    if best is None or v < best - 1e-9:
                        best, best_a = v, a
                row[lv][kp], pick[lv][kp] = best, best_a
    # replay from the real starting charge with exact (not rounded) physics
    actions, socs, cost, lvl = [], [], 0.0, soc
    kp = KIND.get(prev_action, NONE_K) if prev_action else NONE_K
    for t, s in enumerate(slots):
        a = choice[t][min(LEVELS - 1, max(0, round(lvl)))][kp]
        ps = PlanSlot(s, a, "", target_soc=grid_target(p))
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
