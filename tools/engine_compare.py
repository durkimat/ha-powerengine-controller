#!/usr/bin/env python3
"""Engine v1 against engine v2 on the demo pack's recorded days, closed loop (engine v2 build, work package E).

For each day the WHOLE app runs in demo mode (a fake AppDaemon and a fake clock; the demo world plays the recorded
house, sun, prices, smart slots and grid events and simulates the battery), Active, RAM remote control, from the same
start, once with engine v1 and once with engine v2. The app's own run_every callbacks (the 30 s cycle, v2's 10 s tick,
the 5 min input check, the heartbeat) are called at their intervals, and the world is stepped every 10 s, so both
engines are judged on the same outside world. Each run is costed from the world's own flows:

    grid import x the import rate in force (smart-slot rates included)
    - grid export x the export rate
    - battery energy exported in a grid event x the event pay (axle_value + export rate, the same for both engines)

Also: plain self-use (the world with no commands), and a perfect-foresight bound: engine v2's value.solve on the
actual day (recorded house, sun and prices as the forecast with no spread, slots as they happened, the car held
against the battery as the rules say) and its forward run.

The end battery level is valued at the day's cheapest import rate (a run that ends fuller is not penalised): "endval"
is (end level - self-use's end level) x capacity x that rate, and the "adjusted" figures add it to the cash ones.

    tools/engine_compare.py [--days sunny dull axle car] [--hours 24] [--start HH:MM] [--json]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (ROOT / "apps" / "powerengine", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from pe_core.compare import replay  # noqa: E402
from pe_core.compare.harness import Clock, FrozenDatetime, loaded_app  # noqa: E402,F401
from pe_core.compare.replay import (  # noqa: E402,F401
    FLIP_WINDOW,
    PEAK_P,
    RUN_NAMES,
    TICK_S,
    Account,
    Scenario,
    Tracker,
    mode_key,
)
from pe_core.demo.pack import load_pack  # noqa: E402

BST = timezone(timedelta(hours=1))
DAY = date(2026, 10, 6)                    # a BST day; every pack day is played on it
DAYS = ("sunny", "dull", "axle", "car")


# --- the app, the world and the costing live in the app package (pe_core/compare): the nightly comparison uses them ---

def scenario(day: str, start: datetime, hours: float, backfill: bool = True) -> Scenario:
    return Scenario(pack=load_pack(), day=day, tz=BST, start=start, hours=hours, backfill=backfill)


def run_app(day: str, engine: str, start: datetime, hours: float, backfill: bool = True, on_tick=None) -> dict:
    return replay.run_app(scenario(day, start, hours, backfill), engine, on_tick)


def run_selfuse(day: str, start: datetime, hours: float, event_pay: tuple[float, bool]) -> dict:
    """Plain self-use: the demo world with no commands at all."""
    return replay.run_selfuse(scenario(day, start, hours), event_pay)


def perfect_bound(day: str, start: datetime, hours: float, facts, settings) -> dict:
    return replay.perfect_bound(scenario(day, start, hours), facts, settings)


# --- a day, the table -------------------------------------------------------------------------------------------------

def compare_day(day: str, start: datetime, hours: float, say=lambda *a: None, backfill: bool = True) -> dict:
    return replay.compare_day(scenario(day, start, hours, backfill), say)


COLUMNS = (("day", 6), ("run", 8), ("cost", 8), ("save", 7), ("end%", 6), ("endval", 7), ("adjsave", 8),
           ("cmds", 5), ("flips", 5), ("peakkWh", 8), ("min%", 6), ("gap", 7), ("revals", 6))


def table(results: list[dict]) -> str:
    def line(cells):
        pairs = enumerate(zip(cells, COLUMNS, strict=True))
        return "  ".join(str(c).rjust(w) if i > 1 else str(c).ljust(w) for i, (c, (_, w)) in pairs)

    def f(x, n=2, sign=False):
        return "-" if x is None else (f"{x:+.{n}f}" if sign else f"{x:.{n}f}")
    lines = [line([n for n, _ in COLUMNS])]
    totals: dict[str, dict] = {}
    for res in results:
        for name, run in res["runs"].items():
            if name == "bound":
                cmds, flips = run.get("mode_changes"), 0
            else:
                cmds, flips = run.get("commands"), run.get("flip_flops")
            lines.append(line([res["day"] if name == "selfuse" else "", name, f(run["cost_gbp"]),
                               f(run["save_gbp"], 2, True), f(run["end_soc"], 1), f(run["endval_gbp"], 2, True),
                               f(run["adj_save_gbp"], 2, True),
                               "-" if cmds is None else cmds, "-" if name in ("bound", "selfuse") else flips,
                               f(run.get("peak_charge_kwh"), 1), f(run.get("min_soc"), 1),
                               f(run["gap_to_bound_gbp"], 2, True), run.get("revalues", "-")]))
            t = totals.setdefault(name, {"cost": 0.0, "save": 0.0, "endval": 0.0, "adj": 0.0, "cmds": 0, "flips": 0,
                                         "peak": 0.0, "gap": 0.0, "rev": 0})
            t["cost"] += run["cost_gbp"]
            t["save"] += run["save_gbp"]
            t["endval"] += run["endval_gbp"]
            t["adj"] += run["adj_save_gbp"]
            t["cmds"] += cmds or 0
            t["flips"] += flips or 0
            t["peak"] += run.get("peak_charge_kwh") or 0.0
            t["gap"] += run["gap_to_bound_gbp"]
            t["rev"] += run.get("revalues", 0)
    if len(results) > 1:
        lines.append("")
        for i, (name, t) in enumerate(totals.items()):
            lines.append(line(["TOTAL" if i == 0 else "", name, f(t["cost"]), f(t["save"], 2, True), "",
                               f(t["endval"], 2, True), f(t["adj"], 2, True), t["cmds"],
                               "-" if name in ("bound", "selfuse") else t["flips"], f(t["peak"], 1), "",
                               f(t["gap"], 2, True), t["rev"] or "-"]))
    lines += ["", "cost: GBP from the world's flows (no standing charge); save: against self-use; endval: end level "
              "against self-use's, at the day's cheapest import rate;",
              "adjsave: save + endval; cmds: RAM command changes sent (bound: mode changes of its run); flips: A->B->A "
              "within 10 min as the inverter saw it;",
              "peakkWh: grid energy put into the battery at 20p or more; gap: adjusted cost minus the bound's."]
    for res in results:
        v2 = res["runs"]["v2"]
        lines.append(f"{res['day']}: v2 revalues by cause {v2['revalue_causes']} (errors {v2['errors']})")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", nargs="+", default=list(DAYS), choices=DAYS)
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--start", default="00:00", help="local start time, HH:MM (default 00:00)")
    ap.add_argument("--no-backfill", action="store_true",
                    help="skip the app's 14-day cost backfill at start (about 30 s a run; the load profile is kept)")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args(argv)
    hh, mm = (int(x) for x in args.start.split(":"))
    start = datetime(DAY.year, DAY.month, DAY.day, hh, mm, tzinfo=BST)
    say = (lambda *a: None) if args.json else (lambda *a: print(*a, file=sys.stderr, flush=True))
    results = [compare_day(day, start, args.hours, say, not args.no_backfill) for day in args.days]
    if args.json:
        def clean(o):
            if isinstance(o, dict):
                return {k: clean(v) for k, v in o.items() if k not in ("facts", "settings")}
            if isinstance(o, (list, tuple)):
                return [clean(v) for v in o]
            return o if isinstance(o, (int, float, str, bool, type(None))) else str(o)
        print(json.dumps(clean(results), indent=1))
    else:
        print(table(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
