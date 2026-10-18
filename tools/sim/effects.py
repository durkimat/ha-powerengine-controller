# ruff: noqa: E501  (prose)
"""Does each setting do what it says? Every user setting of engine v2 is run at a few values over stored days, one at a
time from the archived config, and its own measure must move the way its words promise (more of a range's bottom: fewer
hours below it; a dearer mode change: fewer changes; and so on). Runs on this machine through the simulator (cached
runs are reused), never on CI: about 40 runs a day of data.

    python3 tools/sim/effects.py --days 2026-10-08,2026-10-11,2026-10-13,2026-10-15 [--code REF|DIR] [--jobs N] [--out DIR]

A check passes when the measure never moves against the promise (within a small tolerance) and moves by at least its
minimum from the first value to the last; WEAK when it is the right way but smaller than that; FAIL when it moves the
wrong way. The money (adjusted cost) and the energy the battery moved are printed beside each, to see what each
setting costs. Exit code 1 when any check fails.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import runner  # noqa: E402
import workspace  # noqa: E402

STEP_H = 5 / 60          # the trace's points are 5 minutes apart


# --- measures, per run (a day), summed over the days -------------------------------------------------------------
def _trace(r):
    return r["series"]["trace"]                     # [time, level %, mode, charge W, discharge W]


def hours_below(x):
    return lambda r: sum(STEP_H for _t, lvl, *_ in _trace(r) if lvl < x)


def hours_above(x):
    return lambda r: sum(STEP_H for _t, lvl, *_ in _trace(r) if lvl > x)


def hours_outside(lo, hi):
    return lambda r: sum(STEP_H for _t, lvl, *_ in _trace(r) if lvl < lo or lvl > hi)


def hours_in(mode):
    return lambda r: sum(STEP_H for _t, _l, m, *_ in _trace(r) if m == mode)


def hours_in_without_car(mode):
    """Hours in `mode` in the half-hours the car was not charging (a charging car holds the battery whatever is set)."""
    def fn(r):
        car = {row["start"][11:16]: (row.get("car") or 0) > 0 for row in r["series"]["rows"]}
        out = 0.0
        for t, _l, m, *_ in _trace(r):
            hh, mm = t.split(":")[:2]
            slot = f"{hh}:{'30' if int(mm) >= 30 else '00'}"
            if m == mode and not car.get(slot, False):
                out += STEP_H
        return out
    return fn


def discharged_kwh(r):
    return sum(row[4] for row in _trace(r) if len(row) > 4) / 1000 * STEP_H


def score(key):
    return lambda r: r["score"][key]


MEASURES = {
    "modes": ("mode changes", score("mode_changes")),
    "exported": ("kWh exported", score("export_kwh")),
    "dear_charge": ("kWh charged at the peak rate", score("peak_charge_kwh")),
    "discharged": ("kWh the battery gave out", discharged_kwh),
    "below50": ("hours below 50%", hours_below(50)),
    "below70": ("hours below 70%", hours_below(70)),
    "above90": ("hours above 90%", hours_above(90)),
    "outside70_90": ("hours outside 70 to 90%", hours_outside(70, 90)),
    "hold": ("hours in Hold, the car not charging", hours_in_without_car("hold")),
}

# setting, values in order, the measure, which way it must go as the values go ("down" / "up"), the smallest move that
# counts as an effect (first value to last), and what the setting promises (from its help text)
CHECKS = [
    ("range_low_soc", ["40", "55", "70", "80"], "below50", "down", 0.5, "below the range's bottom the plan stays out where it can"),
    ("range_high_soc", ["80", "90", "97"], "above90", "up", 0.5, "above the top only when it pays: a higher top, more time up there"),
    ("range_firmness", ["off", "gentle", "firm", "strict"], "outside70_90", "down", 1.0, "a firmer range: less time outside it"),
    ("min_run_pct", ["0", "10", "20"], "modes", "down", 3, "longer runs: fewer mode changes"),
    ("slot_floor_soc", ["0", "75", "85"], "below70", "down", 0.5, "kept above the slot floor while a slot is counted on"),
    ("slot_odds_pct", ["30", "60", "90"], "below70", "up", 0.5, "surer slots: the plan lets the battery go lower before one"),
    ("switch_cost_p", ["0", "2", "6"], "modes", "down", 3, "a dearer change: fewer changes"),
    ("wear_p", ["0", "3", "6"], "discharged", "down", 2.0, "wear counted: the battery gives out less"),
    ("charge_max_import_p", ["10", "20", "100"], "dear_charge", "up", 0.2, "a higher cap: more charging at dear prices"),
    ("hold_use", ["car and reserve", "cheap times", "any time"], "hold", "up", 0.5, "Hold allowed more widely: more time in Hold"),
    ("arbitrage", ["false", "true"], "exported", "up", 2.0, "selling allowed: more exported"),
]
TOL = 0.02               # a move against the promise smaller than this share of the range of values is noise


def verdict(vals: list[float], way: str, min_move: float) -> str:
    sign = 1 if way == "up" else -1
    spread = max(vals) - min(vals)
    slack = max(TOL * spread, 1e-9)
    against = any(sign * (b - a) < -slack for a, b in zip(vals, vals[1:], strict=False))
    move = sign * (vals[-1] - vals[0])
    if against or move < -slack:
        return "FAIL"
    return "PASS" if move >= min_move else "WEAK"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--data", default=str(Path.home() / "pe-data"))
    p.add_argument("--work", default=str(Path.home() / "pe-sim"))
    p.add_argument("--days", required=True)
    p.add_argument("--code", default=None, help="a git ref or a folder holding a checkout (default: this checkout)")
    p.add_argument("--jobs", type=int, default=max(1, (__import__("os").cpu_count() or 2) - 2))
    p.add_argument("--only", default=None, help="comma-separated settings to check (default: all)")
    p.add_argument("--out", default=None, help="write effects.md here")
    a = p.parse_args(argv)
    ws = runner.Workspace(Path(a.data), Path(a.work))
    days = runner.parse_days(a.days, workspace.days_with_snapshots(ws.data), workspace.synthetic_days(ws.extra))
    checks = [c for c in CHECKS if not a.only or c[0] in a.only.split(",")]
    variants = {(k, v): runner.Variant(f"{k}={v}", a.code, {k: v}) for k, vals, *_ in checks for v in vals}
    jobs = {key: [ws.job(v, d) for d in days] for key, v in variants.items()}
    results = runner.run_jobs(ws, [j for js in jobs.values() for j in js], a.jobs, False,
                              lambda m: print(m, file=sys.stderr, flush=True))
    lines = ["| setting | values | measure | values of the measure | adjusted cost (GBP) | battery out (kWh) | verdict |",
             "|---|---|---|---|---|---|---|"]
    failed = False
    for key, vals, measure, way, min_move, promise in checks:
        label, fn = MEASURES[measure]
        got, cost, out = [], [], []
        for v in vals:
            rs = [results[j["key"]] for j in jobs[(key, v)]]
            ok = [r for r in rs if r.get("status") == "ok"]
            if len(ok) != len(rs):
                bad = next(r for r in rs if r.get("status") != "ok")
                raise SystemExit(f"{key}={v}: {bad.get('day')} {bad.get('status')}: {bad.get('reason')}")
            got.append(sum(fn(r) for r in ok))
            cost.append(sum(r["score"]["adj_cost"] for r in ok))
            out.append(sum(discharged_kwh(r) for r in ok))
        v_ = verdict(got, way, min_move)
        failed |= v_ == "FAIL"
        fmt = lambda xs, n=1: " / ".join(f"{x:.{n}f}" for x in xs)  # noqa: E731
        lines.append(f"| `{key}` | {' / '.join(vals)} | {label} ({way}: {promise}) | {fmt(got)} | {fmt(cost, 2)} | {fmt(out, 0)} | {v_} |")
    report = f"Days: {', '.join(days)}. Code: {a.code or 'this checkout'}. Each setting alone, from the archived config.\n\n" + "\n".join(lines) + "\n"
    print(report)
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / "effects.md").write_text(report, encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
