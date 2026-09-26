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
    """How full grid charging may take the battery: 100%, or the arbitrage ceiling while arbitrage is on."""
    return p.buffer_target if p.arbitrage else 100.0


def _actions(s: Slot, p: Params) -> list[str]:
    if p.axle_enabled and s.axle:
        return [FORCE_DISCHARGE]
    if p.free_enabled and s.free:
        return [GRID_CHARGE]
    acts = [SELF_USE, HOLD, GRID_CHARGE]
    if p.arbitrage:
        acts.append(EXPORT)
    return acts


def optimise(slots: list[Slot], soc: float, p: Params, wear: float = 0.0) -> dict | None:
    """`wear`: GBP per kWh taken out of the battery, counted in the choice (not in the cash cost returned)."""
    if not slots:
        return None
    prices = [s.price for s in slots if s.price is not None]
    end_value = min(prices) if prices else 0.0
    cap = p.capacity_kwh
    T = len(slots)
    # value[t][level] = lowest cost from half-hour t onwards, starting at that level
    value = [[0.0] * LEVELS for _ in range(T + 1)]
    value[T] = [-(lv / 100 * cap) * end_value for lv in range(LEVELS)]
    choice = [[SELF_USE] * LEVELS for _ in range(T)]
    for t in range(T - 1, -1, -1):
        s, nxt = slots[t], value[t + 1]
        acts = _actions(s, p)
        row, pick = value[t], choice[t]
        for lv in range(LEVELS):
            best, best_a = None, SELF_USE
            for a in acts:
                ps = PlanSlot(s, a, "", target_soc=grid_target(p))
                end = step(ps, float(lv), p)
                total = ps.cost + nxt[min(LEVELS - 1, max(0, round(end)))]
                if wear and end < lv:
                    total += (lv - end) / 100 * cap * wear
                if best is None or total < best - 1e-9:
                    best, best_a = total, a
            row[lv], pick[lv] = best, best_a
    # replay from the real starting charge with exact (not rounded) physics
    actions, socs, cost, lvl = [], [], 0.0, soc
    for t, s in enumerate(slots):
        a = choice[t][min(LEVELS - 1, max(0, round(lvl)))]
        ps = PlanSlot(s, a, "", target_soc=grid_target(p))
        lvl = step(ps, lvl, p)
        actions.append(a)
        socs.append(round(lvl, 1))
        cost += ps.cost
    net = cost - lvl / 100 * cap * end_value
    return {"cost": round(cost, 2), "net": round(net, 2), "end_soc": round(lvl, 1), "actions": actions, "soc": socs,
            "end_value_p": round(end_value * 100, 2)}


def compare(plan, opt: dict | None, p: Params) -> dict | None:
    """Heuristic plan vs optimiser on the same terms (cash cost less the value of charge left at the end)."""
    if opt is None or not plan.slots:
        return None
    end_soc = plan.slots[-1].soc_end
    heur_net = plan.cost - end_soc / 100 * p.capacity_kwh * opt["end_value_p"] / 100
    differ = sum(1 for ps, a in zip(plan.slots, opt["actions"], strict=True) if ps.action != a)
    return {"optimiser_net": opt["net"], "planner_net": round(heur_net, 2),
            "better_by": round(heur_net - opt["net"], 2), "half_hours_differ": differ, "soc": opt["soc"]}
