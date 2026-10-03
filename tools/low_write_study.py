#!/usr/bin/env python3
"""How much of PowerEngine's benefit survives when the inverter's timed windows are changed less often.

    tools/low_write_study.py [--switch-cost P [P ...]] [--days sunny dull axle car]

Runs the real optimiser over the demo pack's recorded days (apps/powerengine/demo/pack.json), at several prices per
window change (the optimiser's `switch_cost_p`), and for four tiers of what the plan is allowed to do:

    T0  plain self-use, Axle events and free-power sessions honoured (the baseline: no PowerEngine planning)
    T1  + grid charge inside the fixed overnight window only
    T2  + arbitrage inside the fixed overnight window only (sell early in it, refill before it closes)
    T3  + grid charge and car-slot holds anywhere (smart slots)
    T4  + arbitrage anywhere (selling stored energy before a cheaper refill): the full PowerEngine plan

and prints, per tier, the average saving against T0 in GBP/day and the window changes per day (a "full" change opens
or closes a charge or discharge window; a "current-only" change is hold <-> charge). See docs/plans/low-write-mode.md
for what the numbers mean and what they don't (four recorded September days: an indication, not a forecast).

Each day is planned with the next day as look-ahead at the standard tariff (the fixed overnight window only: tomorrow's
smart slots, car and events are not known yet). Standard library plus the app's own modules; no Home Assistant.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from dataclasses import replace
from datetime import date
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "apps" / "powerengine"))

import pe_core.optimiser as opt  # noqa: E402
from pe_core.decide import EXPORT, GRID_CHARGE, HOLD, SELF_USE  # noqa: E402
from pe_core.demo.pack import day_at, load_pack  # noqa: E402
from pe_core.forecast import Slot  # noqa: E402
from pe_core.planner import Params, PlanSlot, step  # noqa: E402
from pe_core.simulator import mark_overnight  # noqa: E402

TZ = ZoneInfo("Europe/London")
TIERS = {0: "T0 self-use (+events)", 1: "T1 + overnight charge", 2: "T2 + overnight arbitrage",
         3: "T3 + smart-slot holds and charges", 4: "T4 + arbitrage anywhere (full plan)"}

def _today(pack: dict, name: str, d: date) -> list[Slot]:
    out = []
    for r in day_at(pack, name, d, TZ):
        car = r["car"] or 0.0
        out.append(Slot(start=r["start"], price=r["act"], export=r["exp"], solar_kwh=r["solar"],
                        load_kwh=r["house"] + car, car_kw=car / 0.5, smart_slot=bool(r["slot"]), axle=bool(r["axle"]),
                        free=bool(r["free"])))
    return out


def _tomorrow(pack: dict, name: str, d: date) -> list[Slot]:
    return [Slot(start=r["start"], price=r["std"], export=r["exp"], solar_kwh=r["solar"], load_kwh=r["house"])
            for r in day_at(pack, name, d, TZ)]


def run_day(pack: dict, name: str, p: Params) -> dict:
    """One planned day: net cost (GBP, the energy left in the battery valued at the day's cheapest price), window
    changes and the action string (one letter per half-hour from midnight)."""
    today, nxt = _today(pack, name, date(2026, 10, 5)), _tomorrow(pack, name, date(2026, 10, 6))
    mark_overnight(today, nxt, TZ)
    soc = pack["days"][name]["soc"][0]
    res = opt.optimise(today + nxt, soc, p, wear=p.wear_p / 100, prev_action=SELF_USE)
    acts = res["actions"][:len(today)]
    lvl, cost = soc, 0.0
    for s, a in zip(today, acts, strict=True):
        ps = PlanSlot(s, a, "", target_soc=100.0)
        lvl = step(ps, lvl, p)
        cost += ps.cost
    net = cost - lvl / 100 * p.capacity_kwh * min(s.price for s in today)
    kinds = [opt.KIND[SELF_USE]] + [opt.KIND[a] for a in acts]
    changes = [(a, b) for a, b in zip(kinds, kinds[1:], strict=False) if a != b]
    current_only = sum(1 for a, b in changes if {a, b} == {opt.HOLD_K, opt.CHARGE_K})
    return {"net": net, "full": len(changes) - current_only, "current_only": current_only,
            "acts": "".join({SELF_USE: ".", HOLD: "h", GRID_CHARGE: "C", EXPORT: "X"}.get(a, "A") for a in acts)}


def study(switch_costs: list[float], day_names: list[str], pack: dict | None = None) -> dict:
    """{switch cost: {tier: {"saving": GBP/day vs T0, "full": per day, "current_only": per day, "days": {...}}}}."""
    pack = pack or load_pack()
    out = {}
    for sw in switch_costs:
        base = replace(Params(), hold_for_car=True, switch_cost_p=sw, overnight_switch_cost_p=max(3.0, sw))
        ref: dict[str, float] = {}
        out[sw] = {}
        for tier in TIERS:
            res = {n: run_day(pack, n, replace(base, plan_tier=tier, arbitrage=tier >= 2)) for n in day_names}
            if tier == 0:
                ref = {n: r["net"] for n, r in res.items()}
            k = len(day_names)
            out[sw][tier] = {"saving": sum(ref[n] - r["net"] for n, r in res.items()) / k,
                             "full": sum(r["full"] for r in res.values()) / k,
                             "current_only": sum(r["current_only"] for r in res.values()) / k, "days": res}
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--switch-cost", type=float, nargs="+", default=[2.0, 10.0, 20.0, 40.0],
                    help="the optimiser's price per window change, pence (default 2 10 20 40)")
    ap.add_argument("--days", nargs="+", default=None, help="demo pack days (default: all)")
    ap.add_argument("--timeline", action="store_true", help="also print each day's action string")
    args = ap.parse_args(argv[1:])
    pack = load_pack()
    names = args.days or list(pack["days"])
    print("legend: . self-use  C grid charge  h hold  X arbitrage sale  A event discharge (48 half-hours from 00:00)")
    for sw, tiers in study(args.switch_cost, names, pack).items():
        print(f"\nprice per window change {sw:g}p")
        for tier, row in tiers.items():
            changes = row["full"] + row["current_only"]
            print(f"  {TIERS[tier]:36s} saves GBP {row['saving']:5.2f}/day vs T0, window changes/day {changes:4.1f} "
                  f"(full {row['full']:.1f}, current-only {row['current_only']:.1f})")
            if args.timeline:
                for n, r in row["days"].items():
                    print(f"      {n:6s} {r['acts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
