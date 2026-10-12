"""The one-screen table: per variant, per day and in total. Detail goes to files, not here."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

COLS = [
    ("day", 10),
    ("cost", 7),
    ("v self", 7),
    ("to bound", 8),
    ("adj", 7),
    ("modes", 5),
    ("flips", 5),
    ("peak kWh", 8),
    ("min %", 5),
    ("cmds", 4),
    ("calc s", 6),
    ("wall s", 6),
]


def _g(x, places=2, signed=False):
    if x is None:
        return "-"
    return f"{x:+.{places}f}" if signed else f"{x:.{places}f}"


def row(day: str, s: dict) -> list[str]:
    return [
        day,
        _g(s["cost"]),
        _g(s["save_vs_selfuse"], 2, True),
        _g(s["gap_to_bound"]),
        _g(s["adj_cost"]),
        str(s["mode_changes"]),
        str(s["flip_flops"]),
        _g(s["peak_charge_kwh"]),
        _g(s["min_soc"], 0),
        str(s["commands"]),
        _g(s["calc_s"], 0),
        _g(s["wall_s"], 0),
    ]


def total(scores: list[dict]) -> dict:
    t = {
        k: sum((s[k] or 0) for s in scores)
        for k in (
            "cost",
            "adj_cost",
            "save_vs_selfuse",
            "gap_to_bound",
            "mode_changes",
            "flip_flops",
            "peak_charge_kwh",
            "commands",
            "calc_s",
            "wall_s",
        )
    }
    t["min_soc"] = min((s["min_soc"] for s in scores if s["min_soc"] is not None), default=None)
    return t


def table(title: str, results: list[dict]) -> str:
    """`results`: the ok results of one variant, in day order."""
    lines = [title, "  ".join(h.rjust(w) for h, w in COLS)]
    ok = [r for r in results if r.get("status") == "ok"]
    for r in results:
        if r.get("status") != "ok":
            lines.append(f"{r.get('day', '?'):>10}  not run: {r.get('status')}: {r.get('reason', '')[:90]}")
            continue
        lines.append("  ".join(c.rjust(w) for c, (_, w) in zip(row(r["day"], r["score"]), COLS, strict=True)))
    if len(ok) > 1:
        lines.append(
            "  ".join(c.rjust(w) for c, (_, w) in zip(row("TOTAL", total([r["score"] for r in ok])), COLS, strict=True))
        )
    return "\n".join(lines)


def versus(base: list[dict], other: list[dict], name: str) -> str:
    """Total cost, flip-flops and modes of `other` against `base`, over the days both ran."""
    b = {r["day"]: r["score"] for r in base if r.get("status") == "ok"}
    o = {r["day"]: r["score"] for r in other if r.get("status") == "ok"}
    days = sorted(set(b) & set(o))
    if not days:
        return f"{name}: no days in common with the baseline"
    d_adj = sum(o[d]["adj_cost"] - b[d]["adj_cost"] for d in days)
    worst = max(days, key=lambda d: o[d]["adj_cost"] - b[d]["adj_cost"])
    d_flip = sum(o[d]["flip_flops"] - b[d]["flip_flops"] for d in days)
    d_mode = sum(o[d]["mode_changes"] - b[d]["mode_changes"] for d in days)
    return (
        f"{name} vs base over {len(days)} day(s): adj cost {d_adj:+.2f} GBP (worst day {worst} "
        f"{o[worst]['adj_cost'] - b[worst]['adj_cost']:+.2f}), mode changes {d_mode:+d}, flip-flops {d_flip:+d}"
    )


def ranking(results: dict[str, list[dict]], base: str) -> str:
    """Every variant against the baseline over the days both ran, cheapest first: what each change was worth."""
    b = {r["day"]: r["score"] for r in results[base] if r.get("status") == "ok"}
    rows = []
    for name, res in results.items():
        if name == base:
            continue
        o = {r["day"]: r["score"] for r in res if r.get("status") == "ok"}
        days = sorted(set(b) & set(o))
        if not days:
            rows.append((0.0, [name, "-", "-", "-", "-", "-", "-", "not run"]))
            continue

        def d(key, days=days, o=o):
            return sum((o[x][key] or 0) - (b[x][key] or 0) for x in days)

        per_day = [o[x]["adj_cost"] - b[x]["adj_cost"] for x in days]
        rows.append(
            (
                d("adj_cost"),
                [
                    name,
                    f"{d('adj_cost'):+.2f}",
                    f"{max(per_day):+.2f}",
                    f"{d('mode_changes'):+.0f}",
                    f"{d('flip_flops'):+.0f}",
                    f"{d('peak_charge_kwh'):+.2f}",
                    f"{min(o[x]['min_soc'] for x in days):.0f}",
                    str(len(days)),
                ],
            )
        )
    head = ["variant", "d adj GBP", "worst day", "d modes", "d flips", "d peak kWh", "min %", "days"]
    rows.sort(key=lambda r: r[0])
    width = [max(len(str(r[1][i])) for r in rows + [(0, head)]) for i in range(len(head))]

    def fmt(cells):
        return "  ".join(
            str(c).rjust(w) if i else str(c).ljust(w) for i, (c, w) in enumerate(zip(cells, width, strict=True))
        )

    return "\n".join([f"Against {base} (negative d adj is cheaper):", fmt(head), *[fmt(r[1]) for r in rows]])


# --- how well the run followed its plan -------------------------------------------
# Worked out from a run's stored series (changes, 5-minute trace, each re-plan's timeline), so cached runs score too.
# The trace is 5-minute, so levels at the end of a run, and minutes, are good to about 5 minutes / a point.

REACHED_PT = 1.0  # a run that ended within this many points of its target reached it
RUN_MODES = ("charge", "discharge")
CUT_MIN = 5  # a run that ended this long before its plan did was cut short (a re-plan)

METRIC_COLS = [
    ("day", 10),
    ("holds", 5),
    ("hold@tgt min", 12),
    ("wait min", 8),
    ("car min", 8),
    ("idle min", 8),
    ("runs", 4),
    ("cut", 4),
    ("over mean", 9),
    ("over max", 8),
    ("under mean", 10),
    ("under max", 9),
    ("lvl err mean", 12),
    ("lvl err max", 11),
]


def _at(day: str, hms: str, tz: ZoneInfo) -> datetime:
    parts = [int(x) for x in hms.split(":")]
    h, m, sec = (parts + [0, 0])[:3]
    return datetime.fromisoformat(day).replace(hour=h, minute=m, second=sec, tzinfo=tz)


def _plan_in_force(plans: list[tuple[datetime, dict]], t: datetime):
    cur = None
    for at, p in plans:
        if at <= t:
            cur = p
        else:
            break
    return cur


def _planned_level(plan: dict, t: datetime):
    for _mode, a, b, l0, l1, *_ in plan["timeline"]:
        a, b = datetime.fromisoformat(a), datetime.fromisoformat(b)
        if a <= t < b:
            f = (t - a) / (b - a) if b > a else 0.0
            return l0 + (l1 - l0) * f
    return None


def _target(plan: dict, t: datetime, mode: str):
    """What the plan in force said about a `mode` run at `t`: (corridor low, corridor high, time) at the end of the
    consecutive items in that direction (charge or export) from the one that covers `t`; None when `t` is not in
    such an item. A plan without corridors (older code) has a point: low and high are the item's end level."""
    want = "charge" if mode == "charge" else "export"
    end = last_b = None
    for i in plan["timeline"]:
        a, b = datetime.fromisoformat(i[1]), datetime.fromisoformat(i[2])
        lo = i[7] if len(i) > 7 and i[7] is not None else i[4]
        hi = i[8] if len(i) > 8 and i[8] is not None else i[4]
        if end is None:
            if a <= t < b and i[0] == want:
                end, last_b = (lo, hi), b
        elif a == last_b and i[0] == want:
            end, last_b = (lo, hi), b
        else:
            break
    return None if end is None else (*end, last_b)


def _rd(x):
    return None if x is None else round(x, 2)


def _mean(x):
    return sum(x) / len(x) if x else None


def run_metrics(result: dict) -> dict | None:
    """Per day: holds, minutes in Hold at a reached target, target overshoot/undershoot per run that ran to its
    planned end (points; overshoot is going past the target in the run's direction, undershoot stopping short of
    it), runs cut short before it (`cut_early`, not scored) and plan-vs-actual level error."""
    if result.get("status") != "ok":
        return None
    s, day = result["series"], result["day"]
    tz = ZoneInfo(s["tz"])
    plans = sorted(((datetime.fromisoformat(p["at"]), p) for p in s["plans"]), key=lambda x: x[0])
    trace = [(_at(day, t, tz), lvl) for t, lvl, *_ in s["trace"]]
    changes = [(_at(day, t, tz), k) for t, k in s["changes"]]
    end_of_day = _at(day, "00:00", tz) + timedelta(days=1)

    def level_at(t):
        for tt, lvl in trace:
            if tt >= t:
                return lvl
        return trace[-1][1] if trace else None

    rows = sorted((datetime.fromisoformat(r["start"]), (r["act"], r["exp"], bool(r.get("axle")), bool(r.get("free"))))
                  for r in s.get("rows", []))

    def prices_over(a, b):
        """The distinct (import, export, event, free) in force at some time in [a, b]."""
        seen, cur = set(), None
        for st, v in rows:
            if st <= a:
                cur = v
            elif st <= b:
                if cur is not None:
                    seen.add(cur)
                cur = v
        if cur is not None:
            seen.add(cur)
        return seen

    cars = [(datetime.fromisoformat(r["start"]), r.get("car") or 0.0) for r in s.get("rows", [])]

    def car_on(a, b):
        return any(kwh > 0.05 and st < b and st + timedelta(minutes=30) > a for st, kwh in cars)

    at_target: list = []  # (start, minutes, mode before, mode after, level) of each hold at a reached target

    def key_after(n):
        return changes[n + 1][1] if n + 1 < len(changes) else "end"

    over, under, inside, hold_min, wait_min, car_min, holds, cut = [], [], [], 0.0, 0.0, 0.0, 0, 0
    for n, (t, key) in enumerate(changes):
        nxt = changes[n + 1][0] if n + 1 < len(changes) else end_of_day
        if key == "hold":
            holds += 1
            prev_t, prev = changes[n - 1] if n else (None, None)
            plan = _plan_in_force(plans, prev_t) if n else None
            tgt = _target(plan, prev_t, prev) if plan and prev in RUN_MODES else None
            lvl = level_at(t)
            if tgt is not None and lvl is not None and tgt[0] - REACHED_PT <= lvl <= tgt[1] + REACHED_PT:
                mins = (nxt - t).total_seconds() / 60
                hold_min += mins
                at_target.append((t.strftime("%H:%M"), round(mins), prev, key_after(n), round(lvl, 1)))
                if len(prices_over(t, nxt)) > 1:
                    wait_min += mins  # a price (or event) changed during or at the end of it: it waited for that
                elif car_on(t, nxt):
                    car_min += mins  # the car was charging: the battery is held for it, whatever the plan's prices
        if key in RUN_MODES:
            probe = max(t, nxt - timedelta(seconds=60))  # the plan that governed the end of the run
            plan = _plan_in_force(plans, probe)
            tgt = _target(plan, probe, key) if plan else None
            lvl = level_at(nxt)
            if tgt is None or lvl is None:
                continue
            lo, hi, item_end = tgt
            if nxt < item_end - timedelta(minutes=CUT_MIN):  # cut short: no target was missed, so not scored
                cut += 1
                continue
            past = (lvl - hi) if key == "charge" else (lo - lvl)  # positive: went past the corridor
            short = (lo - lvl) if key == "charge" else (lvl - hi)  # positive: stopped short of it
            (over if past > 0 else under if short > 0 else inside).append(max(past, short, 0.0))
    errs = []
    for t, lvl in trace:
        plan = _plan_in_force(plans, t)
        pl = _planned_level(plan, t) if plan else None
        if pl is not None:
            errs.append(abs(lvl - pl))
    return {
        "holds": holds,
        "hold_at_target_min": round(hold_min, 1),
        "hold_wait_min": round(wait_min, 1),
        "hold_car_min": round(car_min, 1),
        "hold_idle_min": round(hold_min - wait_min - car_min, 1),
        "runs": len(over) + len(under) + len(inside),
        "cut_early": cut,
        "overshoot_mean": _rd(_mean(over)),
        "overshoot_max": _rd(max(over, default=None)),
        "undershoot_mean": _rd(_mean(under)),
        "undershoot_max": _rd(max(under, default=None)),
        "level_err_mean": _rd(_mean(errs)),
        "level_err_max": _rd(max(errs, default=None)),
        "_at_target": at_target,
        "_over": over,
        "_under": under,
        "_errs": errs,
    }


def metrics_row(day: str, m: dict) -> list[str]:
    return [
        day,
        str(m["holds"]),
        _g(m["hold_at_target_min"], 0),
        _g(m["hold_wait_min"], 0),
        _g(m["hold_car_min"], 0),
        _g(m["hold_idle_min"], 0),
        str(m["runs"]),
        str(m["cut_early"]),
        _g(m["overshoot_mean"]),
        _g(m["overshoot_max"]),
        _g(m["undershoot_mean"]),
        _g(m["undershoot_max"]),
        _g(m["level_err_mean"]),
        _g(m["level_err_max"]),
    ]


def metrics_total(ms: list[dict]) -> dict:
    over = [x for m in ms for x in m["_over"]]
    under = [x for m in ms for x in m["_under"]]
    errs = [x for m in ms for x in m["_errs"]]
    runs = sum(m["runs"] for m in ms)
    return {
        "holds": sum(m["holds"] for m in ms),
        "hold_at_target_min": round(sum(m["hold_at_target_min"] for m in ms), 1),
        "hold_wait_min": round(sum(m["hold_wait_min"] for m in ms), 1),
        "hold_car_min": round(sum(m["hold_car_min"] for m in ms), 1),
        "hold_idle_min": round(sum(m["hold_idle_min"] for m in ms), 1),
        "runs": runs,
        "cut_early": sum(m["cut_early"] for m in ms),
        "overshoot_mean": _rd(_mean(over)),
        "overshoot_max": _rd(max(over, default=None)),
        "undershoot_mean": _rd(_mean(under)),
        "undershoot_max": _rd(max(under, default=None)),
        "level_err_mean": _rd(_mean(errs)),
        "level_err_max": _rd(max(errs, default=None)),
    }


def metrics_table(results: list[dict]) -> str:
    """How closely the run followed its plan, per day and in total."""
    lines = ["plan following (points of battery level)", "  ".join(h.rjust(w) for h, w in METRIC_COLS)]
    ms = []
    for r in results:
        m = run_metrics(r)
        if m is None:
            continue
        ms.append(m)
        lines.append("  ".join(c.rjust(w) for c, (_, w) in zip(metrics_row(r["day"], m), METRIC_COLS, strict=True)))
    if len(ms) > 1:
        total_row = metrics_row("TOTAL", metrics_total(ms))
        lines.append("  ".join(c.rjust(w) for c, (_, w) in zip(total_row, METRIC_COLS, strict=True)))
    return "\n".join(lines)
