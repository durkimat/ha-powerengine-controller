"""The one-screen table: per variant, per day and in total. Detail goes to files, not here."""

from __future__ import annotations

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
